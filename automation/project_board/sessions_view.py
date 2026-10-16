"""The 세션 tab: the last seven days of a workstation's sessions as an org chart, bars and folded groups.

Phone first: the overview and every group hang off a left spine (on wider screens the overview
fans out top-down), the daily bars are rows of day, bar and count so the labels never shrink, and
each group is a fold whose heading already carries its count and status in words.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from html import escape
from typing import Final

from .dashboard_theme import icon
from .sessions import Agent, Lane, Service, Sessions, SessionsState

_KST: Final = timezone(timedelta(hours=9))
_DAY_MS: Final = 86_400_000
STALE_MS: Final = 45 * 60_000
_WEEKDAYS: Final = "월화수목금토일"

# status key -> (words, badge class); the badge never relies on its colour alone
_STATUS: Final = {
    "done": ("완료", "st-done"), "run": ("진행", "st-doing"), "review": ("확인 대기", "grade"),
    "hold": ("멈춤", "d-soon"), "fail": ("미완", "d-over"), "live": ("가동 중", "st-doing"),
    "ok": ("실패 없음", "st-done"), "warn": ("실패 있음", "d-soon"), "ended": ("종료", "st-open"),
    "unknown": ("기록 없음", "st-open"),
}
_WORST_FIRST: Final = ("fail", "hold", "warn", "run", "review", "live", "ok", "done", "ended", "unknown")
_LANE_ORDER: Final = ("done", "run", "review", "hold", "fail")
_PEAK: Final = ' class="peak"'

_DEPTS: Final = (
    ("ops", "에이전트 운영", "wrench"), ("assistant", "업무 비서·메일", "mail"), ("research", "연구·실험", "flask"),
    ("web", "웹·대시보드", "layout"), ("study", "조사·문서", "book"), ("infra", "장비·인프라", "cpu"),
    ("etc", "기타", "plus"),
)
_PLATFORMS: Final = {"claude": "클로드 코드", "senpi": "센피", "codex": "코덱스", "opencode": "오픈코드"}
_FAMILY_ORDER: Final = ("Opus 5.5", "Sonnet 5.5", "Fable 5.1", "Haiku", "기타 모델")
_WINDOWS: Final = (("lead", "리드 창", "sessions"), ("lane", "레인 작업 창", "layers"), ("proj", "프로젝트 창", "window"))
_SERVICE_GROUPS: Final = (("watch", "상시 감시", "eye"), ("web", "웹 서비스", "globe"), ("timer", "예약·자동 실행", "clock"))

EMPTY_SESSIONS: Final = (
    f'<div class="card empty">{icon("sessions")}<b>아직 비어 있습니다</b>'
    "<p>작업 컴퓨터가 세션 집계를 보내면 이곳에 지난 7일 동안 돈 레인, 에이전트 세션, 서비스가 채워집니다.</p></div>"
)
FILE_SESSIONS: Final = (
    f'<div class="card empty">{icon("sessions")}<b>이 사본에는 싣지 않습니다</b>'
    "<p>세션 현황은 테일넷 대시보드의 세션 탭에서만 보입니다.</p></div>"
)
UNREADABLE_SESSIONS: Final = (
    f'<div class="callout">{icon("alert")}<div><b>세션 집계를 읽지 못했습니다</b>'
    "<p class=\"note\">받은 파일의 형식이 맞지 않습니다. 작업 컴퓨터가 다음 집계를 보내면 다시 채워집니다.</p></div></div>"
)


@dataclass(frozen=True, slots=True)
class _Member:
    label: str
    count: str
    st: str
    detail: str
    when: str
    foot: str
    tag: str


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC).astimezone(_KST)


def _day(ms: int) -> str:
    t = _at(ms)
    return f"{t.month}월 {t.day}일"


def _clock(ms: int) -> str:
    return _at(ms).strftime("%H:%M")


def _wk(ms: int) -> str:
    return _WEEKDAYS[_at(ms).weekday()]


def _span(start: int, end: int | None) -> str:
    if end is None:
        return f"{_day(start)} {_clock(start)}부터 진행 중"
    a, b = _at(start), _at(end)
    tail = _clock(end) if a.date() == b.date() else (f"{b.day}일 {_clock(end)}" if a.month == b.month else f"{_day(end)} {_clock(end)}")
    return f"{_day(start)} {_clock(start)}부터 {tail}까지"


def _n(value: int) -> str:
    return f"{value:,}"


def _dur(ms: int) -> str:
    minutes = round(ms / 60_000)
    if minutes < 60:
        return f"{minutes}분"
    hours, rest = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}시간 {rest}분" if rest else f"{hours}시간"
    days, hours = divmod(hours, 24)
    return f"{days}일 {hours}시간" if hours else f"{days}일"


def _hours(ms: int) -> str:
    return f"{round(ms / 60_000)}분" if ms < 3_600_000 else f"{_n(round(ms / 3_600_000))}시간"


def _worst(states: Iterable[str]) -> str:
    seen = set(states)
    return next((s for s in _WORST_FIRST if s in seen), "unknown")


def _lane_counts(states: Iterable[str]) -> str:
    counts = Counter(states)
    return ", ".join(f"{_STATUS[s][0]} {_n(counts[s])}" for s in _LANE_ORDER if counts[s])


def _badge(st: str) -> str:
    words, css = _STATUS[st]
    return f'<span class="badge {css}">{words}</span>'


def _lane_members(lanes: list[Lane]) -> list[_Member]:
    themes: dict[str, list[Lane]] = {}
    for lane in lanes:
        themes.setdefault(lane.theme, []).append(lane)
    rows = sorted(themes.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [
        _Member(theme, f"{_n(len(g))}건", _worst(x.st for x in g), _lane_counts(x.st for x in g),
                _span(min(x.start for x in g), None if any(x.end is None for x in g) else max(x.end or 0 for x in g)),
                f"걸린 시간 합계 {_dur(sum(x.dur for x in g))}", "")
        for theme, g in rows
    ]


def _agent_members(agents: list[Agent]) -> list[_Member]:
    known = [f for f in _FAMILY_ORDER if any(a.fam == f for a in agents)]
    others = sorted({a.fam for a in agents} - set(known))
    out = []
    for fam in known + others:
        g = [a for a in agents if a.fam == fam]
        live = sum(a.live for a in g)
        out.append(_Member(
            fam, f"{_n(len(g))}개", "run" if live else "ended",
            f"진행 {_n(live)}, 종료 {_n(len(g) - live)}" if live else "모두 종료",
            _span(min(a.start for a in g), None if live else max(a.end for a in g)),
            f"합계 {_hours(sum(a.dur for a in g))}, 응답 {_n(sum(a.calls for a in g))}회", ""))
    return out


def _service_member(s: Service, w0: int) -> _Member:
    if s.group == "timer":
        st = "unknown" if s.runs is None else ("warn" if s.fails else "ok")
        detail = "실행 기록 없음" if s.runs is None else (f"실패 {_n(s.fails or 0)}회" if s.fails else "")
        when = f"마지막 실행 {_day(s.last)} {_clock(s.last)}" if s.last else "이번 주 실행 기록을 찾지 못했습니다"
        foot = "확인 필요" if s.runs is None else f"7일간 실행 {_n(s.runs)}회"
        return _Member(s.label, s.every or f"{s.units}개", st, detail, when, foot, s.tag)
    start = max(s.start or w0, w0)
    foot = "다시 시작 기록 없음" if s.restarts is None else f"7일간 시작 {_n(s.restarts)}회"
    st = s.st if s.st in _STATUS else "unknown"
    return _Member(s.label, f"{s.units}개", st, "", f"{_day(start)} {_clock(start)}부터 지금까지", foot, s.tag)


def _member(m: _Member) -> str:
    detail = f'<span class="muted">{escape(m.detail)}</span>' if m.detail else ""
    tag = f'<span class="ss-tag">{escape(m.tag)}</span>' if m.tag else ""
    return (
        f'<li class="ss-member"><div class="ss-mhead"><b>{escape(m.label)}</b><span class="ss-count">{escape(m.count)}</span></div>'
        f'<div class="meta">{_badge(m.st)}{detail}{tag}</div>'
        f'<p class="ss-line">{escape(m.when)}</p><p class="ss-line muted">{escape(m.foot)}</p></li>'
    )


def _group(key: str, name: str, glyph: str, count: str, summary: str, members: list[_Member], empty: str = "") -> str:
    states = [m.st for m in members]
    body = (f'<ul class="ss-members">{"".join(_member(m) for m in members)}</ul>' if members
            else f'<p class="note">{escape(empty)}</p>')
    badge = _badge(_worst(states)) if members else ""
    return (
        f'<details class="card fold ss-group" data-fold="ss-{key}" data-count="{len(members)}"><summary>'
        f'<span class="ss-icon">{icon(glyph)}</span><span class="ss-gname"><b>{escape(name)}</b>'
        f'<span class="ss-gsum">{escape(count)}{" · " + escape(summary) if summary else ""}</span></span>{badge}</summary>'
        f"{body}</details>"
    )


def _branch(kind: str, glyph: str, title: str, sub: str, total: int, groups: list[str]) -> str:
    return (
        f'<div class="ss-branch" data-kind="{kind}" data-count="{total}">'
        f'<div class="ss-hub"><span class="ss-icon">{icon(glyph)}</span><span><b>{title}</b>'
        f'<span class="ss-hubsub">{sub}</span></span></div>'
        f'<div class="ss-spine">{"".join(groups)}</div></div>'
    )


def _pillar(glyph: str, label: str, big: str, unit: str, sub: str) -> str:
    return (f'<div class="card ss-pillar"><span class="tile-top">{icon(glyph)}{label}</span>'
            f'<b class="ss-big">{big}<small>{unit}</small></b><p class="ss-line muted">{sub}</p></div>')


def _bars(title: str, unit: str, days: list[int], values: list[int], peak: int) -> str:
    top = max(values + [1])
    rows = "".join(
        f'<li{_PEAK if i == peak else ""}><span class="ss-dday">{_at(t).day}일 {_wk(t)}</span>'
        f'<meter min="0" max="{top}" value="{v}" aria-hidden="true"></meter><span class="ss-dval">{_n(v)}{unit}</span></li>'
        for i, (t, v) in enumerate(zip(days, values, strict=True))
    )
    return f'<figure class="ss-chart"><figcaption>{title}</figcaption><ol class="ss-days">{rows}</ol></figure>'


def _freshness(data: Sessions, now_ms: int) -> str:
    age = max(0, now_ms - data.now)
    # the open page keeps the age current itself (data-ago); data-live keeps it out of the page's ETag
    since = f'<span data-live data-ago="{data.now}" data-ago-unit="dur">{_dur(age)}</span>'
    line = f'<p class="note ss-fresh">{icon("clock")}{_day(data.now)} {_clock(data.now)} 기준, {since} 전에 받은 집계입니다</p>'
    if age <= STALE_MS:
        return line
    return line + (f'<div class="callout">{icon("alert")}<div><b>세션 집계가 {since} 동안 새로 오지 않았습니다</b>'
                   '<p class="note">작업 컴퓨터가 꺼져 있거나 전송이 멈췄을 수 있습니다. 아래 숫자는 마지막으로 받은 집계입니다.</p></div></div>')


def _board(data: Sessions, now_ms: int) -> str:
    lanes, agents, services = list(data.lanes), list(data.agents), list(data.services)
    lane_groups = [
        _group(f"lane-{key}", name, glyph, f"{_n(len(g))}건", _lane_counts(x.st for x in g), _lane_members(g))
        for key, name, glyph in _DEPTS if (g := [x for x in lanes if x.dept == key])
    ]
    platforms = [p for p in _PLATFORMS if any(a.platform == p for a in agents)]
    platforms += sorted({a.platform for a in agents} - set(platforms))
    agent_groups = []
    for p in platforms:
        g = [a for a in agents if a.platform == p]
        live = sum(a.live for a in g)
        agent_groups.append(_group(f"agent-{p}", _PLATFORMS.get(p, p), "sessions", f"{_n(len(g))}개",
                                   f"진행 {_n(live)}" if live else "모두 종료", _agent_members(g)))
    windows = [
        _Member(label, f"{_n(len(g))}개", "live", "", f"{_day(first)} {_clock(first)}부터 지금까지",
                f"가장 오래 켜진 창 {_dur(data.now - first)}", "")
        for kind, label, _ in _WINDOWS if (g := [w for w in data.windows if w.kind == kind]) and (first := min(w.start for w in g))
    ]
    agent_groups.append(_group("agent-windows", "상주 작업 창", "window", f"{_n(len(data.windows))}개", "지금 가동 중", windows,
                               "지금 켜져 있는 작업 창이 없습니다."))
    service_groups = [
        _group(f"svc-{key}", name, glyph, f"{_n(len(g))}종", "", [_service_member(s, data.w0) for s in g])
        for key, name, glyph in _SERVICE_GROUPS if (g := [s for s in services if s.group == key])
    ]
    days = list(range(data.w0, data.now, _DAY_MS))
    lane_by_day = [sum(1 for x in lanes if not x.carried and t <= x.start < t + _DAY_MS) for t in days]
    agent_by_day = [sum(1 for a in agents if a.start > data.w0 and t <= a.start < t + _DAY_MS) for t in days]
    peak = lane_by_day.index(max(lane_by_day))
    carried = sum(x.carried for x in lanes), sum(a.start <= data.w0 for a in agents)
    running = sum(x.st == "run" for x in lanes) + sum(a.live for a in agents)
    total = len(lanes) + len(agents) + len(services)
    overview = (
        f'<div class="ss-branch ss-overview" data-kind="overview" data-count="{total}">'
        f'<div class="ss-hub"><span class="ss-icon">{icon("network")}</span><span><b>지난 7일 모든 세션 {_n(total)}개</b>'
        f'<span class="ss-hubsub">지금 진행 중 {_n(running)}개</span></span></div><div class="ss-spine">'
        + _pillar("layers", "작업 레인", _n(len(lanes)), "건", _lane_counts(x.st for x in lanes) or "기록 없음")
        + _pillar("sessions", "에이전트 세션", _n(len(agents)), "개",
                  f"누적 {_hours(sum(a.dur for a in agents))}, 응답 {_n(sum(a.calls for a in agents))}회, 상주 창 {_n(len(data.windows))}개")
        + _pillar("eye", "감시·서비스", _n(len(services)), "종",
                  f"상시 가동 {_n(sum(s.tag == '상시' for s in services))}종, 예약 실행 {_n(sum(s.runs or 0 for s in services))}회")
        + "</div></div>"
    )
    trend = (
        f'<div class="card ss-trend"><h3>하루별 새로 시작한 일</h3><p class="note">가장 바빴던 날은 {_day(days[peak])}({_wk(days[peak])})로 '
        f"레인 {_n(lane_by_day[peak])}건이 열렸습니다. 기간 전에 시작해 이어진 레인 {_n(carried[0])}건과 에이전트 세션 "
        f'{_n(carried[1])}개는 막대에서 뺐습니다.</p><div class="ss-charts">'
        f'{_bars("새 작업 레인", "건", days, lane_by_day, peak)}{_bars("새 에이전트 세션", "개", days, agent_by_day, peak)}</div></div>'
    )
    return (
        _freshness(data, now_ms) + overview + trend
        + _branch("lanes", "layers", "작업 레인", f"{_n(len(lanes))}건, 맡긴 일을 비슷한 일끼리 묶었습니다", len(lanes), lane_groups)
        + _branch("agents", "sessions", "에이전트 세션", f"{_n(len(agents))}개, 플랫폼과 모델별로 묶었습니다", len(agents), agent_groups)
        + _branch("services", "eye", "감시·서비스", f"{_n(len(services))}종, 사람 없이 도는 감시와 예약 실행입니다", len(services), service_groups)
        + '<p class="note">레인 기록, 코딩 에이전트 세션 기록(시작과 끝 시각, 모델, 응답 수만 쓰고 대화 내용은 읽지 않습니다), '
        "서비스 상태로 셉니다. 걸린 시간은 이 기간 안의 시간만 세고, 에이전트 세션의 누적 시간은 동시에 돈 시간을 모두 더한 값입니다.</p>"
    )


def summary_line(state: SessionsState) -> str:
    if not isinstance(state, Sessions):
        return ""
    running = sum(x.st == "run" for x in state.lanes) + sum(a.live for a in state.agents)
    total = len(state.lanes) + len(state.agents) + len(state.services)
    return (
        f'<a class="card ss-strip" href="#sessions">{icon("sessions")}<span><b>작업 컴퓨터 세션</b>'
        f'<span class="ss-hubsub">지난 7일 {_n(total)}개, 지금 진행 중 {_n(running)}개</span></span>{icon("chevron-right")}</a>'
    )


def render_sessions(state: SessionsState, *, now_ms: int, exported: bool) -> str:
    if exported:
        body, sub = FILE_SESSIONS, "작업 컴퓨터에서 지난 7일 동안 돈 세션"
    elif state == "missing":
        body, sub = EMPTY_SESSIONS, "작업 컴퓨터에서 지난 7일 동안 돈 세션"
    elif state == "unreadable":
        body, sub = UNREADABLE_SESSIONS, "작업 컴퓨터에서 지난 7일 동안 돈 세션"
    else:
        body = _board(state, now_ms)
        sub = (f"작업 컴퓨터에서 지난 7일 동안 돈 세션입니다. {_day(state.w0)}({_wk(state.w0)}) 00:00부터 "
               f"{_day(state.now)}({_wk(state.now)}) {_clock(state.now)}까지, 한국 시간.")
    return (f'<section id="sessions" data-tab="sessions"><div class="head"><div><h2>세션</h2><p class="sub">{sub}</p></div></div>'
            f"{body}</section>")
