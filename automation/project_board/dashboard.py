"""The project dashboard as one self-contained HTML page.

One file serves two places: the loopback server (``mode="served"`` — checkboxes record
progress, sources link to the original mail and attachments) and the daily digest
attachment (``mode="file"`` — read-only, no links, opens offline). Nothing is fetched
from outside: no fonts, no CDN, inline CSS and a few lines of inline script.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape
from typing import Final, Literal
from urllib.parse import quote

from .models import OPEN_STATUSES, BoardSnapshot, ProjectView, SourceRow, TodoRow

Mode = Literal["served", "file"]

_STATUS_CLASS: Final = {"열림": "st-open", "진행": "st-doing", "완료": "st-done", "보류": "st-held"}

_CSS: Final = """
:root{--bg:#f5f5f7;--card:#fff;--text:#1d1d1f;--text2:#515154;--line:rgba(0,0,0,.09);
--accent:#0071e3;--red:#b3001b;--red-bg:#fde8ea;--orange:#9a3c00;--orange-bg:#fff0dc;
--green:#1b6b34;--green-bg:#e3f5e8;--gray-bg:#ececef;--r:12px}
*{box-sizing:border-box}
html{scroll-padding-top:64px;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,
"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;letter-spacing:-.2px}
a{color:#0058b0}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:4px}
header{position:sticky;top:0;z-index:5;background:rgba(245,245,247,.92);backdrop-filter:blur(12px);
border-bottom:1px solid var(--line)}
.bar{max-width:1180px;margin:0 auto;padding:10px 18px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.bar h1{font-size:17px;margin:0;font-weight:700}
nav{display:flex;gap:4px;flex-wrap:wrap}
nav a{padding:5px 11px;border-radius:999px;color:var(--text);text-decoration:none;font-size:14px}
nav a:hover{background:var(--gray-bg)}
main{max-width:1180px;margin:0 auto;padding:18px 18px 64px}
section{margin-top:30px}
h2{font-size:21px;margin:0 0 12px}
h3{font-size:16px;margin:18px 0 8px}
.muted{color:var(--text2)} .small{font-size:13px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{background:var(--card);border-radius:var(--r);padding:10px 14px;min-width:120px;border:1px solid var(--line)}
.chip b{display:block;font-size:24px;line-height:1.2}
.chip.over b{color:var(--red)} .chip.soon b{color:var(--orange)}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:14px 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.prog{height:8px;background:var(--gray-bg);border-radius:999px;overflow:hidden;margin:8px 0 4px}
.prog i{display:block;height:100%;background:#34a853}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.d-over{background:var(--red-bg);color:var(--red)} .d-soon{background:var(--orange-bg);color:var(--orange)}
.d-far,.d-none{background:var(--gray-bg);color:var(--text2)}
.st-open{background:var(--gray-bg);color:var(--text)} .st-doing{background:#e1ecfb;color:#0b4a8f}
.st-done{background:var(--green-bg);color:var(--green)} .st-held{background:var(--gray-bg);color:var(--text2)}
.pending{border-left:4px solid var(--orange)}
ul.todos{list-style:none;margin:0;padding:0}
ul.todos li{display:flex;gap:10px;align-items:flex-start;padding:10px 0;border-top:1px solid var(--line)}
ul.todos li:first-child{border-top:0}
ul.todos input{width:20px;height:20px;margin-top:2px;flex:none;accent-color:var(--accent)}
.todo-main{flex:1;min-width:0}
.todo-title{font-weight:600;overflow-wrap:anywhere}
li.done .todo-title{text-decoration:line-through;color:var(--text2)}
.meta{display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:13px;color:var(--text2);margin-top:3px}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.filters input,.filters select{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:#fff}
.filters input{flex:1;min-width:160px}
.agenda h3{display:flex;gap:8px;align-items:center}
.agenda ul{list-style:none;margin:0;padding:0}
.agenda li{padding:8px 0;border-top:1px solid var(--line);display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.agenda li:first-child{border-top:0}
.src{margin-bottom:10px}
.src .names{font-size:13px;color:var(--text2);overflow-wrap:anywhere}
.empty{padding:28px;text-align:center;color:var(--text2)}
.note{font-size:13px;color:var(--text2);margin:6px 0 0}
@media (max-width:600px){.bar{padding:8px 12px}main{padding:12px 12px 48px}.chip{min-width:calc(50% - 4px)}
h2{font-size:19px}}
"""

_SERVED_JS: Final = """
document.querySelectorAll('input[data-todo]').forEach(function(box){
  box.addEventListener('change',function(){
    var token=document.querySelector('meta[name=pb-token]').content;
    var body='status='+encodeURIComponent(box.checked?'완료':'열림');
    box.disabled=true;
    fetch('/todo/'+encodeURIComponent(box.dataset.todo),{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json','X-PB-Token':token},
      body:body}).then(function(r){if(!r.ok){throw new Error(r.status);}location.reload();})
      .catch(function(){box.checked=!box.checked;box.disabled=false;alert('기록하지 못했습니다');});
  });
});
"""

_FILTER_JS: Final = """
(function(){
  var q=document.getElementById('todo-q'),s=document.getElementById('todo-s'),
      c=document.getElementById('todo-count'),rows=document.querySelectorAll('ul.todos li');
  function apply(){
    var text=q.value.trim().toLowerCase(),want=s.value,shown=0;
    rows.forEach(function(li){
      var ok=(!want||li.dataset.status===want)&&(!text||li.textContent.toLowerCase().indexOf(text)>=0);
      li.hidden=!ok;if(ok){shown++;}
    });
    document.querySelectorAll('.todo-group').forEach(function(g){
      g.hidden=!g.querySelector('li:not([hidden])');
    });
    c.textContent=shown+' / '+rows.length+' 건';
  }
  q.addEventListener('input',apply);s.addEventListener('change',apply);apply();
})();
"""


def _kst(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp
    return (moment + timedelta(hours=9)).strftime("%m-%d %H:%M") + " KST" if moment.tzinfo else stamp


def _dday(due: str | None, today: date) -> tuple[str, str]:
    if not due:
        return "미정", "d-none"
    delta = (date.fromisoformat(due) - today).days
    if delta < 0:
        return f"D+{-delta} 지연", "d-over"
    if delta == 0:
        return "D-day", "d-soon"
    return f"D-{delta}", "d-soon" if delta <= 7 else "d-far"


def _bucket(due: str | None, today: date) -> str:
    if not due:
        return "미정"
    day = date.fromisoformat(due)
    week_end = today + timedelta(days=6 - today.weekday())
    if day < today:
        return "지연"
    if day <= week_end:
        return "이번 주"
    if day <= week_end + timedelta(days=7):
        return "다음 주"
    return "이후"


def _source_href(mode: Mode, kind: str, source_id: str) -> str | None:
    return None if mode == "file" else f"/source/{quote(kind, safe='')}/{quote(source_id, safe='')}"


def _summary(snapshot: BoardSnapshot, today: date) -> str:
    open_todos = [t for t in snapshot.todos if t.status in OPEN_STATUSES]
    overdue = sum(1 for t in open_todos if _bucket(t.due, today) == "지연")
    week = sum(1 for t in open_todos if _bucket(t.due, today) == "이번 주")
    doing = sum(1 for t in snapshot.todos if t.status == "진행")
    chips = (
        f'<div class="chip over"><span class="muted small">지연</span><b>{overdue}</b></div>'
        f'<div class="chip soon"><span class="muted small">이번 주 마감</span><b>{week}</b></div>'
        f'<div class="chip"><span class="muted small">진행 중</span><b>{doing}</b></div>'
        f'<div class="chip"><span class="muted small">검토 대기</span><b>{len(snapshot.pending)}</b></div>'
    )
    pending = ""
    if snapshot.pending:
        items = []
        for p in snapshot.pending:
            title = escape(p.project)
            link = (
                f' · <a href="{escape(p.thread_url, quote=True)}">승인 스레드</a>' if p.thread_url else ""
            )
            items.append(
                f"<li><b>{title}</b> · 할 일 {p.todo_count}건 · 근거 「{escape(p.source_subject)}」{link}</li>"
            )
        pending = (
            '<div class="card pending" style="margin-top:12px"><b>소유자 승인을 기다리는 제안</b>'
            '<p class="note">아직 프로젝트 데이터가 아닙니다 — 카드에서 ✅ 하면 반영됩니다.</p>'
            f'<ul class="small">{"".join(items)}</ul></div>'
        )
    return (
        '<section id="summary"><h2>요약</h2>'
        f'<div class="chips">{chips}</div>{pending}'
        f'<p class="note">기준일 {escape(snapshot.today)} · 생성 {escape(_kst(snapshot.generated_at))}</p></section>'
    )


def _project_card(view: ProjectView, today: date) -> str:
    percent = round(100 * view.done / view.total) if view.total else 0
    owner = f'<div class="muted small">담당 {escape(view.owner)}</div>' if view.owner else ""
    overdue = f' <span class="badge d-over">지연 {view.overdue}</span>' if view.overdue else ""
    if view.next_due:
        label, css = _dday(view.next_due, today)
        upcoming = f'다음 마감 {escape(view.next_due)} <span class="badge {css}">{label}</span>'
    else:
        upcoming = "다음 마감 없음"
    return (
        f'<div class="card"><b>{escape(view.name)}</b>{owner}'
        f'<div class="prog" role="img" aria-label="진척 {percent}%"><i style="width:{percent}%"></i></div>'
        f'<div class="small">{view.done}/{view.total} 완료{overdue}</div>'
        f'<div class="small muted" style="margin-top:4px">{upcoming}</div></div>'
    )


def _projects(snapshot: BoardSnapshot, today: date) -> str:
    cards = "".join(_project_card(view, today) for view in snapshot.projects)
    body = f'<div class="grid">{cards}</div>' if cards else '<div class="card empty">아직 승인된 프로젝트가 없습니다.</div>'
    return f'<section id="projects"><h2>프로젝트</h2>{body}</section>'


def _todo_item(todo: TodoRow, mode: Mode, today: date) -> str:
    done = todo.status == "완료"
    label, css = _dday(todo.due, today) if not done else ("완료", "d-far")
    box = (
        f'<input type="checkbox" data-todo="{escape(todo.todo_id, quote=True)}"'
        f'{" checked" if done else ""} aria-label="완료 표시: {escape(todo.title, quote=True)}">'
        if mode == "served"
        else f'<input type="checkbox" disabled{" checked" if done else ""} aria-label="완료 여부">'
    )
    href = _source_href(mode, todo.source_kind, todo.source_id)
    subject = escape(todo.source_subject or todo.source_id)
    source = f'<a href="{escape(href, quote=True)}">근거: {subject}</a>' if href else f"근거: {subject}"
    owner = f"<span>{escape(todo.owner)}</span>" if todo.owner else '<span>담당 미정</span>'
    due = f"<span>{escape(todo.due)}</span>" if todo.due else ""
    progress = f"<span>{todo.progress}%</span>" if todo.status == "진행" else ""
    note = f'<div class="note">{escape(todo.note)}</div>' if todo.note else ""
    done_class = ' class="done"' if done else ""
    return (
        f'<li id="todo-{escape(todo.todo_id, quote=True)}" data-status="{escape(todo.status, quote=True)}"'
        f"{done_class}>{box}<div class=\"todo-main\">"
        f'<div class="todo-title">{escape(todo.title)}</div><div class="meta">'
        f'<span class="badge {_STATUS_CLASS.get(todo.status, "st-open")}">{escape(todo.status)}</span>'
        f'<span class="badge {css}">{label}</span>{owner}{due}{progress}<span>{source}</span>'
        f"</div>{note}</div></li>"
    )


def _todos(snapshot: BoardSnapshot, mode: Mode, today: date) -> str:
    if not snapshot.todos:
        return '<section id="todos"><h2>할 일</h2><div class="card empty">승인된 할 일이 없습니다.</div></section>'
    groups: dict[str, list[TodoRow]] = defaultdict(list)
    for todo in snapshot.todos:
        groups[todo.project].append(todo)
    blocks = []
    for project in sorted(groups):
        items = sorted(groups[project], key=lambda t: (t.status == "완료", t.due or "9999", t.todo_id))
        blocks.append(
            f'<div class="todo-group card" style="margin-bottom:10px"><h3 style="margin-top:0">{escape(project)}</h3>'
            f'<ul class="todos">{"".join(_todo_item(t, mode, today) for t in items)}</ul></div>'
        )
    options = "".join(f'<option value="{s}">{s}</option>' for s in ("열림", "진행", "완료", "보류"))
    note = (
        '<p class="note">체크하면 완료로 기록됩니다(다시 풀면 열림).</p>'
        if mode == "served"
        else '<p class="note">읽기 전용 사본입니다 — 진행 기록은 로컬 대시보드나 명령으로 남깁니다.</p>'
    )
    filters = (
        '<div class="filters"><input id="todo-q" type="search" placeholder="검색 (제목·담당·프로젝트)" '
        'aria-label="할 일 검색"><select id="todo-s" aria-label="상태 필터"><option value="">전체 상태</option>'
        f'{options}</select><span id="todo-count" class="small muted"></span></div>'
    )
    return f'<section id="todos"><h2>할 일</h2>{filters}{note}{"".join(blocks)}</section>'


def _agenda(snapshot: BoardSnapshot, today: date) -> str:
    buckets: dict[str, list[TodoRow]] = defaultdict(list)
    for todo in snapshot.todos:
        if todo.status in OPEN_STATUSES:
            buckets[_bucket(todo.due, today)].append(todo)
    parts = []
    for name in ("지연", "이번 주", "다음 주", "이후", "미정"):
        rows = sorted(buckets.get(name, []), key=lambda t: (t.due or "9999", t.project))
        if not rows:
            continue
        items = []
        for todo in rows:
            label, css = _dday(todo.due, today)
            items.append(
                f'<li><span class="badge {css}">{label}</span><b>{escape(todo.title)}</b>'
                f'<span class="small muted">{escape(todo.project)}'
                f'{" · " + escape(todo.owner) if todo.owner else ""}'
                f'{" · " + escape(todo.due) if todo.due else ""}</span></li>'
            )
        parts.append(f'<div class="card" style="margin-bottom:10px"><h3 style="margin-top:0">{name} '
                     f'<span class="badge d-none">{len(rows)}</span></h3><ul>{"".join(items)}</ul></div>')
    body = "".join(parts) or '<div class="card empty">마감이 남은 할 일이 없습니다.</div>'
    return f'<section id="schedule" class="agenda"><h2>일정</h2>{body}</section>'


def _source_card(row: SourceRow, produced: list[TodoRow], mode: Mode) -> str:
    href = _source_href(mode, row.source_kind, row.source_id)
    subject = escape(row.source_subject or row.source_id)
    title = f'<a href="{escape(href, quote=True)}">{subject}</a>' if href else subject
    kind = "기관 메일" if row.source_kind == "mail" else "회의록"
    files = []
    for name in row.attachments:
        if href:
            files.append(f'<a href="{escape(href + "/attachment/" + quote(name, safe=""), quote=True)}">'
                         f"{escape(name)}</a>")
        else:
            files.append(escape(name))
    attachments = f'<div class="names">첨부: {", ".join(files)}</div>' if files else ""
    todos = ", ".join(escape(t.title) for t in produced) or "없음"
    sender = f" · {escape(row.source_sender)}" if row.source_sender else ""
    return (
        f'<div class="card src"><div class="small muted">{escape(row.source_date[:10])} · {kind}{sender} · '
        f'{escape(row.project)}</div><b>{title}</b><p class="small" style="margin:6px 0">{escape(row.summary)}</p>'
        f'{attachments}<div class="names">만든 할 일: {todos}</div></div>'
    )


def _sources(snapshot: BoardSnapshot, mode: Mode) -> str:
    by_proposal: dict[str, list[TodoRow]] = defaultdict(list)
    for todo in snapshot.todos:
        by_proposal[todo.proposal_id].append(todo)
    cards = "".join(_source_card(row, by_proposal[row.proposal_id], mode) for row in snapshot.sources)
    body = cards or '<div class="card empty">승인된 근거가 없습니다.</div>'
    return f'<section id="sources"><h2>근거</h2>{body}</section>'


def _events(snapshot: BoardSnapshot) -> str:
    if not snapshot.events:
        return ""
    kinds = {"approved": "승인 반영", "progress": "진행 기록"}
    items = "".join(
        f'<li><span class="muted">{escape(_kst(e.at))}</span> · {kinds.get(e.kind, escape(e.kind))} · '
        f"{escape(e.project)} · {escape(e.detail)}</li>"
        for e in snapshot.events[:15]
    )
    return f'<section id="events"><h2>최근 기록</h2><div class="card"><ul class="small">{items}</ul></div></section>'


def render_dashboard(snapshot: BoardSnapshot, *, mode: Mode, token: str = "") -> str:
    today = date.fromisoformat(snapshot.today)
    empty = ""
    if not snapshot.todos and not snapshot.pending:
        empty = (
            '<div class="card empty" style="margin-top:18px">아직 데이터가 없습니다. 메일·회의록 분석 제안을 '
            "소유자가 승인(✅)하면 프로젝트와 할 일이 여기에 나타납니다.</div>"
        )
    meta = f'<meta name="pb-token" content="{escape(token, quote=True)}">' if mode == "served" else ""
    scripts = _FILTER_JS + (_SERVED_JS if mode == "served" else "")
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"{meta}<title>프로젝트 대시보드</title><style>{_CSS}</style></head><body>"
        '<header><div class="bar"><h1>프로젝트 대시보드</h1><nav aria-label="섹션">'
        '<a href="#summary">요약</a><a href="#projects">프로젝트</a><a href="#todos">할 일</a>'
        '<a href="#schedule">일정</a><a href="#sources">근거</a></nav></div></header><main>'
        f"{empty}{_summary(snapshot, today)}{_projects(snapshot, today)}{_todos(snapshot, mode, today)}"
        f"{_agenda(snapshot, today)}{_sources(snapshot, mode)}{_events(snapshot)}</main>"
        f"<script>{scripts if snapshot.todos else ''}</script></body></html>"
    )
