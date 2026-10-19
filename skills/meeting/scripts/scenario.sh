#!/usr/bin/env bash
# W2-3 meeting skill sandbox scenario (offline, deterministic, no LLM/network).
# Contract (deploy-skill.sh): runs under `env -i HOME=... PATH=/usr/bin:/bin`,
# must print SCENARIO-PASS on success and exit nonzero on any failure.
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export PYTHONPYCACHEPREFIX="$work/pycache"
cd "$work"

export MEETING_NOTES_DIR="$work/notes"
export MEETING_PROMPT_FILE="$skill_dir/prompts/meeting-extraction-v4.md"
export MEETING_LOG_DIR="$work/logs"
export MEETING_PLAN_DIR="$work/plan"
export MEETING_CONFIG="$work/no-config.json"

cli="$skill_dir/scripts/meeting_cli.py"
fx="$skill_dir/fixtures"
cat > "$work/evidence-pack.json" <<'JSON'
{"version":"knowledge-v1","query":{"text":"실증 회의 연구팀","purpose":"cite","sources":["rag","wiki","twin"],"tags":[],"limit":8,"caller":"meeting"},"verdict":"hit","items":[{"id":"E1","store":"rag","source_type":"meeting","ref":"meetings/previous.md","title":"선행 회의","doc_date":"2026-08-14","date_basis":"path","score":0.9,"grounded":true,"authority":null,"expired":null,"sensitivity":null,"content":"지난 회의 결정","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],"layers":{"rag":"hit","wiki":"none","twin":"none"},"notes":[]}
JSON
export KNOWLEDGE_FAKE_PACK="$work/evidence-pack.json"

echo "[1] compile"
python3 -m py_compile "$skill_dir"/scripts/*.py "$skill_dir"/plugin/__init__.py

echo "[2] clean md ingest (recorded LLM) -> cards>=3, minutes, team post"
python3 "$cli" ingest --file "$fx/meeting-clean.md" --with-evidence \
  --recorded-response "$fx/recorded-clean.json" --offline --notify-channel SANDBOX
card_lines=$(wc -l < "$work/plan/kanban-plan.jsonl")
[ "$card_lines" -ge 3 ] || { echo "FAIL cards=$card_lines"; exit 1; }
grep -q '^## 마일스톤' "$work/notes/"*.md || { echo "FAIL milestones missing from minutes"; exit 1; }
head -1 "$work/plan/team-post.txt" | grep -q '```json' || { echo "FAIL team post"; exit 1; }
grep -q '회의록 처리 완료' "$work/plan/notify.txt" || { echo "FAIL notify"; exit 1; }
grep -q '"verdict": "hit"' "$work/notes/"*.evidence.json || { echo "FAIL evidence sidecar"; exit 1; }

echo "[3] patent-keyword md ingest -> handled like any meeting (cards, milestones, team post)"
rm -rf "$work/plan" && mkdir -p "$work/plan"
out=$(python3 "$cli" ingest --file "$fx/meeting-patent.md" \
  --recorded-response "$fx/recorded-patent.json" --offline --notify-channel SANDBOX)
echo "$out" | tail -1 | grep -q '"sensitive"' && { echo "FAIL sensitivity verdict still reported"; exit 1; }
grep -q '청구항(claim) 초안 작성' "$work/plan/kanban-plan.jsonl" || { echo "FAIL card title masked"; exit 1; }
grep -q '특허 출원서 제출' "$work/notes/"*.md || { echo "FAIL milestone masked"; exit 1; }
head -1 "$work/plan/team-post.txt" | grep -q '```json' || { echo "FAIL team post suppressed"; exit 1; }
untagged=$(grep -L '^tags: \[meeting, w2-3\]$' "$work/notes/"*.md || true)
[ -z "$untagged" ] || { echo "FAIL extra tag on note"; exit 1; }

echo "[5] 30MiB reject"
dd if=/dev/zero of="$work/big.md" bs=1 count=1 seek=31457279 status=none
rc=0; python3 "$cli" ingest --file "$work/big.md" --offline >/dev/null || rc=$?
[ "$rc" -eq 3 ] || { echo "FAIL size rc=$rc"; exit 1; }

echo "[6] scanned pdf -> manual conversion request"
python3 "$skill_dir/scripts/make_fixture_pdf.py" "$work/scan.pdf" --scanned
rc=0; out=$(python3 "$cli" ingest --file "$work/scan.pdf" --offline) || rc=$?
[ "$rc" -eq 4 ] || { echo "FAIL scanned rc=$rc"; exit 1; }
echo "$out" | grep -q '수동 변환 요청' || { echo "FAIL scanned notice"; exit 1; }

echo "[7] text pdf ingest works"
python3 "$skill_dir/scripts/make_fixture_pdf.py" "$work/text.pdf" --text
python3 "$cli" ingest --file "$work/text.pdf" \
  --recorded-response "$fx/recorded-clean.json" --offline \
  | grep -q '"provider": "recorded"' || { echo "FAIL pdf ingest"; exit 1; }

echo "[8] slides deck labels the note and keeps the body clean"
rm -rf "$work/notes" "$work/plan" && mkdir -p "$work/plan"
printf '# 킥오프\n과제명: AUTOPHAGY-2026 자율 연구 에이전트\n' > "$work/deck.md"
python3 "$cli" ingest --file "$fx/meeting-clean.md" --slides "$work/deck.md" \
  --recorded-response "$fx/recorded-clean.json" --offline > /dev/null
note=$(echo "$work/notes/"*.md)
grep -q '| 발표자료 | deck.md (1쪽) |' "$note" || { echo "FAIL slide label missing"; exit 1; }
grep -q '^## 부록 · 근거와 원문$' "$note" || { echo "FAIL appendix boundary missing"; exit 1; }
awk '/^## 부록 · 근거와 원문$/{exit} /근거:/{leak=1} END{exit leak?1:0}' "$note" \
  || { echo "FAIL inline evidence leaked into the minutes body"; exit 1; }
grep -q '^- \[근1\] ' "$note" || { echo "FAIL evidence not defined in the appendix"; exit 1; }

echo "SCENARIO-PASS"
