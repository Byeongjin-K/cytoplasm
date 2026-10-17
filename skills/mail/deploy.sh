#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Fail closed on an unset host. The old fallback was a literal placeholder hostname, so
# forgetting the variable produced an unresolvable-hostname ssh
# error that pointed at DNS instead of at the real cause ("the variable was not set").
# Nothing was ever deployed to a wrong node — this is deploy-time friction, not a
# safety hole — but the failure has to name its own cause.
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [ -z "$host" ]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)" >&2
  echo "              and re-run; refusing to ssh to an unresolvable placeholder." >&2
  exit 3
fi

run_agent() {
  local script="$1"
  # Land in a directory the agent account can read. ssh leaves us in the
  # operator's home and `-H` only rewrites HOME, not the cwd — so tools that
  # restore their initial directory die there. Measured: the runtime release's
  # digest `find` aborted with "Failed to restore initial working directory:
  # /home/<operator>: Permission denied", and `set -euo pipefail` took the
  # whole deploy with it. Same guard as automation/memory_curator/deploy.sh.
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "cd \"\$HOME\"; $script")"
}

# push_file 은 원격 read-back 으로 착지를 확인한다 — 확인 없는 push 가 rc=0 으로
# 끝나면서 파일은 그대로였던 실측(2026-08-20)이 이 공유 구현의 이유다.
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

# Ship the vendored mailon tree (source of truth after the emailAutomation
# cutover) to a checkout-external staging dir, then build+activate a versioned
# runtime release LOCALLY on the node. mailon_runtime_release.sh keeps data/
# logs/.venv outside the checkout so runtime writes never dirty it (Oracle S3).
deploy_vendor_mailon() {
  local staging='.hermes/mailon-vendor-staging'
  run_agent "rm -rf \"\$HOME/$staging\"; mkdir -p \"\$HOME/$staging\""
  # Stream the vendor tree (mailon/ + requirements.txt) as a tar to the node.
  deploy_archive_stream "$repo_root" "$repo_root/skills/mail/vendor" mailon requirements.txt \
    | ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "umask 077; tar -C \"\$HOME/$staging\" -xzf -")"
  push_file "$repo_root/skills/mail/scripts/mailon_vendor_digest.sh" \
    "$staging/mailon_vendor_digest.sh"
  push_file "$repo_root/skills/mail/scripts/mailon_runtime_release.sh" \
    "$staging/mailon_runtime_release.sh"
  run_agent "chmod 700 \"\$HOME/$staging/mailon_runtime_release.sh\"; \"\$HOME/$staging/mailon_runtime_release.sh\" \"\$HOME/$staging\""
}

# Deploy guard: refuse to push code that origin/main does not have (see the header of
# automation/deploy_provenance.sh for why a silent revert is otherwise inevitable).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/skills/mail/scripts/mail_digest_watch.py" \
  "$repo_root/skills/mail/scripts/mail_attachment_drive_watch.py" \
  "$repo_root/skills/mail/scripts/watch_failure_streak.py" \
  "$repo_root/skills/mail/scripts/mail_triage_watch.py" \
  "$repo_root/skills/mail/scripts/mail_sync_watch.py" \
  "$repo_root/skills/mail/scripts/mailon_runtime_release.sh" \
  "$repo_root/skills/mail/scripts/mailon_vendor_digest.sh" \
  "$repo_root/skills/mail/scripts/mailon_runtime_drift.sh" \
  "$repo_root/skills/mail/vendor/mailon" \
  "$repo_root/skills/mail/vendor/requirements.txt" \
  "$repo_root/skills/mail/deploy.sh" "$repo_root/skills/mail/deploy-manifest.txt" || exit 4

# The streak helper both watchers import. It ships first: a wrapper that lands without
# it still runs (ImportError fallback), but it would keep the old per-tick behaviour.
push_file "$repo_root/skills/mail/scripts/watch_failure_streak.py" \
  '.hermes/scripts/watch_failure_streak.py'
# The runtime drift probe and its digest helper. The digest helper must land first: the
# probe sources it from its own directory and exits UNKNOWN (never 0) without it.
push_file "$repo_root/skills/mail/scripts/mailon_vendor_digest.sh" \
  '.hermes/scripts/mailon_vendor_digest.sh'
push_file "$repo_root/skills/mail/scripts/mailon_runtime_drift.sh" \
  '.hermes/scripts/mailon_runtime_drift.sh'
run_agent "chmod 700 \"\$HOME/.hermes/scripts/mailon_runtime_drift.sh\""
push_file "$repo_root/skills/mail/scripts/mail_digest_watch.py" \
  '.hermes/scripts/mail_digest_watch.py'
push_file "$repo_root/skills/mail/scripts/mail_attachment_drive_watch.py" \
  '.hermes/scripts/mail_attachment_drive_watch.py'
run_agent "chmod 700 \"\$HOME/.hermes/scripts/mail_attachment_drive_watch.py\""
# Remove the agent-made 2026-08-29 sync copy: the watcher now runs the sync script
# directly from the governed live skill mount, not from the agent home.
run_agent 'rm -f "$HOME/.hermes/scripts/"mail_attachment_drive_sync.py'
# The approval/send loop. This file was corrected to read the governed live
# store months ago but was never listed here, so the node kept running a copy
# that still probed the pre-inversion ~/.hermes/skills/mail path — after the
# SS-1 root inversion that path holds the agent's OWN skills, not deployed ones.
# Measured 2026-08-18: 108 consecutive ticks exited 1 with "mail skill is not
# mounted", so an owner ✅ resolved to nothing. "커밋됨 ≠ 배포됨" applies to
# watchers too — a fix that no deploy script carries is not deployed.
push_file "$repo_root/skills/mail/scripts/mail_triage_watch.py" \
  '.hermes/scripts/mail_triage_watch.py'
# Converge the triage cron onto --deliver discord. That was unsafe while every failing
# tick printed a line (a stuck */10 job would have DM'd cha 144 times a day); with the
# streak threshold the watcher speaks once when an incident opens and once when it
# closes, so the delivery target can finally be one that reaches somebody. Measured
# 2026-08-18: 111 consecutive failures under --deliver local reached nobody at all.
run_agent 'PATH="$HOME/.local/bin:$PATH"; job_id=$(hermes cron list --all | awk "/^  [0-9a-f]+ \[/{id=\$1} /Name:[[:space:]]+mail-triage-watch\$/{print id; exit}"); if [ -n "$job_id" ]; then hermes cron edit "$job_id" --schedule "*/2 * * * *" --deliver discord --no-agent --script mail_triage_watch.py; else hermes cron create "*/2 * * * *" --name mail-triage-watch --no-agent --script mail_triage_watch.py --deliver discord; fi'
source "$repo_root/automation/deploy_timer.sh"
converge_timer mail-triage-watch "*/2 * * * *" mail_triage_watch.py discord
# Register (or converge) the daily digest cron. --deliver discord routes the
# no-agent script's stdout to the owner DM, so a failure marker line + exit 1
# actually reaches cha (the 2026-07-31 incident vanished under --deliver local,
# which has 0 delivery targets). An already-registered job is converged in
# place with `edit` (preserving its job id/history); otherwise it is created.
run_agent 'PATH="$HOME/.local/bin:$PATH"; job_id=$(hermes cron list --all | awk "/^  [0-9a-f]+ \[/{id=\$1} /Name:[[:space:]]+mail-daily-digest\$/{print id; exit}"); if [ -n "$job_id" ]; then hermes cron edit "$job_id" --schedule "0 8 * * *" --deliver discord --no-agent --script mail_digest_watch.py; else hermes cron create "0 8 * * *" --name mail-daily-digest --no-agent --script mail_digest_watch.py --deliver discord; fi'
converge_timer mail-daily-digest "0 8 * * *" mail_digest_watch.py discord
source "$repo_root/automation/deploy_cron.sh"
converge_timer mail-attachment-drive-watch "every 30m" mail_attachment_drive_watch.py discord
# Workday hourly collection (2026-10-13): the 08:00 digest sync was the only scheduled one,
# so one failed morning left a day without stored mail and nobody was told.
push_file "$repo_root/skills/mail/scripts/mail_sync_watch.py" '.hermes/scripts/mail_sync_watch.py'
run_agent "chmod 700 \"\$HOME/.hermes/scripts/mail_sync_watch.py\""
converge_timer mail-sync-watch "20 9-19 * * 1-5" mail_sync_watch.py discord

# N1: 다이제스트 실패 → 수리 티켓(09:10 KST). 같은 이름의 손으로 넣은 에이전트 모드 Hermes 잡은
# converge_timer 가 일시정지한다(지우지 않는다).
push_file "$repo_root/skills/mail/scripts/mail_digest_repair_watch.py" '.hermes/scripts/mail_digest_repair_watch.py'
converge_timer mail-triage-repair-daily "10 9 * * *" mail_digest_repair_watch.py local
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all'

# Build + activate the vendored mailon runtime release on the node.
deploy_vendor_mailon
