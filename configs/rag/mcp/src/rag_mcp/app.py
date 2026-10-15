from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Annotated, ClassVar, Final, override

from fastapi import FastAPI
from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from rag_mcp.auth import authorization_status
from rag_mcp.settings import RagSettings
from rag_mcp.store import MemoryDeleteResult, MemoryLoadResult, MemorySearchResult, MemoryStore


class AgentApiKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, api_key: str) -> None:
        super().__init__(app)
        self.api_key: str = api_key

    @override
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        status_code = authorization_status(request.headers.get("Authorization"), self.api_key)
        if status_code == HTTPStatus.OK:
            return await call_next(request)
        return Response(status_code=status_code)


class HealthResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    collection: str
    status: str


MAX_PASSAGE_LIMIT = 20
_NO_EXCLUSIONS: Final[list[str]] = []  # Shared schema default, never mutated.
settings = RagSettings.from_environment()
memory_store = MemoryStore(settings)
mcp = FastMCP("personal-memory")


@mcp.tool
async def load_memory(
    content: Annotated[str, Field(min_length=1, max_length=100_000)],
    source: Annotated[str, Field(min_length=1, max_length=2_000)],
    metadata: dict[str, str] | None = None,
) -> MemoryLoadResult:
    return await memory_store.load(content, source, metadata or {})


@mcp.tool
async def search_memory(  # noqa: PLR0913 - public MCP tool arguments are separate JSON fields
    query: Annotated[str, Field(min_length=1, max_length=100_000)],
    limit: Annotated[int, Field(ge=1, le=100)] = 5,
    entity_anchors: Annotated[list[str] | None, Field(max_length=20)] = None,
    one_per_note: bool = False,  # noqa: FBT001, FBT002 - MCP tool arguments are named JSON fields
    per_note: Annotated[int, Field(ge=1, le=3)] = 1,
    exclude_notes: Annotated[
        list[Annotated[str, Field(max_length=300)]], Field(max_length=300)
    ] = _NO_EXCLUSIONS,
) -> list[MemorySearchResult]:
    if one_per_note:
        if entity_anchors is not None:
            message = "one_per_note cannot be combined with entity_anchors"
            raise ValueError(message)
        return await memory_store.search(
            query, limit, one_per_note=True, per_note=per_note, exclude_notes=exclude_notes
        )
    if per_note != 1 or exclude_notes:
        message = "per_note > 1 or exclude_notes needs one_per_note=True"
        raise ValueError(message)
    if limit > MAX_PASSAGE_LIMIT:
        message = f"limit above {MAX_PASSAGE_LIMIT} needs one_per_note"
        raise ValueError(message)
    if entity_anchors is None:
        return await memory_store.search(query, limit)
    return await memory_store.search(query, limit, entity_anchors)


@mcp.tool
async def memory_core_notes() -> list[dict[str, str]]:
    return await memory_store.core_notes()


@mcp.tool
async def delete_memory(
    document_id: Annotated[str, Field(min_length=1, max_length=100)],
) -> MemoryDeleteResult:
    return await memory_store.delete(document_id)


mcp_app = mcp.http_app(
    path="/",
    middleware=[Middleware(AgentApiKeyMiddleware, api_key=settings.api_key)],
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await memory_store.ensure_collection()
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> HealthResponse:
    await memory_store.health()
    return HealthResponse(collection=settings.collection_name, status="ok")


app.mount("/mcp", mcp_app)
