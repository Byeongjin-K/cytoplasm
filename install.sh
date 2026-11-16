#!/usr/bin/env bash
# Installs or updates cytoplasm's default Dori setup on Linux (and macOS), the twin of install.ps1:
#   curl -fsSL https://raw.githubusercontent.com/orientpine/cytoplasm/main/install.sh | bash
# Run the same line again to update. Options are environment variables put in front of `bash`:
#   CYTO_ROLES (okdori,moodori,gongdori)  CYTO_OWNER_CALL  CYTO_HOME (default ~/.cytoplasm)
#   CYTO_REPO  CYTO_TRUST_KEY  CYTO_EXPECT_FINGERPRINT
#
# What it does: finds or installs git, Python 3.11+, unzip, ssh-keygen, bun and omo (the agent each Dori runs; the
# system packages through apt, dnf or brew, which may ask for your password; bun and omo through their own installers,
# per user); clones cytoplasm into <CYTO_HOME>/src; on the first run checks the newest release tag's signature against
# the update trust key whose fingerprint is published in README (nothing from the clone runs before that); then hands
# over to `python3 -m automation.install.update`, which keeps that check for every later update and runs the Dori
# installer (pinned fork, herdr, the user units for the herdr server, its web page and `dori serve`). No root for
# cytoplasm itself.
#
# The whole script is one function called on its last line: under `curl | bash` bash reads the script from stdin, and a
# command that reads stdin (apt, an installer) would otherwise eat the lines that follow it.
set -euo pipefail

cyto_main() {
  local repo="${CYTO_REPO:-https://github.com/orientpine/cytoplasm.git}"
  local root="${CYTO_HOME:-$HOME/.cytoplasm}"
  local expect="${CYTO_EXPECT_FINGERPRINT:-SHA256:4yOZrMFmCSiSryt6+sal+E2hROpMlalgWNUqSf9lqsU}"
  local principal="update-trust@autophagy"
  local src="$root/src" signers="$root/update-allowed-signers"

  step() { printf '== %s\n' "$*"; }
  have() { command -v "$1" >/dev/null 2>&1; }
  needs() { printf 'CYTO-LINUX-NEEDS: %s\n' "$*" >&2; exit 2; }
  fail() { printf 'CYTO-LINUX-FAIL: %s\n' "$*" >&2; exit 1; }
  # system_package <apt name> <dnf name> <brew name> <why>
  system_package() {
    local cmd
    if have apt-get; then cmd=(sudo apt-get install -y "$1")
    elif have dnf; then cmd=(sudo dnf install -y "$2")
    elif have brew; then cmd=(brew install "$3")
    else needs "$4 - install $1 and run this again"; fi
    printf 'Installing %s (%s): %s\n' "$1" "$4" "${cmd[*]}"
    if [ "${cmd[0]}" = sudo ]; then printf '  sudo may ask for your password.\n'; fi
    "${cmd[@]}" </dev/null || needs "$4 - '${cmd[*]}' failed; install $1 and run this again"
  }
  python_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; }

  step "git"
  have git || system_package git git git "git fetches cytoplasm and the Dori fork"
  git --version

  step "Python 3.11+"
  local py=""
  for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
    if have "$candidate" && python_ok "$candidate"; then py="$candidate"; break; fi
  done
  if [ -z "$py" ]; then
    system_package python3 python3 python@3.12 "Python 3.11 or newer runs the installer"
    python_ok python3 && py=python3
  fi
  [ -n "$py" ] || needs "Python 3.11 or newer (this system's python3 is older): install it and run this again"
  "$py" --version

  step "unzip and ssh-keygen (bun's installer unpacks a zip; git checks release signatures with ssh-keygen)"
  have unzip || system_package unzip unzip unzip "bun's installer needs unzip"
  have ssh-keygen || system_package openssh-client openssh-clients openssh "git verify-tag needs ssh-keygen"

  step "bun"
  export PATH="$HOME/.bun/bin:$HOME/.local/bin:$PATH"
  have bun || { curl -fsSL https://bun.sh/install </dev/null | bash; } || fail "bun's installer (https://bun.sh/install)"
  have bun || needs "bun (curl -fsSL https://bun.sh/install | bash), then run this again"
  bun --version

  step "omo (the agent the Dori lead runs in its herdr tab)"
  # cytoplasm and Dori install without omo (the lead tab then waits for it); omo's installer may refuse root
  have omo || { curl -fsSL https://get.omo.dev/install.sh </dev/null | bash; } || true
  have omo || echo "CYTO-LINUX-OMO-SKIP: omo is not installed - the Dori lead tab waits for it. As yourself (not root): curl -fsSL https://get.omo.dev/install.sh | bash, then the command on the DORI-LEAD-PANE line below"

  local update_args=(-m automation.install.update --state "$root")
  if [ ! -d "$src/.git" ] || [ ! -f "$signers" ]; then
    step "first install: newest signed release of $repo"
    if [ ! -d "$src/.git" ]; then
      mkdir -p "$root"
      git clone -q --no-checkout "$repo" "$src" </dev/null || fail "git clone $repo"
    fi
    local work key line algorithm blob actual tag chosen="" said
    work="$(mktemp -d)"
    # shellcheck disable=SC2064 # expand now: the local is gone when the trap fires
    trap "rm -rf '$work'" EXIT
    key="${CYTO_TRUST_KEY:-}"
    if [ -z "$key" ]; then
      key="$work/update-trust.pub"
      curl -fsSL -o "$key" https://github.com/orientpine/cytoplasm/releases/latest/download/update-trust.pub \
        || fail "download of the release's update-trust.pub"
    fi
    line="$(grep -v -e '^#' -e '^[[:space:]]*$' "$key" | head -n 1)"
    algorithm="$(printf '%s' "$line" | awk '{print $1}')"
    blob="$(printf '%s' "$line" | awk '{print $2}')"
    # the SHA256 fingerprint is base64 of the sha256 of the key blob, without padding, exactly as ssh-keygen -lf prints it
    actual="$("$py" -c 'import base64, hashlib, sys; print("SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(sys.argv[1])).digest()).decode().rstrip("="))' "$blob")" \
      || fail "update trust key $key is not a public key line"
    if [ "$actual" != "$expect" ]; then
      fail "CYTO-LINUX-FINGERPRINT-MISMATCH: update trust key $actual is not the published $expect - nothing from the clone was run"
    fi
    echo "CYTO-TRUST-KEY $actual"
    printf '%s namespaces="git" %s %s\n' "$principal" "$algorithm" "$blob" >"$work/allowed_signers"
    for tag in $(git -C "$src" ls-remote --tags --refs origin 'v*' </dev/null | awk '{sub("refs/tags/", "", $2); print $2}' \
                 | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -t. -k1.2,1nr -k2,2nr -k3,3nr); do
      git -C "$src" fetch -q --no-tags origin "+refs/tags/$tag:refs/tags/$tag" </dev/null || fail "git fetch $tag"
      said="$(git -C "$src" -c "gpg.ssh.allowedSignersFile=$work/allowed_signers" verify-tag "$tag" 2>&1 </dev/null)" || said=""
      if printf '%s' "$said" | grep -qF "Good \"git\" signature for $principal"; then chosen="$tag"; break; fi
      echo "CYTO-UNSIGNED-SKIP $tag"
    done
    [ -n "$chosen" ] || fail "CYTO-LINUX-NO-SIGNED-RELEASE: no tag of $repo is signed by the update trust key"
    git -C "$src" checkout -q --detach "refs/tags/$chosen" </dev/null
    echo "CYTO-RELEASE $chosen (signature checked)"
    update_args+=(--trust-key "$key" --expect-fingerprint "$expect")
    local role
    IFS=',' read -r -a roles <<<"${CYTO_ROLES:-}"
    for role in "${roles[@]}"; do
      role="${role// /}"
      [ -z "$role" ] || update_args+=(--role "$role")
    done
    [ -z "${CYTO_OWNER_CALL:-}" ] || update_args+=(--owner-call "$CYTO_OWNER_CALL")
    step "install (python3 -m automation.install.update)"
  else
    step "update (python3 -m automation.install.update)"
  fi
  (cd "$src" && PYTHONUNBUFFERED=1 "$py" "${update_args[@]}" </dev/null) || fail "automation.install.update exit $?"
  echo
  echo "CYTO-LINUX-INSTALLED $src"
  echo "Update later: run the same line again. Open a new terminal so PATH picks up dori, herdr and omo."
}

cyto_main "$@"
