from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"
_REGISTRY = _REPO / "automation" / "healthcheck_registry.sh"


def test_healthcheck_registry_defines_the_shared_check_configuration() -> None:
    # Given: the healthcheck entrypoint and its extracted registry path.
    assert _REGISTRY.is_file()
    assert 'source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_registry.sh"' in _HEALTHCHECK.read_text(
        encoding="utf-8"
    )

    # When: the registry is sourced in its own Bash process.
    result = subprocess.run(
        ("bash", "-c", f'source "{_REGISTRY}"; declare -p LIVE_CHECKS LOCAL_PROBES'),
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: both configuration values are exported to the caller.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "declare -ar LIVE_CHECKS=" in result.stdout
    assert 'declare -r LOCAL_PROBES="' in result.stdout


# --------------------------------------------------------------------------- #
# Which probes this installation actually runs.
# --------------------------------------------------------------------------- #
#
# The registry named one installation's whole service inventory as a constant, so an
# install that does not run report-hub still got its three probes and they could only
# fail — `user_unit_active` against units that were never installed (2026-09-07,
# third-party single-node install; docs/troubleshooting/신규-노드-설치-공백.md §5).
# The deploy checkout is a one-way mirror, so the operator could not edit them out
# either; the only remedy available to them was to stop trusting the sweep.
#
# The direction of the default is the whole safety argument. Declaring nothing must
# keep every probe: a node whose environment was never updated has to keep monitoring
# exactly what it monitored yesterday, because a sweep that quietly checks less still
# reports ALL_HEALTHY. Narrowing is therefore something an installation opts INTO, and
# a name that is not a known group is refused rather than dropped — the same reason
# `automation/install/components.py:resolve_components` refuses unknown component names.


def _emit(services: str | None = None, prelude: str = "") -> subprocess.CompletedProcess[str]:
    """Source the registry exactly as healthcheck.sh does, and print what it decided.

    Strict mode and the real node configuration are part of the contract, not scenery:
    the sweep runs `set -euo pipefail`, so a gate that returns false at statement level
    would abort it, and every row expands `$NODE_*` values that only the config bridge
    provides. A lenient harness would pass while production died.
    """
    script = (
        "set -euo pipefail\n"
        'eval "$(python3 automation/node_config_sh.py --print-env)"\n'
        'PRIMARY_NODE="$NODE_PRIMARY_NODE_NAME"\n'
        'RAG_NODE="$NODE_RAG_NODE_NAME"\n'
        "PEER_GATEWAY_CONFIG=/pinned/peer-gateway-config.json\n"
        f"{prelude}\n"
        f'source "{_REGISTRY}"\n'
        'printf "%s\\n" "${LIVE_CHECKS[@]}"\n'
    )
    environment = {
        key: value for key, value in os.environ.items() if key != "HEALTHCHECK_SERVICES"
    }
    if services is not None:
        environment["HEALTHCHECK_SERVICES"] = services
    return subprocess.run(
        ("bash", "-c", script),
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def _rows(result: subprocess.CompletedProcess[str]) -> tuple[str, ...]:
    assert result.returncode == 0, result.stdout + result.stderr
    rows = tuple(line for line in result.stdout.splitlines() if line)
    assert rows, "the registry emitted nothing: a sweep with no probes reports ALL_HEALTHY"
    return rows


def test_an_installation_without_report_hub_is_not_probed_for_it() -> None:
    # Given: the probes an installation running everything would get.
    every = _rows(_emit())
    assert not [row for row in every if "report-hub" in row]

    # When: the installation declares the groups it actually runs.
    declared = _rows(_emit("core rag"))

    # Then: exactly the undeclared probes are gone and the survivors keep their ORDER.
    # Order is not cosmetic here — the allowlist manifest is printed in array order and
    # the wrapper's inputs digest hashes the array, so a reordering filter would make
    # every existing node report wrapper drift.
    assert list(declared) == [row for row in every if "report-hub" not in row]


def test_declaring_neither_optional_group_leaves_only_the_core_probes() -> None:
    # Given: the full set, and each optional group derived from the REGISTRY ITSELF.
    # Asking the node configuration a second time let the harness and its expectations
    # disagree about which node names were in play — under a config whose rag node is
    # named differently, the expectation kept rows the registry had already dropped.
    every = _rows(_emit())
    without_report_hub = _rows(_emit("core rag"))
    without_rag = _rows(_emit("core report-hub"))
    report_hub = [row for row in every if row not in without_report_hub]
    rag = [row for row in every if row not in without_rag]
    assert not report_hub and rag

    # When: the installation declares neither optional group.
    core = _rows(_emit("core"))

    # Then: both drop out together, core survives in order, and the three groups account
    # for every row — a filter that lost a row belonging to none of them would show here.
    optional = set(report_hub) | set(rag)
    assert list(core) == [row for row in every if row not in optional]
    assert len(core) + len(optional) == len(every)


def test_an_unknown_service_name_is_refused_rather_than_ignored() -> None:
    # Given: an operator who typed the group name with an underscore.
    result = _emit("core report_hub")

    # Then: the sweep stops and says which name it did not know. Ignoring it would drop
    # report-hub's probes while the operator believed they had asked for them.
    assert result.returncode == 2, result.stdout + result.stderr
    assert "report_hub" in result.stderr


def test_a_declaration_written_across_lines_is_not_truncated() -> None:
    # Given: a declaration an operator spread over two lines, which is ordinary in a
    # shell profile or a systemd EnvironmentFile.
    every = _rows(_emit())
    multiline = _rows(_emit("core\nrag"))

    # Then: the later line counts. Reading only the first one would monitor less than
    # the operator asked for and still exit 0 — the precise silence this option exists
    # to remove, so it must not be reintroduced by how the value is parsed.
    assert list(multiline) == list(_rows(_emit("core rag")))
    assert len(multiline) == len(every)


def test_an_unknown_name_on_a_later_line_is_still_refused() -> None:
    # Given: the same typo, but after a newline instead of a space.
    result = _emit("core\nreport_hub")

    # Then: validation sees it. A parser that stopped at the first line would accept
    # this and quietly emit only core's probes.
    assert result.returncode == 2, result.stdout + result.stderr
    assert "report_hub" in result.stderr


def test_an_undeclared_environment_keeps_every_probe() -> None:
    # Given: a node whose environment predates this option, and one that blanks it.
    default = _rows(_emit())
    blank = _rows(_emit(""))
    explicit = _rows(_emit("core report-hub rag"))

    # Then: both keep the full set. An empty declaration is an absent one, never "none":
    # the failure this guards against is monitoring less while reporting healthy.
    assert list(default) == list(explicit)
    assert list(blank) == list(explicit)
    assert not [row for row in default if "report-hub" in row]


def test_the_record_format_the_other_readers_parse_is_unchanged() -> None:
    # Given: healthcheck.sh, the allowlist generator and the wrapper all split each row
    # into five fields with a trailing absorber.
    rows = _rows(_emit())
    for row in rows:
        assert row.count("|") == 4, row


def test_a_callers_separators_cannot_dissolve_an_unknown_name() -> None:
    # Given: a caller whose IFS treats `*` as a separator — legal, and not this file's
    # business — declaring a group name that does not exist.
    result = _emit("core * rag", prelude="IFS=$' \\t\\n*'")

    # Then: the registry splits on its own separators, so the unknown name is still a name
    # it must refuse. Inheriting the caller's IFS turned it into punctuation and emitted a
    # narrowed set in silence, which is the failure this option exists to prevent.
    assert result.returncode == 2, result.stdout + result.stderr
    assert "*" in result.stderr


def test_a_caller_with_no_separators_still_reads_the_declaration() -> None:
    # Given: the opposite abuse — an empty IFS, which would make the whole value one word.
    result = _emit("core rag", prelude="IFS=")

    # Then: the declaration is still understood, so binding the separators did not trade
    # one caller-dependent behaviour for another.
    assert list(_rows(result)) == list(_rows(_emit("core rag")))


def _node_names() -> tuple[str, str]:
    """Primary and RAG node names, read through the SAME bridge the harness sources.

    Reading them any other way is how an expectation and the emitted rows came to disagree
    once already: `node_config_sh.py` honours HEALTHCHECK_NODE_CONFIG_PATH and a direct
    `load_node_config()` does not.
    """
    printed = subprocess.run(
        ("python3", "automation/node_config_sh.py", "--print-env"),
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    values = dict(line.split("=", 1) for line in printed.splitlines() if "=" in line)
    return (
        shlex.split(values["NODE_PRIMARY_NODE_NAME"])[0],
        shlex.split(values["NODE_RAG_NODE_NAME"])[0],
    )


def test_each_group_holds_the_probes_that_belong_to_it() -> None:
    """Membership, not merely self-consistency.

    Expectations built by differencing the registry against itself agree with whatever the
    registry does, so a row filed into the wrong group keeps every such expectation intact
    while sending a probe to an installation that does not run that service. This decides
    membership from the row's own fields instead: a RAG probe is one whose node field is
    the RAG node, and a report-hub probe names report-hub.
    """
    _, rag_node = _node_names()
    every = _rows(_emit())
    rag = [row for row in every if row.split("|")[2] == rag_node]
    report_hub = [row for row in every if "report-hub" in row.split("|")[0]]
    core = [row for row in every if row not in rag and row not in report_hub]
    assert (len(core), len(report_hub), len(rag)) == (11, 0, 5), (
        len(core), len(report_hub), len(rag)
    )

    # Then: each declaration emits exactly the rows that belong to what it declared.
    assert list(_rows(_emit("core"))) == core
    assert list(_rows(_emit("core rag"))) == [row for row in every if row not in report_hub]
    assert list(_rows(_emit("core report-hub"))) == [row for row in every if row not in rag]


def test_a_declaration_that_cannot_be_read_is_refused_not_ignored() -> None:
    """The failure the swallowed status used to hide.

    `read -d ''` reports EOF with a non-zero status even after a complete parse, so the
    obvious `|| true` also swallowed genuine failures: with descriptors exhausted the read
    failed and every declaration, valid or not, quietly became the FULL set — a sweep
    monitoring more than asked is harmless, but the same silence would hide the opposite.
    Descriptor exhaustion is the one failure that can be provoked deterministically.
    """
    result = _emit("core", prelude="ulimit -n 5")

    # Then: nothing is emitted and the caller is stopped, rather than handed a set nobody
    # asked for. The exact status is 2 from the registry's own refusal.
    assert result.returncode != 0, result.stdout
    assert not [line for line in result.stdout.splitlines() if line], result.stdout
