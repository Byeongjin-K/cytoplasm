from __future__ import annotations

import textwrap
from pathlib import Path


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def write_fake_sudo(bin_dir: Path) -> None:
    write_executable(
        bin_dir / "sudo",
        r"""
        #!/usr/bin/python3
        import os
        import shutil
        import subprocess
        import sys
        import tarfile
        from pathlib import Path

        arguments = sys.argv[1:]
        if "-u" in arguments:
            account = arguments[arguments.index("-u") + 1]
            command = arguments[arguments.index("-c") + 1]
            if account == "peer" and "ISOLATION-FAIL" in command:
                print("ISOLATION-OK simulated account boundary")
                raise SystemExit(0)
            environment = dict(os.environ)
            environment["HOME"] = environment[f"FAKE_{account.upper()}_HOME"]
            environment["USER"] = account
            raise SystemExit(subprocess.run(("/bin/bash", "-c", command), env=environment).returncode)

        command_index = 1 if arguments and arguments[0] == "-n" else 0
        command = arguments[command_index:]
        # The release convergence a deploy performs now goes through the argument-free
        # privileged helper, so honour that path and let the harness copy answer.
        if command and command[0].endswith("autophagy-converge-origin-main"):
            raise SystemExit(subprocess.run(command).returncode)
        if not command or not command[0].endswith("autophagy-install-skill"):
            raise SystemExit(97)
        skill = command[command.index("--skill") + 1]
        digest = command[command.index("--hash") + 1]
        store = Path(os.environ["FAKE_SKILL_STORE"])
        release = store / "releases" / digest
        if release.exists():
            shutil.rmtree(release)
        release.mkdir(parents=True)
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
            archive.extractall(release)
        live = store / "live" / skill
        live.parent.mkdir(parents=True, exist_ok=True)
        live.unlink(missing_ok=True)
        live.symlink_to(release / skill, target_is_directory=True)
        """,
    )


def write_fake_python(bin_dir: Path) -> None:
    write_executable(
        bin_dir / "python3",
        r"""
        #!/usr/bin/python3
        import json
        import os
        import sys
        from pathlib import Path

        arguments = sys.argv[1:]
        script_index = 1 if arguments and arguments[0] == "-I" else 0
        script = Path(arguments[script_index]).name if len(arguments) > script_index else ""
        tail = arguments[script_index + 1:]

        def option(name: str, default: str = "") -> str:
            return tail[tail.index(name) + 1] if name in tail else default

        if script == "deploy_execution_lock.py":
            print(f"EXECUTION-LOCK-ACQUIRED skill={option('--skill')}", flush=True)
            _ = sys.stdin.read()
            raise SystemExit(0)
        if script in {"peer_attest.py", "skill_review.py"}:
            if script == "peer_attest.py" and os.environ.get("FAKE_PEER_ATTEST_FAIL") == "1":
                raise SystemExit(5)
            print("FAKE-PASS")
            raise SystemExit(0)
        if script == "skill_gate.py":
            command = tail[0]
            tail = tail[1:]
            pending = Path(os.environ["HOME"]) / ".hermes" / "skill-gate" / "pending" / f"{option('--skill')}.json"
            if command == "request":
                provenance = json.loads(Path(option("--provenance-file")).read_text())
                record = {
                    "hash": option("--hash"),
                    "message_id": "message-1",
                    "deploy_nonce": "d" * 32,
                    "personal_head_sha": provenance["personal_head_sha"],
                }
                pending.parent.mkdir(parents=True, exist_ok=True)
                pending.write_text(json.dumps(record))
                Path(os.environ["FAKE_APPROVAL_CAPTURE"]).write_text(json.dumps(record))
                print(json.dumps({"message_id": record["message_id"], "deploy_nonce": record["deploy_nonce"]}))
                raise SystemExit(0)
            if command == "check":
                record = json.loads(pending.read_text())
                provenance = json.loads(Path(option("--provenance-file")).read_text())
                valid = (
                    record["hash"] == option("--hash")
                    and record["message_id"] == option("--message-id")
                    and record["deploy_nonce"] == option("--deploy-nonce")
                    and record["personal_head_sha"] == provenance["personal_head_sha"]
                )
                raise SystemExit(0 if valid and os.environ.get("FAKE_OWNER_PENDING") != "1" else 1)
            if command == "sign":
                Path(option("--out")).write_text("signed")
                raise SystemExit(0)
            if command == "consume":
                pending.unlink(missing_ok=True)
                print("CONSUMED")
                raise SystemExit(0)
            raise SystemExit(96)
        os.execv(os.environ["REAL_PYTHON"], (os.environ["REAL_PYTHON"], *arguments))
        """,
    )
