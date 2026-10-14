"""W2-5 recall skill — response schema / threshold / grounding / 기억 없음."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "recall" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import recall_cli  # noqa: E402
import recall_core  # noqa: E402


def _row(score: float, content: str, source: str = "wiki:노트.md#c0000", **metadata):
    metadata.setdefault("source_type", source.split(":", 1)[0])
    return {"score": score, "source": source, "content": content, "metadata": metadata}


# --- tokenize / grounding ----------------------------------------------------

def test_tokenize_keeps_ids_and_hangul_drops_stopwords() -> None:
    tokens = recall_core.tokenize("heliotrope-42198 배양기 코드네임이 뭐야?")
    assert "heliotrope-42198" in tokens
    assert "배양기" in tokens and "코드네임이" in tokens
    assert "뭐야" not in tokens


def test_grounding_matches_suffixed_korean_token() -> None:
    # query token "코드네임이" vs content "코드네임은" — one-char strip retry
    ratio = recall_core.grounding_ratio(
        ["배양기", "코드네임이"], "차세대 배양기 코드네임은 pistachio-5501이다."
    )
    assert ratio == 1.0


def test_grounding_rejects_fabricated_token_query() -> None:
    # live-calibrated case: fabricated query scored 0.52 against this content
    content = "W2-4 인제스트 파이프라인 E2E 고유토큰 heliotrope-42198."
    tokens = recall_core.tokenize("zephyrine-88231 프로토콜 코드네임")
    assert recall_core.grounding_ratio(tokens, content) < recall_core.GROUNDING_RATIO


# --- classify: threshold + grounding rule ------------------------------------

def test_classify_drops_rows_below_hard_floor() -> None:
    hits = recall_core.classify("배양기 코드네임", [_row(0.44, "배양기 코드네임 내용")])
    assert hits == []


def test_classify_needs_grounding_between_floor_and_strong() -> None:
    rows = [_row(0.52, "W2-4 인제스트 파이프라인 E2E 고유토큰 heliotrope-42198.")]
    assert recall_core.classify("zephyrine-88231 프로토콜 코드네임", rows) == []
    grounded = recall_core.classify("W2-4 인제스트 파이프라인 멱등성", rows)
    assert len(grounded) == 1 and grounded[0]["grounded"] is True


def test_classify_strong_score_hits_without_grounding() -> None:
    hits = recall_core.classify("완전히 다른 표현", [_row(0.61, "의미상 매우 유사한 내용")])
    assert len(hits) == 1 and hits[0]["grounded"] is False


# --- response schema ----------------------------------------------------------

_TOP_KEYS = {
    "version", "query", "status", "message", "threshold",
    "strong_threshold", "results", "search",
}
_RESULT_KEYS = {
    "rank", "score", "grounded", "source", "source_type",
    "attribution", "title", "excerpt", "metadata",
}


def test_hit_response_schema_and_ranking() -> None:
    rows = [
        _row(0.61, "배양기 코드네임 pistachio-5501", title="노트", path="노트.md"),
        _row(0.55, "배양기 코드네임 논의", "meeting:회의.md#c0000", path="회의.md"),
    ]
    response = recall_core.build_response("배양기 코드네임", rows, base_url="http://x:8765")
    assert set(response) == _TOP_KEYS
    assert response["version"] == "recall-v1"
    assert response["status"] == "hit" and response["message"] is None
    assert [r["rank"] for r in response["results"]] == [1, 2]
    assert all(set(r) == _RESULT_KEYS for r in response["results"])
    assert response["search"]["attempts"] == 1


def test_no_memory_response_says_exactly_기억_없음() -> None:
    response = recall_core.build_response("없는 사실", [])
    assert response["status"] == "no_memory"
    assert response["message"] == "기억 없음"
    assert response["results"] == []
    assert recall_core.render_text(response).startswith("RECALL-NO-MEMORY 기억 없음")


def test_unavailable_response_when_search_errors() -> None:
    response = recall_core.build_response("질문", None, error="McpUnreachableError: refused")
    assert response["status"] == "unavailable"
    assert "검색 불가" in response["message"]
    assert response["results"] == []
    assert response["search"]["error"].startswith("McpUnreachableError")
    assert response["search"]["attempts"] == 1


# --- attribution --------------------------------------------------------------

def test_attribution_covers_w24_source_types() -> None:
    cases = [
        ("wiki:w2-5-노트.md#c0000", {"source_type": "wiki", "path": "w2-5-노트.md"},
         "위키: w2-5-노트.md"),
        ("meeting:2026-07-15-회의.md#c0000", {"source_type": "meeting",
         "path": "2026-07-15-회의.md"}, "회의: 2026-07-15-회의.md"),
        ("agents-log:1526000000000000001#c0000",
         {"source_type": "peer-report", "task_id": "W1-5"},
         "동료 보고: #agents-log 메시지 1526000000000000001 (task W1-5)"),
        ("team:111-222#c0000", {"source_type": "team-chat", "channel": "team"},
         "팀 채팅: #team 111-222"),
        ("conversation:s1:2026-07-15#c0000",
         {"source_type": "conversation", "session_id": "s1", "day": "2026-07-15"},
         "대화 기록: 세션 s1 (2026-07-15)"),
    ]
    for source, metadata, expected in cases:
        assert recall_core.attribution(source, metadata) == expected


def test_render_text_carries_attribution_and_score() -> None:
    rows = [_row(0.61, "배양기 코드네임 pistachio-5501", title="W2-5 노트", path="노트.md")]
    text = recall_core.render_text(recall_core.build_response("배양기 코드네임", rows))
    assert "score=0.610" in text
    assert "위키: 노트.md" in text
    assert "출처 표기용: 위키: 노트.md" in text


# --- CLI offline round-trip (same hooks the sandbox scenario uses) ------------

def test_cli_fake_unreachable_is_single_attempt_exit_zero(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "recall_cli.py"), "search", "질문", "--json"],
        # env 를 새로 만들면 HOME 도 넘긴다 — 빠지면 자식이 passwd 의 실제 홈에서 ~/.hermes 를 읽는다.
        env={
            "HOME": str(tmp_path),
            "RECALL_FAKE_ERROR": "unreachable",
            "RECALL_LOG_DIR": str(tmp_path),
        },
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "unavailable"
    assert payload["search"]["attempts"] == 1
    log_lines = list(tmp_path.glob("recall-*.log"))[0].read_text().splitlines()
    assert len(log_lines) == 1


# --- phase-0 entity fallback --------------------------------------------------

def _entity_args(query: str) -> argparse.Namespace:
    return argparse.Namespace(
        command="search", query=query, limit=5,
        threshold=recall_core.DEFAULT_THRESHOLD,
        strong_threshold=recall_core.DEFAULT_STRONG_THRESHOLD,
        json=True, entity_fallback=True,
    )


def test_relationship_intent_extracts_only_entity_anchor() -> None:
    intent = recall_cli.analyze_entity_intent("최근 김민준 박사와 함께 진행한 업무 협업 내역")
    assert intent.matches is True
    assert intent.entity_hints == ("김민준",)
    assert recall_cli.analyze_entity_intent("최근 진행한 업무 내역").matches is False
    assert recall_cli.analyze_entity_intent("최근 동료 박사와 한 업무").matches is False
    assert recall_core.tokenize("최근 김민준 박사와 함께 진행한 업무 협업 내역") == ["김민준"]


def test_flagged_fallback_unions_rows_but_only_entity_literal_can_hit(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    unrelated = _row(0.59, "일반 연구 과제 계획과 회의 기록", "obsidian:projects/general.md#c0000", path="projects/general.md")
    anchored = _row(0.52, "김민준 박사와 배양 자동화 일정을 조율했다.", "obsidian:people/collaboration.md#c0000", path="people/collaboration.md")
    calls: list[str] = []

    def fake_search(query: str, limit: int, picker: object = None):
        calls.append(query)
        rows = [unrelated] if len(calls) == 1 else [unrelated, anchored]
        return rows[:limit], None, ["fake://entity"], "fake://entity"

    monkeypatch.setattr(recall_cli, "_mcp_search", fake_search)
    monkeypatch.setenv("RECALL_LOG_DIR", str(tmp_path))
    assert recall_cli.run_search(_entity_args("최근 김민준 박사와 함께 진행한 업무 협업 내역")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == ["최근 김민준 박사와 함께 진행한 업무 협업 내역", "김민준"]
    assert payload["status"] == "hit"
    assert [item["source"] for item in payload["results"]] == ["obsidian:people/collaboration.md#c0000"]
    assert payload["search"]["searches"] == 2
    assert payload["search"]["entity_hint_count"] == 1


def test_flagged_fallback_rejects_strong_unrelated_auxiliary_row(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    unrelated = _row(0.91, "무관한 고득점 문서", "wiki:unrelated.md#c0000", path="unrelated.md")
    calls = 0

    def fake_search(query: str, limit: int, picker: object = None):
        nonlocal calls
        del query, limit
        calls += 1
        return ([] if calls == 1 else [unrelated]), None, ["fake://entity"], "fake://entity"

    monkeypatch.setattr(recall_cli, "_mcp_search", fake_search)
    monkeypatch.setenv("RECALL_LOG_DIR", str(tmp_path))
    assert recall_cli.run_search(_entity_args("요즘 김민준 박사와 같이 한 협업")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == 2
    assert payload["status"] == "no_memory"
    assert payload["results"] == []
    assert payload["search"]["searches"] == 2


def test_flag_off_preserves_single_search_behavior(monkeypatch, capsys, tmp_path: Path) -> None:
    calls = 0

    def fake_search(query: str, limit: int, picker: object = None):
        nonlocal calls
        del query, limit
        calls += 1
        return [], None, ["fake://entity"], "fake://entity"

    monkeypatch.setattr(recall_cli, "_mcp_search", fake_search)
    monkeypatch.setenv("RECALL_LOG_DIR", str(tmp_path))
    args = _entity_args("최근 김민준 박사와 함께 한 협업")
    args.entity_fallback = False
    assert recall_cli.run_search(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == 1
    assert payload["status"] == "no_memory"
    assert payload["search"]["searches"] == 1
    assert payload["search"]["entity_hint_count"] == 0


def test_result_forwards_date_and_folder_metadata() -> None:
    row = _row(
        0.61, "김민준 협업 기록", "obsidian:people/collaboration.md#c0000",
        created="2026-05-02", updated="2026-08-20", day="2026-08-20",
        folder="people",
    )
    result = recall_core.classify("김민준 협업", [row])[0]
    assert result["metadata"] == {
        "source_type": "obsidian", "created": "2026-05-02",
        "updated": "2026-08-20", "day": "2026-08-20",
        "folder": "people",
    }


def test_obsidian_attribution_uses_path() -> None:
    assert recall_core.attribution(
        "obsidian:opaque-id#c0000",
        {"source_type": "obsidian", "path": "projects/collaboration.md"},
    ) == "Obsidian: projects/collaboration.md"


def test_recall_runtime_root_prefers_the_release_over_the_stale_mirror(tmp_path: Path) -> None:
    """The skill must resolve `automation.*` from the immutable release, not the mirror.

    `/srv/autophagy-agents` is a one-way observation checkout that STOPS advancing the
    moment it is dirty, and whatever it holds shadows the release's `automation`
    package because this insert lands first in sys.path. Measured 2026-08-22: the
    mirror sat at b6b3574 with a dirty tracked file and therefore carried no
    `automation/knowledge`, so the deployed recall died at import with
    `ModuleNotFoundError: No module named 'automation.knowledge'` even though the
    running release carried the package. The mirror is only the last-resort fallback.
    """
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    scripts = root / "skills/recall/scripts"
    assert '"/srv/autophagy-agents"' not in (scripts / "recall_cli.py").read_text(
        encoding="utf-8"
    ), "recall_cli must not name the ops mirror; the resolver owns that fallback"

    spec = importlib.util.spec_from_file_location(
        "recall_runtime_under_test", scripts / "recall_runtime.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    current = tmp_path / "release"
    mirror = tmp_path / "mirror"
    current.mkdir()
    mirror.mkdir()

    assert module.runtime_root({}, current=current, mirror=mirror) == current
    assert module.runtime_root({}, current=tmp_path / "absent", mirror=mirror) == mirror
    assert module.runtime_root(
        {"AUTOPHAGY_REPO_ROOT": str(tmp_path / "override")}, current=current, mirror=mirror
    ) == tmp_path / "override"
