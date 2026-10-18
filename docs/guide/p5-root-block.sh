#!/usr/bin/env bash
# Owner workstation only. No new root helper or sudoers permission is installed.
set -euo pipefail
mode="${1:---preview}"
[[ "$mode" == --preview || "$mode" == --dry-run ]] || { echo 'usage: bash p5-root-block.sh [--dry-run]' >&2; exit 2; }
host="$(python3 - <<'PY'
import pathlib, re, tomllib
config = tomllib.loads((pathlib.Path.home() / ".hermes/node.toml").read_text())
host = config["deploy_ssh_host"]
if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host):
    raise SystemExit("deploy_ssh_host must be an SSH alias")
print(host)
PY
)"
preview() {
  printf '%s\n' \
    '1. peer 홈을 private_root/retired/peer-<UTC>/home.tar(root 0600)에 백업; 크기·tar -tf 개수·sha256·tar --compare 검증.' \
    '2. loginctl disable-linger peer; Linger=no 재조회.' \
    '3. HARD TO UNDO: 백업 재검증 뒤 userdel -r peer; 계정·홈 부재 재조회.' \
    '4. /etc/autophagy/peer-attest-peer.pub·peer-read sudoers·peers.yaml 백업; 남는 sudoers 집합 visudo 검사 뒤 제거; peer 봇 항목만 제거·재조회.' \
    '실패 시 즉시 단계 이름과 함께 중지. Discord 포털의 봇·토큰은 변경하지 않음.'
}
root_script() {
  cat <<'ROOT'
set -euo pipefail
umask 077
step=preflight
trap 'printf "P5-ROOT-FAIL step=%s line=%s\n" "$step" "$LINENO" >&2' ERR
[[ "$EUID" == 0 ]]
mapfile -t cfg < <(python3 - <<'PY'
import pathlib, re, tomllib
c = tomllib.loads(pathlib.Path("/etc/autophagy/node.toml").read_text())
for k in ("peer_account", "peer_home", "private_root"):
    v = c[k]
    if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", v):
        raise SystemExit("unsafe node configuration")
    print(v)
PY
)
[[ "${#cfg[@]}" == 3 ]]
peer="${cfg[0]}"; home="${cfg[1]}"; private="${cfg[2]}"
[[ "$peer" != root && "$home" == /home/* && "$home" != /home/ && "$home" != *..* ]]
cmp -s "$private/peer-retired" <(printf retired)
python3 -c 'import yaml'
getent passwd "$peer" >/dev/null
[[ "$(getent passwd "$peer" | cut -d: -f6)" == "$home" && -d "$home" && ! -L "$home" ]]
backup="$private/retired/peer-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -m 700 -p "$backup"
chown root:root "$backup"
getent passwd "$peer" > "$backup/passwd"
id -G "$peer" > "$backup/groups"
loginctl show-user "$peer" --property=Linger --value > "$backup/linger"
cp -p /etc/autophagy/peers.yaml "$backup/peers.yaml"
cmp -s /etc/autophagy/peers.yaml "$backup/peers.yaml"
for f in "/etc/autophagy/peer-attest-$peer.pub" /etc/sudoers.d/autophagy-healthcheck-peer-read; do
  [[ -f "$f" && ! -L "$f" ]]
  cp -p "$f" "$backup/$(basename "$f")"
  cmp -s "$f" "$backup/$(basename "$f")"
done
step=backup-peer-home
tar -cpf "$backup/home.tar" -C "$(dirname "$home")" "$(basename "$home")"
chmod 600 "$backup/home.tar"
sync -f "$backup/home.tar"
[[ "$(stat -c %U:%G:%a "$backup/home.tar")" == root:root:600 ]]
[[ "$(stat -c %s "$backup/home.tar")" -gt 0 ]]
count="$(tar -tf "$backup/home.tar" | wc -l)"
[[ "$count" -gt 0 ]]
tar --compare -f "$backup/home.tar" -C "$(dirname "$home")"
(cd "$backup" && sha256sum home.tar > home.tar.sha256 && sha256sum -c home.tar.sha256)
printf 'backup=%s size=%s members=%s\n' "$backup" "$(stat -c %s "$backup/home.tar")" "$count"
# Prepare the complete remaining sudoers tree BEFORE removing the real entry.
step=validate-remaining-sudoers
mkdir -m 700 "$backup/sudoers.d"
cp -a /etc/sudoers.d/. "$backup/sudoers.d/"
rm -- "$backup/sudoers.d/autophagy-healthcheck-peer-read"
python3 - "$backup" <<'PY'
import pathlib, sys
b = pathlib.Path(sys.argv[1])
text = pathlib.Path("/etc/sudoers").read_text()
if "/etc/sudoers.d" not in text:
    raise SystemExit("cannot identify sudoers include directory")
(b / "sudoers").write_text(text.replace("/etc/sudoers.d", str(b / "sudoers.d")))
PY
visudo -c -f "$backup/sudoers"
step=disable-linger
loginctl disable-linger "$peer"
[[ ! -e "/var/lib/systemd/linger/$peer" ]]
# When the last user manager exits, loginctl no longer has a user object. The
# linger file is authoritative; if an object remains it must explicitly say no.
linger="$(loginctl show-user "$peer" --property=Linger --value 2>/dev/null || true)"
[[ -z "$linger" || "$linger" == no ]]
step=delete-peer-account-HARD-TO-UNDO
(cd "$backup" && sha256sum -c home.tar.sha256)
[[ "$(tar -tf "$backup/home.tar" | wc -l)" == "$count" ]]
printf '%s\n' 'HARD TO UNDO: userdel -r; best-effort rollback only.'
userdel -r "$peer"
status=0
getent passwd "$peer" >/dev/null || status=$?
[[ "$status" == 2 ]]
[[ ! -e "$home" ]]
step=remove-attestation-key
rm -- "/etc/autophagy/peer-attest-$peer.pub"
[[ ! -e "/etc/autophagy/peer-attest-$peer.pub" ]]
step=remove-peer-read-sudoers
rm -- /etc/sudoers.d/autophagy-healthcheck-peer-read
[[ ! -e /etc/sudoers.d/autophagy-healthcheck-peer-read ]]
visudo -c
step=remove-peer-bot-registry-entry
python3 - "$peer" <<'PY'
import os, pathlib, sys, yaml
p = pathlib.Path("/etc/autophagy/peers.yaml")
raw = yaml.safe_load(p.read_text())
peers = raw["peers"]
matches = [k for k, v in peers.items() if v.get("account") == sys.argv[1]]
if len(matches) != 1:
    raise SystemExit("expected exactly one peer bot entry")
del peers[matches[0]]
t = p.with_name(p.name + ".p5-tmp")
mode = p.stat().st_mode & 0o777
t.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
os.chmod(t, mode)
os.replace(t, p)
again = yaml.safe_load(p.read_text())
if any(v.get("account") == sys.argv[1] for v in again["peers"].values()):
    raise SystemExit("peer bot entry remains")
PY
step=rollback-instructions
IFS=: read -r name password uid gid gecos oldhome shell < "$backup/passwd"
printf '\n%s\n' 'Rollback (owner root; peer account restore is HARD TO UNDO and best-effort):'
printf '1. sha256sum -c %q/home.tar.sha256 (run from %q)\n' "$backup" "$backup"
printf '2. Original linger=%s; if yes: loginctl enable-linger %q\n' "$(tr -d '\n' < "$backup/linger")" "$peer"
printf '3. Best-effort: groupadd -g %q %q # only if original group is absent\n' "$gid" "$peer"
printf '   useradd -u %q -g %q -d %q -s %q -c %q %q\n' "$uid" "$gid" "$oldhome" "$shell" "$gecos" "$peer"
printf '   tar -xpf %q/home.tar -C %q # restores original numeric ownership\n' "$backup" "$(dirname "$home")"
printf '   Restore supplementary groups listed in %q/groups; review UID/GID conflicts first.\n' "$backup"
printf '4. cp -p %q/peer-attest-%s.pub /etc/autophagy/; cp -p %q/peers.yaml /etc/autophagy/peers.yaml\n' "$backup" "$peer" "$backup"
printf '   install -m 0440 %q/autophagy-healthcheck-peer-read /etc/sudoers.d/; visudo -c\n' "$backup"
printf 'P5-ROOT-COMPLETE backup=%s\n' "$backup"
ROOT
}
preview
if [[ "$mode" == --dry-run ]]; then
  # Read-only transport probe, easily tested with a fake ssh; no sudo or uploads.
  ssh -T "$host" 'printf "P5-ROOT-DRY-RUN: connection only; no changes\n"'
  exit 0
fi
printf '대상 SSH 별칭: %s. 위 단계를 실행하려면 yes: ' "$host"
IFS= read -r answer
[[ "$answer" == yes ]] || { echo '취소됨'; exit 1; }
# Stage as the operator before asking sudo on an actual TTY. Piping a script directly
# through ssh -t sudo bash -s can feed script text to the password prompt.
remote="$(root_script | ssh -T "$host" 'umask 077; f=$(mktemp /tmp/p5-owner.XXXXXXXX.sh); cat > "$f"; printf "%s\n" "$f"')"
[[ "$remote" == /tmp/p5-owner.*.sh && "$remote" != *[!A-Za-z0-9_./-]* ]]
trap 'ssh -T "$host" "rm -f -- $remote" </dev/null' EXIT
ssh -t "$host" "sudo bash $remote"
