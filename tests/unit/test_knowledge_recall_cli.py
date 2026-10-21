from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CLI = _REPO / "skills" / "recall" / "scripts" / "recall_cli.py"


def test_evidence_command_prints_summary_and_single_rendered_source(tmp_path: Path) -> None:
    pack = {
        "version": "knowledge-v1",
        "query": {"text": "동향", "purpose": "cite", "sources": ["rag"], "tags": [], "limit": 8, "caller": "recall"},
        "verdict": "hit",
        "items": [{"id": "E1", "store": "rag", "source_type": "note", "ref": "lab-notes/20260818.md", "title": "실험 기록", "doc_date": "2026-08-18", "date_basis": "path", "score": 0.7, "grounded": True, "authority": None, "expired": None, "sensitivity": None, "content": "본문", "sha256": "a" * 64}],
        "layers": {"rag": "hit", "wiki": "skipped", "twin": "skipped"},
        "notes": [],
    }
    fixture = tmp_path / "pack.json"
    fixture.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "AUTOPHAGY_REPO_ROOT": str(_REPO), "KNOWLEDGE_FAKE_PACK": str(fixture)}
    process = subprocess.run([sys.executable, str(_CLI), "evidence", "동향", "--purpose", "cite", "--json"], env=env, capture_output=True, text=True, check=False)
    assert process.returncode == 0
    payload = json.loads(process.stdout)
    assert payload["evidence_count"] == 1
    assert payload["layers"] == pack["layers"]
    assert payload["sources"] == "[E1] RAG/note: lab-notes/20260818.md (2026-08-18, path)"
    assert "본문" not in process.stdout
