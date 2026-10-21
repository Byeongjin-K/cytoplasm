"""스킬 CLI 의 interop 코드 기본 경로: 환경변수 → import 루트 → 옛 부분 사본."""
from __future__ import annotations

import ast
import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_COPIES: Final = (
    "skills/calendar/scripts/calendar_confirm.py",
    "skills/coordination/scripts/coordinate_io.py",
    "skills/meeting/scripts/meeting_cli.py",
    "skills/mail/scripts/triage_confirm.py",
    "automation/scenario_runner.py",
)
_NAME: Final = "_interop_runtime"


def _function(relpath: str) -> ast.FunctionDef:
    tree = ast.parse((_REPO / relpath).read_text(encoding="utf-8"))
    found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == _NAME]
    assert len(found) == 1, relpath
    return found[0]


def _call(relpath: str) -> Path:
    module = ast.Module(body=[_function(relpath)], type_ignores=[])
    namespace: dict[str, object] = {"os": os, "Path": Path}
    exec(compile(ast.fix_missing_locations(module), relpath, "exec"), namespace)
    function = namespace[_NAME]
    assert callable(function)
    result = function()
    assert isinstance(result, Path)
    return result


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("INTEROP_RUNTIME", raising=False)
    return tmp_path


@pytest.mark.parametrize("relpath", _COPIES)
def test_import_root_is_preferred_when_it_holds_the_interop_package(
    home: Path, relpath: str
) -> None:
    (home / ".hermes" / "autophagy-import" / "automation" / "interop").mkdir(parents=True)
    assert _call(relpath) == home / ".hermes" / "autophagy-import"


@pytest.mark.parametrize("relpath", _COPIES)
def test_the_legacy_runtime_is_used_until_the_import_root_exists(
    home: Path, relpath: str
) -> None:
    assert _call(relpath) == home / ".hermes" / "interop_runtime"


@pytest.mark.parametrize("relpath", _COPIES)
def test_an_import_root_without_the_interop_package_is_ignored(
    home: Path, relpath: str
) -> None:
    (home / ".hermes" / "autophagy-import" / "automation").mkdir(parents=True)
    assert _call(relpath) == home / ".hermes" / "interop_runtime"


@pytest.mark.parametrize("relpath", _COPIES)
def test_the_environment_variable_always_wins(
    home: Path, relpath: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    (home / ".hermes" / "autophagy-import" / "automation" / "interop").mkdir(parents=True)
    monkeypatch.setenv("INTEROP_RUNTIME", "~/elsewhere")
    assert _call(relpath) == home / "elsewhere"
    monkeypatch.setenv("INTEROP_RUNTIME", "relative/runtime")
    assert _call(relpath) == Path("relative/runtime")


@pytest.mark.parametrize("relpath", _COPIES)
def test_an_empty_environment_variable_is_treated_as_unset(
    home: Path, relpath: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTEROP_RUNTIME", "")
    assert _call(relpath) == home / ".hermes" / "interop_runtime"


def test_the_five_copies_are_identical() -> None:
    dumps = {ast.dump(_function(relpath)) for relpath in _COPIES}
    assert len(dumps) == 1


# --- 호출부 결합: 각 파일이 실제로 sys.path 에 넣는 경로가 판정 함수의 결과여야 한다 ---

_CALENDAR_INPUT: Final = "skills/calendar/scripts/calendar_confirm_input.py"
_ALL_CALLERS: Final = (*_COPIES, _CALENDAR_INPUT)
_CHANNEL: Final = ("123",)
#: interop 런타임을 sys.path 에 설치하는 모든 함수와 그것을 부르는 인자(네트워크 전송은 하지 않는다).
_CALLS: Final = {
    "skills/calendar/scripts/calendar_confirm.py": {"_origin_notice": (), "_thread_transport": _CHANNEL},
    _CALENDAR_INPUT: {"_adapter": ()},
    "skills/coordination/scripts/coordinate_io.py": {"ensure_runtime": ()},
    "skills/meeting/scripts/meeting_cli.py": {"_origin_notice": (), "_transport": _CHANNEL},
    "skills/mail/scripts/triage_confirm.py": {
        "_origin_notice": (), "_dm_transport": _CHANNEL, "_adapter": (),
    },
}
_PARAMS: Final = tuple((p, f, a) for p, calls in _CALLS.items() for f, a in calls.items())
#: interop 런타임이 아닌 경로를 sys.path 에 넣는 자리 — (함수, 인자 식) 으로 그 자리에만 허용한다.
_OTHER_INSERTS: Final = {
    "skills/meeting/scripts/meeting_cli.py": {
        ("<module>", "str(Path(__file__).resolve().parent)"), ("_publish_note", "root"),
        ("_correct_terms", "root"), ("_discord_api", "str(_interop_runtime())"),
        ("_hermes_kanban", "str(runtime_root())"),
    },
    "skills/coordination/scripts/coordinate_io.py": {("calendar_scripts", "str(path)")},
}
_STUBS: Final = {
    "__init__.py": "",
    "notice_bot.py": (_REPO / "automation/notice_bot.py").read_text(),
    "node_config.py": (_REPO / "automation/node_config.py").read_text(),
    "node.example.toml": (_REPO / "configs/node.example.toml").read_text(),
    "owner_notice.py": "def notify_owner(content):\n    return True\n",
    "interop/__init__.py": "",
    "interop/origin_notice.py": "",
    "interop/injection_adapter.py": "",
    "interop/discord_transport.py": (
        "class DiscordTransport:\n    def __init__(self, **fields):\n        self.fields = fields\n"
    ),
}


def _is_automation(name: str) -> bool:
    return name == "automation" or name.startswith("automation.")


@pytest.fixture
def isolated_imports() -> Iterator[None]:
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    yield
    sys.path[:] = saved_path
    for name in [n for n in sys.modules if n not in saved_modules]:
        del sys.modules[name]
    sys.modules.update(saved_modules)


@pytest.fixture
def import_root(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "1")
    root = home / ".hermes" / "autophagy-import" / "automation"
    for name, text in _STUBS.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root.parent


def _enter(relpath: str) -> ModuleType:
    """Load the real module, then hide the repository's own ``automation`` package."""
    scripts = (_REPO / relpath).parent
    sys.path.insert(0, str(scripts))
    module = importlib.import_module(Path(relpath).stem)
    if relpath == _CALENDAR_INPUT:
        importlib.import_module("calendar_confirm")
    for name in [n for n in sys.modules if _is_automation(n)]:
        del sys.modules[name]
    sys.path[:] = [entry for entry in sys.path if entry != str(_REPO)]
    return module


@pytest.mark.usefixtures("isolated_imports")
@pytest.mark.parametrize(("relpath", "function", "arguments"), _PARAMS)
def test_the_caller_itself_installs_exactly_the_selected_import_root(
    import_root: Path, relpath: str, function: str, arguments: tuple[str, ...]
) -> None:
    caller = getattr(_enter(relpath), function)
    assert callable(caller)
    before = list(sys.path)
    caller(*arguments)
    assert sys.path == [str(import_root), *before]
    loaded = [m for n, m in sys.modules.items() if _is_automation(n)]
    assert len(loaded) > 1
    assert all(m.__file__ and Path(m.__file__).is_relative_to(import_root) for m in loaded)


@pytest.mark.usefixtures("isolated_imports")
def test_scenario_runner_hands_the_selected_import_root_to_its_child(
    import_root: Path, tmp_path: Path
) -> None:
    from automation import scenario_runner

    environment = scenario_runner._environment(str(tmp_path), tmp_path)
    assert environment["INTEROP_RUNTIME"] == str(import_root)


def _tree(relpath: str) -> ast.Module:
    return ast.parse((_REPO / relpath).read_text(encoding="utf-8"))


def _is_helper_call(node: ast.expr) -> bool:
    return ast.unparse(node) in {f"{_NAME}()", f"confirm.{_NAME}()"}


def _insert_argument(node: ast.AST) -> str | None:
    """The path expression a ``sys.path.insert(0, <path>)`` call installs, else None."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "insert"
        and ast.unparse(node.func.value) == "sys.path"
    ):
        return ast.unparse(node.args[1])
    return None


def _sites(node: ast.AST, scope: str = "<module>") -> Iterator[tuple[str, str]]:
    for child in ast.iter_child_nodes(node):
        argument = _insert_argument(child)
        if argument is not None:
            yield scope, argument
        yield from _sites(child, child.name if isinstance(child, ast.FunctionDef) else scope)


def _functions(tree: ast.Module) -> list[ast.FunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


def _runtime_writes(function: ast.FunctionDef) -> list[ast.AST]:
    binders = (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr, ast.For)
    return [
        n
        for n in ast.walk(function)
        if isinstance(n, binders)
        and any(
            isinstance(x, ast.Name) and x.id == "runtime"
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
            for x in ast.walk(t)
        )
    ]


@pytest.mark.parametrize("relpath", _ALL_CALLERS)
def test_the_only_interop_installers_are_the_ones_exercised_above(relpath: str) -> None:
    sites = list(_sites(_tree(relpath)))
    installs = [scope for scope, argument in sites if argument == "str(runtime)"]
    assert sorted(installs) == sorted(set(_CALLS.get(relpath, {}))), relpath
    others = {site for site in sites if site[1] != "str(runtime)"}
    assert others == _OTHER_INSERTS.get(relpath, set()), relpath


@pytest.mark.parametrize("relpath", _ALL_CALLERS)
def test_an_installed_runtime_is_bound_once_to_the_shared_decision(relpath: str) -> None:
    installers = set(_CALLS.get(relpath, {}))
    for function in _functions(_tree(relpath)):
        if function.name not in installers:
            continue
        writes = _runtime_writes(function)
        assert len(writes) == 1, (relpath, function.name)
        assert isinstance(writes[0], ast.Assign), (relpath, function.name)
        assert _is_helper_call(writes[0].value), (relpath, function.name)


@pytest.mark.parametrize("relpath", _ALL_CALLERS)
def test_the_decision_is_called_and_nothing_else_reads_the_setting(relpath: str) -> None:
    tree = _tree(relpath)
    inside = {id(n) for f in _functions(tree) if f.name == _NAME for n in ast.walk(f)}
    keys = {id(k) for d in ast.walk(tree) if isinstance(d, ast.Dict) for k in d.keys if k}
    stray = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and id(n) not in inside
        and id(n) not in keys
        and n.value in {"INTEROP_RUNTIME", "~/.hermes/interop_runtime", "~/.hermes/autophagy-import"}
    ]
    assert stray == []
    calls = (n for f in _functions(tree) if f.name != _NAME for n in ast.walk(f))
    assert any(isinstance(n, ast.expr) and _is_helper_call(n) for n in calls)


def _imports_interop(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        names = [node.module or "", *(f"{node.module}.{a.name}" for a in node.names)]
    elif isinstance(node, ast.Import):
        names = [a.name for a in node.names]
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        names = [node.value]
    else:
        return False
    return any(n == "automation.interop" or n.startswith("automation.interop.") for n in names)


def _scope_nodes(tree: ast.Module, scope: str) -> list[ast.AST]:
    if scope == "<module>":
        inner = {id(n) for f in _functions(tree) for n in ast.walk(f)}
        return [n for n in ast.walk(tree) if id(n) not in inner]
    return [n for f in _functions(tree) if f.name == scope for n in ast.walk(f)]


@pytest.mark.parametrize("relpath", tuple(_OTHER_INSERTS))
def test_a_non_interop_insertion_site_never_imports_interop(relpath: str) -> None:
    tree = _tree(relpath)
    for scope, _ in _OTHER_INSERTS[relpath]:
        assert not any(_imports_interop(n) for n in _scope_nodes(tree, scope)), (relpath, scope)
