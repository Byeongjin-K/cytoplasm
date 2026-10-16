#!/usr/bin/env bash
# The 2-minute reconciler converges the RELEASE TREE and nothing else. Everything checked
# here lives OUTSIDE it and is installed by root through a provisioner, so a merged change
# to any of them sits unapplied until a human re-runs that provisioner — and until
# 2026-08-19 nothing said so.
#
# The bill for that silence was three days. `autophagy-deploy-reconcile.service` was
# missing `BindPaths=/run/user`, so `systemctl --user restart` inside the unit could not
# see the control socket, every convergence installed its release and was immediately
# rolled back, and the only trace was `reason=gateway-restart` inside a state file nobody
# reads. This probe existed at the time and watched exactly one file.
#
# /etc/sudoers.d/autophagy-deploy-reconcile is deliberately NOT compared: it is 0440
# root:root and this probe runs as the cron account, so it is unreadable here by design.
# provision-deploy-reconcile.sh already proves that file EFFECTIVE at install time
# (`sudo -n -l -U <account>` must list the converge helper), which is stronger than a hash.
release_helper_probe_log() { printf '[release-helper] %s\n' "$*" >&2; }

#: `.service` and `gateway_pair.py` are `$NODE_*` templates. Comparing the raw template
#: would mark EVERY node as drifted forever, and a probe that is always red is not a
#: signal (2026-08-04 G8 taught that the expensive way). So render with the same renderer
#: the provisioner used. PYTHONDONTWRITEBYTECODE is not optional: this runs python from
#: the sealed release tree, and one stray __pycache__ blocks every later deploy.
release_helper_probe_render() { # release_helper_probe_render <source-root> <relative>
  local root="$1" rel="$2" out status=0
  out="$(mktemp)" || return 1
  PYTHONDONTWRITEBYTECODE=1 python3 "$root/automation/node_asset_renderer.py" \
    "$root/$rel" "$out" >/dev/null 2>&1 || status=1
  if (( status == 0 )); then sha256sum -- "$out" | cut -d' ' -f1; fi
  rm -f -- "$out"
  return "$status"
}

#: 자산마다 놓는 프로비저너가 다르다. “셋 중 하나를 돌려보라”는 안내는 틀린 스크립트를
#: 돌리게 하고, 그러면 드리프트는 그대로 남은 채 고츠 셈 치게 된다 — 그래서 항목마다
#: 소유 프로비저너를 실어 드리프트한 자산의 것만 집어 낸다.
release_helper_probe_guidance() { # release_helper_probe_guidance <provisioner>
  release_helper_probe_log \
    "re-run the provisioner on the node: sudo bash <release>/automation/$1"
}

probe_release_helper_drift() {
  local _node="$1" _account="$2" _target="$3"
  local libexec="${HEALTHCHECK_LIBEXEC_DIR:-/usr/local/libexec}"
  local units="${HEALTHCHECK_UNIT_DIR:-/etc/systemd/system}"
  local source_root="${HEALTHCHECK_RELEASE_SOURCE_ROOT:-/srv/autophagy-agent-current}"
  local helper="${HEALTHCHECK_RELEASE_HELPER:-$libexec/autophagy-install-release}"
  local provenance="${HEALTHCHECK_RELEASE_PROVENANCE:-$libexec/release_provenance.py}"
  local entry installed relative provisioner installed_hash source_hash

  # installed|release-source|provisioner — byte copies, compared directly.
  local -a copied=(
    "$helper|automation/release_store.py|provision-release-store.sh"
    "$provenance|automation/release_provenance.py|provision-release-store.sh"
    "$units/autophagy-deploy-reconcile.timer|automation/systemd/autophagy-deploy-reconcile.timer|provision-deploy-reconcile.sh"
  )
  # installed|release-source|provisioner — `$NODE_*` templates, compared against a fresh render.
  local -a rendered=(
    "$libexec/autophagy-gateway-pair|automation/gateway_pair.py|provision-release-store.sh"
    "$units/autophagy-deploy-reconcile.service|automation/systemd/autophagy-deploy-reconcile.service|provision-deploy-reconcile.sh"
    "$libexec/autophagy-install-skill|automation/skill_store.py|provision-skill-roots.sh"
    "$libexec/autophagy-converge-origin-main|automation/converge_origin_main.sh|provision-deploy-converge.sh"
    "$libexec/autophagy-resume-deploy|automation/libexec/autophagy-resume-deploy|provision-supply-chain-watch.sh"
    #: 리컨실러가 **실제로 실행하는** 사본들이다. libexec 루트의 동명 자산과
    #: 별개라, 위쪽이 신선해도 이쪽은 낡을 수 있다 — 2026-08-20 실측이 정확히 그러했고,
    #: `release_store.py` 가 prune 도입 이전(08-02) 에 멈춰 세대가 무한히 쌓였다.
    "$libexec/autophagy-converge.d/origin_snapshot.sh|automation/origin_snapshot.sh|provision-deploy-converge.sh"
    "$libexec/autophagy-converge.d/release_store.py|automation/release_store.py|provision-deploy-converge.sh"
    "$libexec/autophagy-converge.d/release_provenance.py|automation/release_provenance.py|provision-deploy-converge.sh"
    #: 그리고 그 사본들이 **import 하는** 패키지다. 2026-09-07 까지 설치기는 이 여섯을 아예
    #: 놓지 않아 신규 노드의 첫 수렴이 SYNC-BLOCK 으로 섰다. 이제 두 설치 경로가 같은 집합을
    #: 놓으므로(tests/unit/test_install_converge_parity.py), 감시도 같은 집합이어야 한다 —
    #: 놓기만 하고 아무도 보지 않으면 낡아도 조용한 것은 위 세 개와 똑같다.
    "$libexec/autophagy-converge.d/automation/__init__.py|automation/__init__.py|provision-deploy-converge.sh"
    "$libexec/autophagy-converge.d/automation/git_tag_signature.py|automation/git_tag_signature.py|provision-deploy-converge.sh"
    "$libexec/autophagy-converge.d/automation/typing_compat.py|automation/typing_compat.py|provision-deploy-converge.sh"
    "$libexec/autophagy-converge.d/automation/update_trust.py|automation/update_trust.py|provision-deploy-converge.sh"
    "$libexec/autophagy-converge.d/automation/update_trust_state.py|automation/update_trust_state.py|provision-deploy-converge.sh"
    "$libexec/autophagy-converge.d/automation/node_config.py|automation/node_config.py|provision-deploy-converge.sh"
    "$libexec/autophagy-converge.d/automation/node.example.toml|configs/node.example.toml|provision-deploy-converge.sh"
  )

  for entry in "${copied[@]}" "${rendered[@]}"; do
    IFS='|' read -r installed relative provisioner <<< "$entry"
    # 없는 것과 못 읽는 것은 다르다. 설치기가 놓기로 된 파일이 **없는** 것은
    # 드리프트이므로 재실행 안내까지 내야 하고(실측: converge.d/release_provenance.py 부재),
    # 있는데 권한 때문에 못 읽는 것은 진짜로 모르는 것이라 UNKNOWN 이다 — 그걸
    # “미설치”로 단언하면 멀집한 파일을 지우라고 시키는 오보가 된다.
    if [[ ! -e "$installed" ]]; then
      release_helper_probe_log "HELPER-DRIFT: ${installed} is not installed"
      release_helper_probe_guidance "$provisioner"
      return 1
    fi
    if [[ ! -r "$installed" ]]; then
      release_helper_probe_log "HELPER-DRIFT-UNKNOWN: unreadable path=$installed"
      return 1
    fi
    if [[ ! -r "$source_root/$relative" ]]; then
      release_helper_probe_log "HELPER-DRIFT-UNKNOWN: unreadable release source=$relative"
      return 1
    fi
  done

  for entry in "${copied[@]}"; do
    IFS='|' read -r installed relative provisioner <<< "$entry"
    installed_hash="$(sha256sum -- "$installed" | cut -d' ' -f1)"
    source_hash="$(sha256sum -- "$source_root/$relative" | cut -d' ' -f1)"
    if [[ "$installed_hash" != "$source_hash" ]]; then
      release_helper_probe_log "HELPER-DRIFT: ${installed##*/} differs from release source"
      release_helper_probe_guidance "$provisioner"
      return 1
    fi
  done

  for entry in "${rendered[@]}"; do
    IFS='|' read -r installed relative provisioner <<< "$entry"
    if ! source_hash="$(release_helper_probe_render "$source_root" "$relative")"; then
      release_helper_probe_log "HELPER-DRIFT-UNKNOWN: could not render $relative"
      return 1
    fi
    installed_hash="$(sha256sum -- "$installed" | cut -d' ' -f1)"
    if [[ "$installed_hash" != "$source_hash" ]]; then
      release_helper_probe_log "HELPER-DRIFT: ${installed##*/} differs from the rendered release source"
      release_helper_probe_guidance "$provisioner"
      return 1
    fi
  done

  release_helper_probe_log "HELPER-DRIFT-PASS: privileged helpers and units match release sources"
}

release_helper_probe_script() {
  declare -f release_helper_probe_log release_helper_probe_render \
    release_helper_probe_guidance probe_release_helper_drift
  printf '%s\n' 'probe_release_helper_drift node account target'
}

#: The same check, judged against the release about to be CUT rather than the one the
#: node runs now. release.sh asks "will the root helper accept this candidate?", and the
#: node's current tree cannot answer that: on 2026-10-16 v1.40.1 changed update_trust.py,
#: the fix that restored its bytes could not be released because the check kept comparing
#: the helper with v1.40.1. The candidate's sources (and its renderer) travel inside the
#: script as a tar on stdin, unpack into a private temporary directory that is removed on
#: exit, and render with the node's own config — nothing outside that directory is written.
release_helper_probe_candidate_script() { # <git-repo> <commit>
  local repo="$1" commit="$2" payload
  payload="$(set -o pipefail
    git -C "$repo" archive --format=tar "$commit" -- automation configs/node.example.toml \
      | gzip -9 | base64 -w0)" || return 1
  declare -f release_helper_probe_log release_helper_probe_render \
    release_helper_probe_guidance probe_release_helper_drift
  printf '%s\n' 'candidate="$(mktemp -d)" || exit 70' \
    "trap 'rm -rf -- \"\$candidate\"' EXIT" \
    "base64 -d <<'CANDIDATE' | tar -xzf - -C \"\$candidate\" || exit 70" \
    "$payload" \
    'CANDIDATE' \
    'HEALTHCHECK_RELEASE_SOURCE_ROOT="$candidate" probe_release_helper_drift node account target'
}
