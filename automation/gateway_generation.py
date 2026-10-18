"""Which release generation the running Hermes gateway imported its plugins from.

``record`` runs inside a plugin's ``register`` and writes
``~/.hermes/gateway-generation.json`` only from the gateway main process
(``hermes ... gateway run`` whose pid systemd reports as ``SYSTEMD_EXEC_PID``):
``discover_plugins()`` also runs in CLI, oneshot and web processes and those
must not overwrite the gateway's record. It never raises; a gateway must not
lose its gate because the record could not be written.

Hermes loads the selected memory provider (``memory.provider``) lazily, when the
first agent is built, so its ``register`` cannot write the record at start. The
record therefore also carries the hash of that provider's home plugin file as it
stood when the gateway started; the gateway imports that same file later, and a
file replaced afterwards no longer matches.

``check`` compares that record with the live pidfile, the release a deploy
expects and the installed plugin files. Anything it cannot read is
``unknown``; a missing or mismatching record is ``stale``; only a full match
is ``ok``. CLI: ``python3 -m automation.gateway_generation --check
--runtime-root <path> [--require <plugin>]...`` prints one
``GATEWAY-GENERATION`` line and exits 0 (ok), 1 (stale) or 2 (unknown).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

RECORD_NAME: Final = "gateway-generation.json"
PIDFILE_NAME: Final = "gateway.pid"
EXIT_CODES: Final = {"ok": 0, "stale": 1, "unknown": 2}

Result = tuple[str, str, str, str]


def _import_root() -> str:
    return str(Path(__file__).resolve().parents[1])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_gateway_process(argv: Sequence[str], env: Mapping[str, str], pid: int) -> bool:
    if "gateway" not in argv[1:3]:
        return False
    systemd_pid = env.get("SYSTEMD_EXEC_PID")
    return systemd_pid is None or systemd_pid == str(pid)


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _positive_pid(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _previous_plugins(target: Path, pid: int) -> dict[str, str]:
    try:
        stored = _read_json(target)
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict) or stored.get("pid") != pid:
        return {}
    plugins = stored.get("plugins")
    if not isinstance(plugins, dict):
        return {}
    return {str(name): str(digest) for name, digest in plugins.items()}


def _write_private(target: Path, payload: dict[str, object]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{RECORD_NAME}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _memory_provider_plugin(home: Path) -> tuple[str, Path] | None:
    """The home plugin file of the configured memory provider, if there is one."""
    try:
        from automation.memory_provider.config import read_profile

        name = read_profile((home / ".hermes" / "config.yaml").read_text(encoding="utf-8")).provider
    except Exception:  # noqa: BLE001 - an unreadable config only leaves the provider unrecorded (stale)
        return None
    if name is None:
        return None
    path = home / ".hermes" / "plugins" / name / "__init__.py"
    return (name, path) if path.is_file() else None


def _record(plugin: str, plugin_file: str, *, home: Path, argv: Sequence[str], env: Mapping[str, str], pid: int) -> None:
    if not _is_gateway_process(argv, env, pid):
        return
    target = home / ".hermes" / RECORD_NAME
    plugins = _previous_plugins(target, pid)
    plugins[plugin] = _sha256(Path(plugin_file))
    provider = _memory_provider_plugin(home)
    if provider is not None:
        plugins[provider[0]] = _sha256(provider[1])
    _write_private(target, {
        "pid": pid,
        "import_root": _import_root(),
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plugins": plugins,
    })


def record(plugin: str, plugin_file: str) -> None:
    """Record ``plugin`` for the current gateway process; never raises."""
    try:
        _record(plugin, plugin_file, home=Path.home(), argv=sys.argv, env=os.environ, pid=os.getpid())
    except Exception as error:  # the gate must stay registered whatever happens here
        print(f"GATEWAY-GENERATION-RECORD-FAIL plugin={plugin} error={type(error).__name__}", file=sys.stderr)


def _live_pid(home: Path) -> int | None:
    try:
        payload = _read_json(home / ".hermes" / PIDFILE_NAME)
    except (OSError, ValueError):
        return None
    return _positive_pid(payload.get("pid")) if isinstance(payload, dict) else None


def _load_record(home: Path, want: str) -> tuple[dict[str, object] | None, Result | None]:
    try:
        stored = _read_json(home / ".hermes" / RECORD_NAME)
    except FileNotFoundError:
        return None, ("stale", want, "-", "no-record")
    except OSError:
        return None, ("unknown", want, "-", "record-unreadable")
    except ValueError:
        return None, ("stale", want, "-", "record-invalid")
    if (
        not isinstance(stored, dict)
        or _positive_pid(stored.get("pid")) is None
        or not isinstance(stored.get("import_root"), str)
        or not isinstance(stored.get("plugins"), dict)
    ):
        return None, ("stale", want, "-", "record-invalid")
    return stored, None


def _plugin_matches(home: Path, name: str, recorded: Mapping[object, object]) -> bool:
    try:
        installed = _sha256(home / ".hermes" / "plugins" / name / "__init__.py")
    except OSError:
        return False
    return recorded.get(name) == installed


def check(home: Path, runtime_root: Path | str, required: Sequence[str]) -> Result:
    """Return ``(status, want, have, detail)`` for the gateway of ``home``."""
    from automation.hermes_gateways import stopped_by_release

    expected_root = os.path.realpath(runtime_root)
    want = Path(expected_root).name
    if stopped_by_release(home):
        return ("ok", want, want, "stopped-by-release")
    live_pid = _live_pid(home)
    if live_pid is None:
        return ("unknown", want, "-", "pidfile")
    stored, failure = _load_record(home, want)
    if failure is not None or stored is None:
        return failure or ("unknown", want, "-", "record-unreadable")
    import_root = str(stored["import_root"])
    have = Path(import_root).name or "-"
    if stored["pid"] != live_pid:
        return ("stale", want, have, "pid")
    if not os.path.exists(f"/proc/{live_pid}"):
        return ("stale", want, have, "dead")
    if import_root != expected_root:
        return ("stale", want, have, "import-root")
    plugins = stored["plugins"]
    assert isinstance(plugins, dict)
    for name in required:
        if not _plugin_matches(home, name, plugins):
            return ("stale", want, have, f"plugin:{name}")
    return ("ok", want, have, "-")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.gateway_generation")
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--require", action="append", default=[])
    options = parser.parse_args(argv)
    try:
        status, want, have, detail = check(Path.home(), options.runtime_root, options.require)
    except (OSError, ValueError) as error:
        status, want, have, detail = "unknown", "-", "-", f"error:{type(error).__name__}"
    print(f"GATEWAY-GENERATION {status} want={want} have={have} detail={detail}")
    return EXIT_CODES[status]


if __name__ == "__main__":
    raise SystemExit(main())
