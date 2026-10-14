import hashlib
import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import ClassVar, Literal
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field

from rag_mcp.settings import RagSettings

_HTTP_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
# Depth of the raw dense pool behind one-passage-per-note search (set A: the needed note is
# within the first 100 distinct notes of a 500-deep pool for 39/46 questions).
NOTE_POOL = 500
_CHUNK_SUFFIX = re.compile(r"#c\d+$")


class MemoryPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    chunk_id: str
    content: str
    document_id: str
    metadata: dict[str, str]
    owner_id: Literal["cha"]
    schema_version: Literal[1]
    source: str


class EmbeddingDatum(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    embedding: list[float]


class EmbeddingResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    data: list[EmbeddingDatum] = Field(min_length=1)


class QdrantPoint(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    payload: MemoryPayload
    score: float


class QdrantQueryResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    points: list[QdrantPoint]


class QdrantQueryResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    result: QdrantQueryResult


class QdrantScrollPoint(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: str | int
    payload: MemoryPayload


class QdrantScrollResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    next_page_offset: str | int | None = None
    points: list[QdrantScrollPoint]


class QdrantScrollResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    result: QdrantScrollResult


class MemoryLoadResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    collection: str
    document_id: str
    chunk_id: str


class MemorySearchResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    content: str
    document_id: str
    metadata: dict[str, str]
    score: float
    source: str


class MemoryDeleteResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    deleted: Literal[True]
    document_id: str


@dataclass(frozen=True, slots=True)
class MemoryStore:
    settings: RagSettings

    def client(self, base_url: str) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(retries=3, limits=_HTTP_LIMITS)
        return httpx.AsyncClient(
            base_url=base_url,
            follow_redirects=False,
            limits=_HTTP_LIMITS,
            timeout=_HTTP_TIMEOUT,
            transport=transport,
            trust_env=False,
        )

    async def ensure_collection(self) -> None:
        async with self.client(self.settings.qdrant_url) as client:
            response = await client.get(f"/collections/{self.settings.collection_name}")
            if response.status_code == HTTPStatus.NOT_FOUND:
                response = await client.put(
                    f"/collections/{self.settings.collection_name}",
                    json={
                        "vectors": {
                            "distance": "Cosine",
                            "size": self.settings.embedding_dimension,
                        },
                    },
                )
            _ = response.raise_for_status()

    async def load(
        self,
        content: str,
        source: str,
        metadata: dict[str, str],
    ) -> MemoryLoadResult:
        document_id = str(uuid5(NAMESPACE_URL, f"{source}\n{content}"))
        chunk_id = hashlib.sha256(content.encode()).hexdigest()
        payload = MemoryPayload(
            chunk_id=chunk_id,
            content=content,
            document_id=document_id,
            metadata=metadata,
            owner_id="cha",
            schema_version=1,
            source=source,
        )
        vector = await self.embed(content)
        async with self.client(self.settings.qdrant_url) as client:
            response = await client.put(
                f"/collections/{self.settings.collection_name}/points",
                params={"wait": "true"},
                json={
                    "points": [
                        {
                            "id": document_id,
                            "payload": payload.model_dump(),
                            "vector": vector,
                        },
                    ],
                },
            )
            _ = response.raise_for_status()
        return MemoryLoadResult(
            collection=self.settings.collection_name,
            document_id=document_id,
            chunk_id=chunk_id,
        )

    async def search(
        self,
        query: str,
        limit: int,
        entity_anchors: list[str] | None = None,
        *,
        one_per_note: bool = False,
    ) -> list[MemorySearchResult]:
        vector = await self.embed(query)
        if one_per_note:
            # Owner Q155 (2026-10-15): recall looks at the whole store; long chat logs fill a
            # raw top-k with chunks of one conversation, so keep the best passage per note.
            seen: set[str] = set()
            notes: list[MemorySearchResult] = []
            for point in await self._query_points(vector, NOTE_POOL):
                key = _CHUNK_SUFFIX.sub("", point.payload.source)
                if key in seen:
                    continue
                seen.add(key)
                notes.append(self._search_result(point))
                if len(notes) == limit:
                    break
            return notes
        if not entity_anchors:
            async with self.client(self.settings.qdrant_url) as client:
                response = await client.post(
                    f"/collections/{self.settings.collection_name}/points/query",
                    json={
                        "limit": limit,
                        "query": vector,
                        "with_payload": True,
                    },
                )
                _ = response.raise_for_status()
            query_result = QdrantQueryResponse.model_validate(response.json()).result
            return [self._search_result(point) for point in query_result.points]

        anchors = tuple(dict.fromkeys(anchor.strip().casefold() for anchor in entity_anchors))
        anchors = tuple(anchor for anchor in anchors if anchor)
        if not anchors:
            return await self.search(query, limit)
        semantic_points = await self._query_points(vector, limit)
        exact_point_ids, exact_document_ids = await self._literal_point_ids(anchors)
        exact_points = (
            await self._query_points(vector, limit, point_ids=exact_point_ids)
            if exact_point_ids
            else []
        )
        by_document: dict[str, MemorySearchResult] = {}
        for point in (*semantic_points, *exact_points):
            result = self._search_result(point)
            previous = by_document.get(result.document_id)
            if previous is None or result.score > previous.score:
                by_document[result.document_id] = result
        return sorted(
            by_document.values(),
            key=lambda result: self._rank_key(result, exact_document_ids),
            reverse=True,
        )[:limit]

    async def _query_points(
        self,
        vector: list[float],
        limit: int,
        *,
        point_ids: list[str | int] | None = None,
    ) -> list[QdrantPoint]:
        body: dict[str, object] = {
            "limit": limit,
            "query": vector,
            "with_payload": True,
        }
        if point_ids is not None:
            body["filter"] = {"must": [{"has_id": point_ids}]}
        async with self.client(self.settings.qdrant_url) as client:
            response = await client.post(
                f"/collections/{self.settings.collection_name}/points/query",
                json=body,
            )
            _ = response.raise_for_status()
        return QdrantQueryResponse.model_validate(response.json()).result.points

    async def _literal_point_ids(
        self,
        anchors: tuple[str, ...],
    ) -> tuple[list[str | int], set[str]]:
        point_ids: list[str | int] = []
        document_ids: set[str] = set()
        offset: str | int | None = None
        while True:
            body: dict[str, object] = {"limit": 256, "with_payload": True}
            if offset is not None:
                body["offset"] = offset
            async with self.client(self.settings.qdrant_url) as client:
                response = await client.post(
                    f"/collections/{self.settings.collection_name}/points/scroll",
                    json=body,
                )
                _ = response.raise_for_status()
            page = QdrantScrollResponse.model_validate(response.json()).result
            for point in page.points:
                payload = point.payload
                haystack = "\n".join(
                    (
                        payload.content,
                        payload.source,
                        *(f"{key}: {value}" for key, value in payload.metadata.items()),
                    )
                ).casefold()
                if any(anchor in haystack for anchor in anchors):
                    point_ids.append(point.id)
                    document_ids.add(payload.document_id)
            offset = page.next_page_offset
            if offset is None:
                return point_ids, document_ids

    @staticmethod
    def _search_result(point: QdrantPoint) -> MemorySearchResult:
        return MemorySearchResult(
            content=point.payload.content,
            document_id=point.payload.document_id,
            metadata=point.payload.metadata,
            score=point.score,
            source=point.payload.source,
        )

    @staticmethod
    def _rank_key(
        result: MemorySearchResult,
        exact_document_ids: set[str],
    ) -> tuple[bool, float, bool, str]:
        event_date = result.metadata.get("event_date", "") or result.metadata.get(
            "document_updated", ""
        )
        return (
            result.document_id in exact_document_ids,
            result.score,
            bool(event_date),
            event_date,
        )

    async def delete(self, document_id: str) -> MemoryDeleteResult:
        async with self.client(self.settings.qdrant_url) as client:
            response = await client.post(
                f"/collections/{self.settings.collection_name}/points/delete",
                params={"wait": "true"},
                json={
                    "filter": {
                        "must": [
                            {
                                "key": "document_id",
                                "match": {"value": document_id},
                            },
                        ],
                    },
                },
            )
            _ = response.raise_for_status()
        return MemoryDeleteResult(deleted=True, document_id=document_id)

    async def health(self) -> None:
        async with self.client(self.settings.embedding_url) as embedding_client:
            embedding_response = await embedding_client.get("/health")
            _ = embedding_response.raise_for_status()
        async with self.client(self.settings.qdrant_url) as qdrant_client:
            qdrant_response = await qdrant_client.get(
                f"/collections/{self.settings.collection_name}",
            )
            _ = qdrant_response.raise_for_status()

    async def embed(self, text: str) -> list[float]:
        async with self.client(self.settings.embedding_url) as client:
            response = await client.post("/v1/embeddings", json={"inputs": [text]})
            _ = response.raise_for_status()
        return EmbeddingResponse.model_validate(response.json()).data[0].embedding
