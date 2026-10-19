# Hermes model routing policy

## Q245: reversible workstation batch relay (2026-10-18)

The release-host file `~/.hermes/release-flags/omo-relay`, exact bytes `on`, selects
tool-free workstation omo for `automation.codex_llm` batch calls. In this mode the
workstation's omo account configuration alone chooses the model and fallback;
neither the node nor SSH argv selects a model. Actual provider/model come from omo
JSON, with missing values recorded as `unknown`. Transport/overload gets one retry
after 30 seconds; empty text is a request error, not success.

The release binds the `omo_relay_target` and `omo_relay_key` values from the release
host's own relay file (not the node config, whose root helper copy must stay unchanged) to its
approval, provisions a dedicated node key, and installs the approved relay settings. Removing the host flag and redeploying removes this override and returns
to the unchanged legacy behavior described below. Interactive gateway calls are not
changed by card A. See [relay usage](../docs/기능소개/노드-배치-omo-중계.md).

## Policy

1. **One place picks the model (owner decision 2026-09-29).** Every interactive and
   non-interactive call starts on the main model named in the `model:` block
   (`provider`/`default`) of the account's `~/.hermes/config.yaml`. The Discord gateway reads it
   there; batch automation goes through the shared `automation.codex_llm` client, whose argv
   carries neither `--provider` nor `-m`, so Hermes applies the same block. No code, skill or
   environment variable names a model — `tests/unit/test_model_single_source_conformance.py`
   fails when one does. Changing the main model or the fallback is an edit to that file alone.
2. **One fallback chain, owned by Hermes.** When the primary cannot answer (rate limit or quota,
   server error, auth failure, invalid response), Hermes switches to the `fallback_providers`
   chain in the same `~/.hermes/config.yaml`. The gateway and the batch client read that one
   chain — the batch client does **not** pass `--ignore-user-config`, because that flag drops the
   user config and the chain with it. Fallback is scoped to one turn (gateway) or one call
   (batch); the primary is tried again next time unless Hermes knows its reset time has not
   passed yet.
3. **Fail closed when the chain is exhausted.** A call succeeds only when the shared client
   receives exit code 0 and non-empty output. When the primary and every fallback fail, the error
   reaches the caller. No caller retries on its own or opens a route outside the shared client;
   the only place to add or remove a route is `fallback_providers`.
4. **Content does not choose a route.** All text uses the same account configuration.
   External writes still require their existing owner approval, credentials and permissions.
5. **회계.** 호출 비용·구독 사용량은 실제 응답한 공급자 계정에서 확인한다.
   예전 비용 게이트웨이와 09:00 일일 비용 보고는 2026-10-19 소유자 "이것도 이제 없애라" 결정으로 폐기했다.
6. **Logs name who answered.** Every masked routing log keeps `provider` and
   `model: hermes-config` (the config decided) and records the route that actually answered in `served_provider`/
   `served_model`, read from Hermes' `--usage-file` report (`automation.codex_llm.complete_served`).
   A missing report is logged as `unknown`, never assumed to be Codex.

## Installation binding

| Item | Value |
|---|---|
| Main model | `model.provider` / `model.default` in `~/.hermes/config.yaml` (2026-10-02: `openai-codex` / `gpt-6.1-sol`, reasoning effort `medium`) |
| Fallback chain | `fallback_providers` in the same file (2026-09-29: `xai-oauth` / `grok-4.7`) |
| Batch client | `automation.codex_llm.CodexClient` — `hermes -z <prompt> -t todo` (no model, no provider) |
| First install | `automation/provision-agent.sh` writes the initial block; afterwards only the owner edits it |
| Peer account | follows the agent block — `python3 -m automation.model_sync [--apply]` (operator) copies `model:`/`fallback_providers:` to the peer; operator-mode doctor reports `model-parity` |
| Proposal refine | shared Hermes route (no Codex CLI); rules from im-not-ai `humanize-korean/references/quick-rules.md` |
| Authentication | `hermes auth` — Codex OAuth and xAI Grok OAuth (SuperGrok), both stored for the agent account |
| Success condition | exit code 0 and non-empty stdout |
| Unavailable condition | every route in the chain failed (credentials, quota, transport, timeout, or empty output) |

## Changing the main model

The owner changes the pair in one place and then runs every step below in the same session. Step 4
is the one that was missing: after the 2026-09-29 change one job was skipped every day for nine days
before anyone saw it.

1. Edit `model:` (and `fallback_providers:` if it changes) in the agent's `~/.hermes/config.yaml`.
2. `python3 -m automation.model_sync --apply` (operator) so the peer follows the agent block.
3. Restart the agent and peer gateways together (`docs/guide/operations.md` §2).
4. **Recreate every unpinned agent-mode cron job.** Hermes stamps each job with the model that was
   current when it was created. A job that has no pinned model and is not `--no-agent` is then
   skipped on every run with `[drift_skip:silent]`, and Hermes reports this only once. Do not
   apply the pin that Hermes suggests (`hermes cron edit <id> --model …`), because that writes the
   model into a second place and the job falls behind at the next change. For each account
   (agent, then peer):
   - List the affected jobs:
     `python3 -c "import json,pathlib;d=json.loads((pathlib.Path.home()/'.hermes/cron/jobs.json').read_text());[print(j['id'],j['name']) for j in d['jobs'] if not j.get('no_agent') and not j.get('model')]"`
   - Copy the job's `prompt`, `schedule`, `skills`, `deliver`, `context_from` and
     `enabled_toolsets` from `~/.hermes/cron/jobs.json` to a file before you remove anything.
   - `hermes cron remove <id>`, then
     `hermes cron create "<schedule>" "<prompt>" --name <name> --skill <s> … --deliver <d>`, with
     no `--model` and no `--provider`. Add `--continuity` if `context_from` was `["self"]`.
     `hermes cron create` has no option for `enabled_toolsets`; restore the list with Hermes' own
     locked update, run from `~/.hermes/hermes-agent`:
     `HERMES_HOME=$HOME/.hermes ./venv/bin/python -c 'from cron import jobs; jobs.update_job("<new id>", {"enabled_toolsets": [...]})'`.
   - Compare the new entry with the copy. Only `id`, `created_at`, run counters and the model snapshot
     may differ.
5. Confirm with `python3 -m automation.doctor` as each account. `정기 작업` reports a
   `drift_skip` job as broken on its first skip, so a job missed in step 4 shows up at the next
   hourly doctor alarm instead of after three failures.

## Verification

Where the owner sees the pair without running anything: every release-applied notice in
#notifications carries a `모델:` line with both accounts' main model, fallback and `agent.max_turns`
(`automation/release_models.py`, read by the workstation sweep over ssh). On demand:
`python3 -m automation.doctor` as the agent account prints
`주 모델 <provider>/<model>` and `폴백 <provider>/<model>`; the file itself is
`/home/agent/.hermes/config.yaml`; what actually answered each call is `served_provider`/
`served_model` in the skill routing logs (`~/.hermes/*/logs/llm-calls.jsonl`).

Run a non-interactive completion as the agent user with an empty stdin and a minimal environment.
It must return plain, non-empty text on stdout. `hermes fallback list` as the agent user must show
the chain above; a missing xAI login makes the fallback fail, which leaves the primary's own error
as the result (the same outcome as having no fallback). Do not add a provider anywhere other than
`fallback_providers`.
