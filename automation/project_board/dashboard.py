"""The project dashboard as one self-contained HTML page.

One file serves two places: the loopback server (``mode="served"`` — checkboxes record
progress, sources link to the original mail and attachments) and the daily digest
attachment (``mode="file"`` — read-only, no links, opens offline). Nothing is fetched
from outside: no fonts, no CDN, inline CSS and a few lines of inline script.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, timedelta
from html import escape
from typing import Final, Literal
from urllib.parse import quote

from .dashboard_theme import CSS, brand_mark, favicon, icon
from .models import GRADES, NEVER_EXPORTED, OPEN_STATUSES, BoardSnapshot, EventRow, ProjectView, SourceRow, TodoRow

Mode = Literal["served", "file"]

_STATUS_CLASS: Final = {"열림": "st-open", "진행": "st-doing", "완료": "st-done", "보류": "st-held"}

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
document.querySelectorAll('button[data-archive]').forEach(function(b){
  b.addEventListener('click',function(){
    var on=b.dataset.archive==='1',name=b.dataset.archiveProject;
    if(on&&!confirm('「'+name+'」을(를) 보관할까요?\\n요약·할 일·일정·근거에서 사라지고 보관함에서만 보입니다. 할 일과 근거는 지워지지 않습니다.')){return;}
    var token=document.querySelector('meta[name=pb-token]').content;
    b.disabled=true;
    fetch('/project/'+encodeURIComponent(name),{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json','X-PB-Token':token},
      body:'archived='+(on?'1':'0')}).then(function(r){if(!r.ok){throw new Error(r.status);}
        location.hash=on?'archive':'project/'+encodeURIComponent(name);location.reload();})
      .catch(function(){b.disabled=false;alert('기록하지 못했습니다');});
  });
});
"""

_FOLD_JS: Final = """
(function(){
  var key='pb-fold:';
  document.querySelectorAll('details[data-fold]').forEach(function(d){
    try{if(localStorage.getItem(key+d.dataset.fold)==='0'){d.open=false;}}catch(e){}
    d.addEventListener('toggle',function(){
      try{if(d.open){localStorage.removeItem(key+d.dataset.fold);}else{localStorage.setItem(key+d.dataset.fold,'0');}}catch(e){}
    });
  });
})();
"""

_TABS_JS: Final = """
(function(){
  var panels=document.querySelectorAll('main>[data-tab]'),links=document.querySelectorAll('nav a[data-tab]'),
      names=[].map.call(links,function(a){return a.dataset.tab;}),title=document.title,sticky='';
  function dec(s){try{return decodeURIComponent(s);}catch(e){return '';}}
  function parse(hash){
    var q=hash.indexOf('?p=');
    if(hash.indexOf('project/')===0){return {tab:'project',proj:dec(hash.slice(8))};}
    if(q>=0){return {tab:hash.slice(0,q),proj:dec(hash.slice(q+3))};}
    return {tab:hash.indexOf('todo-')===0?'todos':hash,proj:''};
  }
  function pick(panel,proj){
    var sel=panel?panel.querySelector('select[data-picker]'):null;if(!sel){return '';}
    var known=[].some.call(sel.options,function(o){return o.dataset.p===proj;});
    if(!known){proj='';}
    [].forEach.call(sel.options,function(o){if(o.dataset.p===proj){sel.value=o.value;}});
    return proj;
  }
  document.querySelectorAll('select[data-picker]').forEach(function(sel){
    sel.addEventListener('change',function(){location.hash=sel.value;});
  });
  function filterSchedule(proj){
    var panel=document.getElementById('schedule');if(!panel){return;}
    proj=pick(panel,proj);var any=false,chosen=proj;
    [].forEach.call(panel.querySelectorAll('.bucket'),function(card){
      var n=0;[].forEach.call(card.querySelectorAll('li[data-project]'),function(li){
        var ok=!proj||li.dataset.project===proj;li.hidden=!ok;if(ok){n++;}});
      card.hidden=!n;if(n){any=true;}var c=card.querySelector('.cnt');if(c){c.textContent=n;}
    });
    var none=document.getElementById('schedule-none');if(none){none.style.display=any?'none':'';}
    return chosen;
  }
  function filterTodos(proj){
    var panel=document.getElementById('todos');if(!panel){return;}
    var chosen=document.body.dataset.project=pick(panel,proj);
    document.dispatchEvent(new Event('pb-project'));
    return chosen;
  }
  function show(){
    var hash=location.hash.slice(1),r=parse(hash),tab=r.tab,page=null;
    if(tab==='project'){
      panels.forEach(function(s){if(s.dataset.tab==='project'&&s.dataset.project===r.proj){page=s;}});
      if(!page){tab='projects';}
    }else if(names.indexOf(tab)<0){tab='summary';}
    var navTab=page?(page.dataset.archived?'archive':'projects'):tab;
    panels.forEach(function(s){s.hidden=page?s!==page:s.dataset.tab!==tab;});
    links.forEach(function(a){var on=a.dataset.tab===navTab;a.classList.toggle('on',on);
      if(on){a.setAttribute('aria-current','page');}else{a.removeAttribute('aria-current');}});
    var t=filterTodos(tab==='todos'?r.proj:''),s=filterSchedule(tab==='schedule'?r.proj:'');
    if(page){sticky=r.proj;}else if(tab==='todos'){sticky=t||'';}else if(tab==='schedule'){sticky=s||'';}
    links.forEach(function(a){var n=a.dataset.tab;if(n==='todos'||n==='schedule'){
      a.setAttribute('href','#'+n+(sticky?'?p='+encodeURIComponent(sticky):''));}});
    document.title=page?r.proj+' · '+title:title;
    var target=hash.indexOf('todo-')===0&&document.getElementById(hash);
    if(target){target.scrollIntoView({block:'center'});}else{window.scrollTo(0,0);}
  }
  window.addEventListener('hashchange',show);show();
})();
"""

TABS: Final = (
    ("summary", "요약"), ("projects", "프로젝트"), ("todos", "할 일"), ("schedule", "일정"), ("sources", "근거"),
    ("archive", "보관함"),
)
_NAV: Final = "".join(f'<a href="#{tab}" data-tab="{tab}">{icon(tab)}{label}</a>' for tab, label in TABS)
DEFAULT_BRAND: Final = "프로젝트 대시보드"
LOCAL_ACTOR: Final = "노드 로컬 접속"

EMPTY_SECTION: Final = (
    f'<div class="card empty">{icon("inbox")}<b>아직 비어 있습니다</b>'
    "<p>새 메일과 회의록을 분석한 제안이 승인되면 이곳에 할 일과 일정이 채워집니다.</p></div>"
)

_FILTER_JS: Final = """
(function(){
  var q=document.getElementById('todo-q'),s=document.getElementById('todo-s'),
      c=document.getElementById('todo-count'),rows=document.querySelectorAll('#todos ul.todos li');
  function apply(){
    var text=q.value.trim().toLowerCase(),want=s.value,proj=document.body.dataset.project||'',shown=0,total=0;
    rows.forEach(function(li){
      var mine=!proj||li.dataset.project===proj;if(mine){total++;}
      var ok=mine&&(!want||li.dataset.status===want)&&(!text||li.textContent.toLowerCase().indexOf(text)>=0);
      li.hidden=!ok;if(ok){shown++;}
    });
    document.querySelectorAll('#todos .todo-group').forEach(function(g){
      g.hidden=!g.querySelector('li:not([hidden])');
    });
    c.textContent=total+'건 중 '+shown+'건';
  }
  q.addEventListener('input',apply);s.addEventListener('change',apply);
  document.addEventListener('pb-project',apply);apply();
})();
"""

_WEEKDAYS: Final = "월화수목금토일"


def _kst(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp
    if not moment.tzinfo:
        return stamp
    local = moment + timedelta(hours=9)
    return f"{local.month}월 {local.day}일 {local:%H:%M}"


def _md(day: str) -> str:
    try:
        moment = date.fromisoformat(day[:10])
    except ValueError:
        return day
    return f"{moment.month}월 {moment.day}일({_WEEKDAYS[moment.weekday()]})"


def _ago(stamp: str, now: str) -> str:
    try:
        then = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError:
        return stamp
    minutes = int((current - then).total_seconds() // 60)
    if minutes < 1:
        return "방금"
    if minutes < 60:
        return f"{minutes}분 전"
    if minutes < 24 * 60:
        return f"{minutes // 60}시간 전"
    days = (current + timedelta(hours=9)).date() - (then + timedelta(hours=9)).date()
    return "어제" if days.days == 1 else f"{days.days}일 전" if days.days < 7 else _md((then + timedelta(hours=9)).date().isoformat())


def _to(word: str) -> str:
    """The Korean particle 로/으로 after ``word`` (으로 after a final consonant other than ㄹ)."""
    last = word[-1:] or "가"
    code = ord(last) - 0xAC00
    final = code % 28 if 0 <= code < 11172 else 0
    return f"{word}으로" if final and final != 8 else f"{word}로"


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


def _grade_badge(grade: str) -> str:
    """The 업무 security grade; 대외비·기관지정 also say they never leave this screen."""
    css = f"g-{GRADES.index(grade)}" if grade in GRADES else "g-1"
    hint = " · 외부 비공개" if grade in NEVER_EXPORTED else ""
    return f'<span class="badge {css}" title="업무 보안 등급">{escape(grade)}{hint}</span>'


def project_href(name: str, tab: str = "project") -> str:
    """``#project/<name>`` opens one project's page; ``#todos?p=<name>`` filters a tab to it."""
    encoded = quote(name, safe="")
    return f"#project/{encoded}" if tab == "project" else f"#{tab}?p={encoded}"


def project_sort_key(view: ProjectView) -> tuple[str, ...]:
    """The one place that decides project order — cards, the picker and every per-project list follow it.

    Name order (가나다) until the owner settles "주관 순서" (card Q172); change only this function.
    """
    return (view.name,)


def _in_project_order(names: Iterable[str], snapshot: BoardSnapshot) -> list[str]:
    """Group names in the board's project order (``snapshot.projects`` is already sorted)."""
    rank = {view.name: index for index, view in enumerate(snapshot.projects)}
    return sorted(names, key=lambda name: (rank.get(name, len(rank)), name))


def _picker(tab: str, snapshot: BoardSnapshot) -> str:
    """전체 / 프로젝트별 — a dropdown above a tab. Each option is a ``#tab?p=`` address, so back and sharing work."""
    if not snapshot.projects:
        return ""
    options = [f'<option value="#{tab}" data-p="">전체 프로젝트</option>'] + [
        f'<option value="{escape(project_href(view.name, tab), quote=True)}" data-p="{escape(view.name, quote=True)}">'
        f"{escape(view.name)}</option>"
        for view in snapshot.projects
    ]
    return f'<label class="picker">프로젝트<select data-picker aria-label="프로젝트 고르기">{"".join(options)}</select></label>'


def _fold(key: str, title: str, count: int, body: str, *, cls: str = "fold sect") -> str:
    """A list block whose heading folds it; the count stays in the heading, the state is kept per browser."""
    return (
        f'<details class="{cls}" data-fold="{escape(key, quote=True)}" open><summary><h3>{title} '
        f'<span class="badge d-none cnt">{count}</span></h3></summary>{body}</details>'
    )


def _head(title: str, sub: str = "", extra: str = "") -> str:
    lead = f'<p class="sub">{sub}</p>' if sub else ""
    return f'<div class="head"><div><h2>{title}</h2>{lead}</div>{extra}</div>'


def _tile(name: str, label: str, value: str, tone: str, href: str = "") -> str:
    tag, link = ("a", f' href="{href}"') if href else ("div", "")
    css = f" is-{tone}" if tone else ""
    return (f'<{tag} class="tile{css}"{link}><span class="tile-top">{icon(name)}{label}</span>'
            f"<b>{value}</b></{tag}>")


def _fresh_in_shelf(full: BoardSnapshot, shelf: tuple[ProjectView, ...]) -> dict[str, int]:
    """Todos approved into a project after it was archived — counted, so archiving never hides new work silently."""
    counts = {view.name: sum(1 for t in full.todos if t.project == view.name and t.approved_at > view.archived_at)
              for view in shelf}
    return {name: count for name, count in counts.items() if count}


def _nav(shelf: tuple[ProjectView, ...], fresh: dict[str, int]) -> str:
    """The tab bar; 보관함 carries its own counts so archived work is noticed without leaving the shelf."""
    if not shelf:
        return _NAV
    new = sum(fresh.values())
    badges = f' <span class="badge d-none">{len(shelf)}</span>' + (
        f' <span class="badge d-soon">새 {new}</span>' if new else ""
    )
    return _NAV.replace("보관함</a>", f"보관함{badges}</a>")


def _long_day(today: date) -> str:
    return f"{today.year}년 {today.month}월 {today.day}일 {_WEEKDAYS[today.weekday()]}요일"


def _summary(snapshot: BoardSnapshot, today: date) -> str:
    open_todos = [t for t in snapshot.todos if t.status in OPEN_STATUSES]
    overdue = sum(1 for t in open_todos if _bucket(t.due, today) == "지연")
    week = sum(1 for t in open_todos if _bucket(t.due, today) == "이번 주")
    doing = sum(1 for t in snapshot.todos if t.status == "진행")
    waiting = len(snapshot.pending)
    tiles = (
        _tile("alert", "지연", str(overdue), "danger" if overdue else "", "#schedule")
        + _tile("schedule", "이번 주 마감", str(week), "warn" if week else "", "#schedule")
        + _tile("activity", "진행 중", str(doing), "info" if doing else "", "#todos")
        + _tile("inbox", "승인 대기", str(waiting), "info" if waiting else "")
    )
    pending = ""
    if snapshot.pending:
        items = []
        for p in snapshot.pending:
            link = f' · <a href="{escape(p.thread_url, quote=True)}">승인 스레드 열기</a>' if p.thread_url else ""
            items.append(
                f"<li><b>{escape(p.project)}</b> {_grade_badge(p.grade)} · 할 일 {p.todo_count}건 · "
                f"근거 「{escape(p.source_subject)}」{link}</li>"
            )
        pending = (
            f'<div class="callout" id="pending">{icon("inbox")}<div><b>승인을 기다리는 제안 {len(items)}건</b>'
            '<p class="note">아직 할 일로 들어오지 않았습니다. 승인 카드에서 승인하면 반영됩니다.</p>'
            f'<ul class="small">{"".join(items)}</ul></div></div>'
        )
    withheld = (
        f'<p class="note">보안 등급(대외비·기관지정) 할 일 {snapshot.withheld}건은 이 사본에서 뺐습니다.</p>'
        if snapshot.withheld
        else ""
    )
    projects = ""
    if snapshot.projects:
        cards = "".join(_project_card(view, today) for view in snapshot.projects)
        projects = (
            '<div class="section-title"><h3>프로젝트</h3><span class="small muted">눌러서 그 프로젝트만 봅니다</span></div>'
            f'<div class="grid">{cards}</div>'
        )
    return (
        f'<section id="summary" data-tab="summary">{_head("오늘의 현황", "기준일 " + _long_day(today))}'
        f'<div class="tiles">{tiles}</div>{pending}{projects}{withheld}</section>'
    )


def _project_card(view: ProjectView, today: date, note: str = "") -> str:
    percent = round(100 * view.done / view.total) if view.total else 0
    owner = f'<div class="small muted">담당 {escape(view.owner)}</div>' if view.owner else ""
    overdue = f'<span class="badge d-over">지연 {view.overdue}건</span>' if view.overdue else ""
    if view.next_due:
        label, css = _dday(view.next_due, today)
        upcoming = f'<span>다음 마감 {_md(view.next_due)}</span><span class="badge {css}">{label}</span>'
    else:
        upcoming = "" if view.overdue else "<span>남은 마감 없음</span>"
    upcoming_row = f'<div class="facts">{upcoming}</div>' if upcoming else ""
    href = escape(project_href(view.name), quote=True)
    return (
        f'<a class="card pcard" href="{href}" data-project="{escape(view.name, quote=True)}">'
        f'<div class="pcard-top"><b>{escape(view.name)}</b>{icon("chevron-right")}</div>{owner}'
        f'<div class="meter"><progress class="prog" value="{view.done}" max="{max(view.total, 1)}" '
        f'aria-label="진척 {percent}%"></progress><span>{percent}%</span></div>'
        f'<div class="facts"><span>{view.done}/{view.total} 완료</span>{overdue}</div>'
        f"{upcoming_row}{note}</a>"
    )


def _projects(snapshot: BoardSnapshot, today: date) -> str:
    cards = "".join(_project_card(view, today) for view in snapshot.projects)
    body = f'<div class="grid">{cards}</div>' if cards else EMPTY_SECTION
    sub = f"진행 중인 프로젝트 {len(snapshot.projects)}개" if cards else ""
    return f'<section id="projects" data-tab="projects">{_head("프로젝트", sub)}{body}</section>'


def _todo_item(todo: TodoRow, mode: Mode, today: date, *, anchor: bool = True) -> str:
    done = todo.status == "완료"
    name = escape(todo.title, quote=True)
    box = (
        f'<label class="check"><input type="checkbox" data-todo="{escape(todo.todo_id, quote=True)}"'
        f'{" checked" if done else ""} aria-label="완료 표시: {name}"></label>'
        if mode == "served"
        else f'<label class="check"><input type="checkbox" disabled{" checked" if done else ""} aria-label="완료 여부"></label>'
    )
    status = f"{todo.status} {todo.progress}%" if todo.status == "진행" else todo.status
    badges = "" if todo.status == "열림" else f'<span class="badge {_STATUS_CLASS.get(todo.status, "st-open")}">{escape(status)}</span>'
    if not done:
        label, css = _dday(todo.due, today)
        badges += f'<span class="badge {css}">{label}</span>'
    due = f"<span>{_md(todo.due)}</span>" if todo.due else ""
    owner = f"<span>{escape(todo.owner)}</span>" if todo.owner else '<span class="muted">담당 미정</span>'
    href = _source_href(mode, todo.source_kind, todo.source_id)
    subject = escape(todo.source_subject or todo.source_id)
    kind = "mail" if todo.source_kind == "mail" else "sources"
    source = (
        f'<a class="src-link" href="{escape(href, quote=True)}" title="근거 원문 열기">{icon(kind)}<span>{subject}</span></a>'
        if href else f'<span class="src-link">{icon(kind)}<span>{subject}</span></span>'
    )
    note = f'<div class="note">{escape(todo.note)}</div>' if todo.note else ""
    done_class = ' class="done"' if done else ""
    ident = f'id="todo-{escape(todo.todo_id, quote=True)}" ' if anchor else ""
    return (
        f'<li {ident}data-project="{escape(todo.project, quote=True)}" data-status="{escape(todo.status, quote=True)}"'
        f'{done_class}>{box}<div class="todo-main"><div class="todo-title">{escape(todo.title)}</div>'
        f'<div class="meta">{badges}{_grade_badge(todo.grade)}{owner}{due}</div>{source}{note}</div></li>'
    )


def _todo_order(todo: TodoRow) -> tuple[bool, str, str]:
    return (todo.status == "완료", todo.due or "9999", todo.todo_id)


def _todos(snapshot: BoardSnapshot, mode: Mode, today: date) -> str:
    sub = "체크하면 완료로 기록됩니다. 다시 풀면 열림으로 돌아갑니다." if mode == "served" else (
        "읽기 전용 사본입니다. 진행 기록은 대시보드 화면에서 남깁니다."
    )
    if not snapshot.todos:
        return f'<section id="todos" data-tab="todos">{_head("할 일")}{EMPTY_SECTION}</section>'
    groups: dict[str, list[TodoRow]] = defaultdict(list)
    for todo in snapshot.todos:
        groups[todo.project].append(todo)
    blocks = []
    for project in _in_project_order(groups, snapshot):
        items = sorted(groups[project], key=_todo_order)
        heading = f'<a href="{escape(project_href(project), quote=True)}">{escape(project)}</a>'
        body = f'<ul class="todos">{"".join(_todo_item(t, mode, today) for t in items)}</ul>'
        fold = _fold(f"t:{project}", heading, len(items), body, cls="todo-group card fold")
        blocks.append(fold.replace("<details ", f'<details data-project="{escape(project, quote=True)}" ', 1))
    options = "".join(f'<option value="{s}">{s}</option>' for s in ("열림", "진행", "완료", "보류"))
    filters = (
        f'<div class="filters">{_picker("todos", snapshot)}'
        f'<label class="field field-search">검색<span class="control">{icon("search")}'
        '<input id="todo-q" type="search" placeholder="제목, 담당, 프로젝트" autocomplete="off"></span></label>'
        f'<label class="field">상태<select id="todo-s"><option value="">전체 상태</option>{options}</select></label>'
        '<span id="todo-count" class="count" aria-live="polite"></span></div>'
    )
    return f'<section id="todos" data-tab="todos">{_head("할 일", sub)}{filters}{"".join(blocks)}</section>'


def _agenda_cards(
    todos: tuple[TodoRow, ...] | list[TodoRow], today: date, key: str = "s", *, with_project: bool = True
) -> str:
    """Open todos bucketed by due date (지연·이번 주·다음 주·이후·미정), one card per bucket."""
    buckets: dict[str, list[TodoRow]] = defaultdict(list)
    for todo in todos:
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
            where = [escape(todo.project)] if with_project else []
            where += [escape(todo.owner)] if todo.owner else []
            where += [_md(todo.due)] if todo.due else []
            items.append(
                f'<li data-project="{escape(todo.project, quote=True)}"><span class="badge {css}">{label}</span>'
                f'<b>{escape(todo.title)}</b>{_grade_badge(todo.grade)}'
                f'<span class="where">{" · ".join(where)}</span></li>'
            )
        parts.append(_fold(f"{key}:{name}", name, len(rows), f'<ul>{"".join(items)}</ul>', cls="card bucket fold"))
    return "".join(parts)


def _agenda(snapshot: BoardSnapshot, today: date) -> str:
    cards = _agenda_cards(snapshot.todos, today)
    fallback = (
        f'<div class="card empty">{icon("schedule")}<b>남은 마감이 없습니다</b><p>열린 할 일에 모두 마감이 지났거나 끝났습니다.</p></div>'
        if snapshot.todos else EMPTY_SECTION
    )
    none = (
        f'<div id="schedule-none" class="card empty" style="display:none">{icon("schedule")}'
        "<b>이 프로젝트에는 남은 마감이 없습니다</b><p>다른 프로젝트를 고르거나 전체 프로젝트로 돌아가 보세요.</p></div>"
        if cards
        else ""
    )
    body = (cards + none) or fallback
    picker = f'<div class="picker-row">{_picker("schedule", snapshot)}</div>' if snapshot.projects else ""
    head = _head("일정", "열린 할 일을 마감 주별로 묶었습니다.")
    return f'<section id="schedule" data-tab="schedule" class="agenda">{head}{picker}{body}</section>'


def _source_card(row: SourceRow, produced: list[TodoRow], mode: Mode, *, with_project: bool = True) -> str:
    href = _source_href(mode, row.source_kind, row.source_id)
    subject = escape(row.source_subject or row.source_id)
    title = f'<a class="title" href="{escape(href, quote=True)}">{subject}</a>' if href else f'<span class="title">{subject}</span>'
    kind = "기관 메일" if row.source_kind == "mail" else "회의록"
    files = []
    for name in row.attachments:
        if href:
            files.append(f'<a href="{escape(href + "/attachment/" + quote(name, safe=""), quote=True)}">'
                         f"{escape(name)}</a>")
        else:
            files.append(escape(name))
    attachments = f'<div class="names">{icon("paperclip")}<span>{", ".join(files)}</span></div>' if files else ""
    todos = ", ".join(escape(t.title) for t in produced) or "없음"
    top = [_md(row.source_date), kind] + ([escape(row.source_sender)] if row.source_sender else [])
    top += [escape(row.project)] if with_project else []
    return (
        f'<div class="card src-card"><div class="top"><span>{" · ".join(top)}</span>{_grade_badge(row.grade)}</div>'
        f'{title}<p>{escape(row.summary)}</p>{attachments}'
        f'<div class="names">{icon("list")}<span>만든 할 일: {todos}</span></div></div>'
    )


def _sources(snapshot: BoardSnapshot, mode: Mode) -> str:
    by_proposal: dict[str, list[TodoRow]] = defaultdict(list)
    for todo in snapshot.todos:
        by_proposal[todo.proposal_id].append(todo)
    groups: dict[str, list[SourceRow]] = defaultdict(list)
    for row in snapshot.sources:
        groups[row.project].append(row)
    body = "".join(
        _fold(f"e:{project}", escape(project), len(rows),
              '<div class="stack">' + "".join(_source_card(row, by_proposal[row.proposal_id], mode, with_project=False)
                                              for row in rows) + "</div>")
        for project, rows in ((name, groups[name]) for name in _in_project_order(groups, snapshot))
    ) or EMPTY_SECTION
    hint = (
        '<p class="note">원문 메일과 첨부는 이 사본에 없습니다. 대시보드 화면의 근거 탭에서 열립니다.</p>'
        if mode == "file" and snapshot.sources
        else ""
    )
    head = _head("근거", "할 일을 만든 메일과 회의록입니다.")
    return f'<section id="sources" data-tab="sources">{head}{hint}{body}</section>'


_PROGRESS: Final = re.compile(r"^(\S+)→(\S+) (\d+)→(\d+)%(?: · (.*))?$")


def _event_text(event: EventRow, titles: dict[str, str]) -> str:
    """What happened, as a sentence; a progress record's ``열림→완료 0→100%`` becomes ``… 완료로 바꿈``."""
    title = titles.get(event.todo_id, "")
    named = f"「{escape(title)}」 " if title else ""
    if event.kind == "approved":
        return f"할 일 추가 「{escape(event.detail)}」"
    if event.kind == "progress":
        match = _PROGRESS.match(event.detail)
        if not match:
            return f"{named}진행 기록"
        old, new, before, after, note = match.groups()
        text = f"{named}{escape(_to(new))} 바꿈" if old != new else f"{named}진행률 {after}%" if before != after else f"{named}메모"
        return text + (f" · {escape(note)}" if note else "")
    if event.kind == "grade":
        return f"보안 등급 변경 {escape(event.detail)}"
    return {"archive": "프로젝트를 보관함으로 옮김", "unarchive": "보관 해제"}.get(event.kind, escape(event.kind))


def _events(snapshot: BoardSnapshot, mode: Mode) -> str:
    if not snapshot.events:
        return ""
    titles = {todo.todo_id: todo.title for todo in snapshot.todos}

    def who(event: EventRow) -> str:
        # who acted is a login; the file copy may be mailed out, so only the owner's screen shows it
        return f'<div class="who">{escape(_actor_label(event.actor))}</div>' if mode == "served" and event.actor else ""

    items = "".join(
        f'<li><time datetime="{escape(e.at, quote=True)}" title="{escape(_kst(e.at), quote=True)}">'
        f"{_ago(e.at, snapshot.generated_at)}</time><div class=\"what\"><b>{escape(e.project)}</b> · "
        f"{_event_text(e, titles)}{who(e)}</div></li>"
        for e in sorted(snapshot.events, key=lambda event: event.at, reverse=True)[:8]
    )
    return (
        '<section id="events" data-tab="summary"><div class="section-title"><h3>최근 기록</h3></div>'
        f'<div class="card"><ul class="events">{items}</ul></div></section>'
    )


def _actor_label(actor: str) -> str:
    """Who acted, in words a buyer reads: the stored local-access marker (with its address) shows as 「이 기기에서」."""
    return "이 기기에서" if actor.startswith(LOCAL_ACTOR) else actor


def _archived_line(view: ProjectView, mode: Mode) -> str:
    """When (and, on the owner's own screen, by whom) a project was archived."""
    who = f" · {escape(_actor_label(view.archived_by))}" if mode == "served" and view.archived_by else ""
    return f"보관 {escape(_kst(view.archived_at))}{who}"


def _archive(shelf: tuple[ProjectView, ...], mode: Mode, today: date, fresh: dict[str, int] | None = None) -> str:
    """보관함: archived projects only. Their todos and evidence are kept and open from the card."""
    sub = ("보관한 프로젝트는 요약, 프로젝트, 할 일, 일정, 근거와 프로젝트 고르기에 나오지 않습니다. "
           "할 일과 근거는 지워지지 않고, 프로젝트 화면의 「보관 해제」로 되돌립니다.")

    def line(view: ProjectView) -> str:
        count = (fresh or {}).get(view.name, 0)
        flag = f' <span class="badge d-soon">보관 뒤 새 할 일 {count}</span>' if count else ""
        return f'<div class="small muted">{_archived_line(view, mode)}{flag}</div>'

    cards = "".join(_project_card(view, today, line(view)) for view in shelf)
    body = f'<div class="grid">{cards}</div>' if cards else (
        f'<div class="card empty">{icon("archive")}<b>보관한 프로젝트가 없습니다</b>'
        "<p>끝난 프로젝트는 프로젝트 화면의 「보관」으로 이곳에 옮길 수 있습니다.</p></div>"
    )
    return f'<section id="archive" data-tab="archive">{_head("보관함", sub)}{body}</section>'


def _project_page(
    index: int, view: ProjectView, snapshot: BoardSnapshot, mode: Mode, today: date, *, archived: bool = False
) -> str:
    """One project's own screen: its 할 일, 일정 and 근거 only, grade badges kept on every row."""
    todos = [t for t in snapshot.todos if t.project == view.name]
    sources = [s for s in snapshot.sources if s.project == view.name]
    pending = [p for p in snapshot.pending if p.project == view.name]
    grades = sorted({t.grade for t in todos} | {s.grade for s in sources}, key=lambda g: GRADES.index(g) if g in GRADES else 1)
    badges = "".join(_grade_badge(grade) for grade in grades)
    open_todos = [t for t in todos if t.status in OPEN_STATUSES]
    overdue = sum(1 for t in open_todos if _bucket(t.due, today) == "지연")
    week = sum(1 for t in open_todos if _bucket(t.due, today) == "이번 주")
    owner = f'<span class="small muted">담당 {escape(view.owner)}</span>' if view.owner else ""
    tiles = (
        _tile("alert", "지연", str(overdue), "danger" if overdue else "")
        + _tile("schedule", "이번 주 마감", str(week), "warn" if week else "")
        + _tile("todos", "완료", f"{view.done}/{view.total}", "")
        + _tile("inbox", "승인 대기", str(len(pending)), "info" if pending else "")
    )
    items = "".join(_todo_item(t, mode, today, anchor=False) for t in sorted(todos, key=_todo_order))
    todo_block = f'<div class="card"><ul class="todos">{items}</ul></div>' if items else (
        f'<div class="card empty">{icon("todos")}<b>할 일이 없습니다</b><p>이 프로젝트에서 승인된 할 일이 아직 없습니다.</p></div>'
    )
    key = f"p:{view.name}"
    agenda = _agenda_cards(todos, today, f"{key}:s", with_project=False) or (
        f'<div class="card empty">{icon("schedule")}<b>남은 마감이 없습니다</b><p>열린 할 일이 없거나 모두 끝났습니다.</p></div>'
    )
    by_proposal: dict[str, list[TodoRow]] = defaultdict(list)
    for todo in todos:
        by_proposal[todo.proposal_id].append(todo)
    evidence = "".join(_source_card(row, by_proposal[row.proposal_id], mode, with_project=False) for row in sources) or (
        f'<div class="card empty">{icon("sources")}<b>근거가 없습니다</b><p>이 프로젝트에 연결된 메일이나 회의록이 아직 없습니다.</p></div>'
    )
    todo_fold = _fold(key + ":t", "할 일", len(todos), todo_block)
    agenda_fold = _fold(key + ":s", "일정", len(open_todos), '<div class="agenda">' + agenda + "</div>")
    evidence_fold = _fold(key + ":e", "근거 (메일·회의록)", len(sources), '<div class="stack">' + evidence + "</div>")
    name_attr = escape(view.name, quote=True)
    button = (
        f'<button type="button" class="btn" data-archive="{"0" if archived else "1"}" data-archive-project="{name_attr}">'
        f'{"보관 해제" if archived else "보관"}</button>'
        if mode == "served"
        else ""
    )
    back = (f'<a class="back" href="#archive">{icon("chevron-left")}보관함</a>' if archived
            else f'<a class="back" href="#projects">{icon("chevron-left")}전체 프로젝트</a>')
    shelf = (
        f'<div class="card note shelf-note">보관한 프로젝트입니다. 요약, 할 일, 일정, 근거에 나오지 않습니다. '
        f"{_archived_line(view, mode)}</div>"
        if archived
        else ""
    )
    flag = ' data-archived="1"' if archived else ""
    return (
        f'<section id="pv-{index}" data-tab="project" data-project="{name_attr}"{flag}>{back}'
        f'<div class="pv-head"><h2>{escape(view.name)}</h2>{badges}{owner}{button}</div>{shelf}'
        f'<div class="tiles">{tiles}</div>'
        f"{todo_fold}{agenda_fold}{evidence_fold}</section>"
    )


def render_dashboard(snapshot: BoardSnapshot, *, mode: Mode, token: str = "", brand: str = DEFAULT_BRAND) -> str:
    today = date.fromisoformat(snapshot.today)
    shelf = tuple(sorted(snapshot.archived, key=project_sort_key))
    hidden = {view.name for view in shelf}
    full = snapshot
    snapshot = replace(  # the main screens: archived projects and everything under them are left out
        full,
        projects=tuple(sorted(full.projects, key=project_sort_key)),
        todos=tuple(t for t in full.todos if t.project not in hidden),
        sources=tuple(s for s in full.sources if s.project not in hidden),
        events=tuple(e for e in full.events if e.project not in hidden),
    )
    fresh = _fresh_in_shelf(full, shelf)
    empty = ""
    if not snapshot.todos and not snapshot.pending:
        empty = (
            '<div class="card empty" data-tab="summary">아직 데이터가 없습니다. 메일·회의록 분석 제안을 '
            "승인 카드에서 승인하면 프로젝트와 할 일이 여기에 나타납니다.</div>"
        )
    meta = f'<meta name="pb-token" content="{escape(token, quote=True)}">' if mode == "served" else ""
    scripts = _FOLD_JS + _TABS_JS + (_FILTER_JS if snapshot.todos else "") + (_SERVED_JS if mode == "served" else "")
    pages = "".join(
        _project_page(i, view, full, mode, today, archived=view in shelf)
        for i, view in enumerate(snapshot.projects + shelf, 1)
    )
    name = escape(brand or DEFAULT_BRAND)
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'{meta}<title>{name}</title><link rel="icon" href="{favicon(brand or DEFAULT_BRAND)}">'
        f"<style>{CSS}</style></head><body>"
        '<a class="skip" href="#main">본문으로 건너뛰기</a>'
        f'<header><div class="bar"><a class="brand" href="#summary">{brand_mark(brand or DEFAULT_BRAND)}{name}</a>'
        f'<nav aria-label="화면">{_nav(shelf, fresh)}</nav></div></header><main id="main">'
        f"{empty}{_summary(snapshot, today)}{_projects(snapshot, today)}{_todos(snapshot, mode, today)}"
        f"{_agenda(snapshot, today)}{_sources(snapshot, mode)}{_archive(shelf, mode, today, fresh)}{_events(snapshot, mode)}{pages}</main>"
        f'<footer><span>{name}</span><span aria-hidden="true">·</span><span>기준일 {escape(snapshot.today)}</span>'
        f'<span aria-hidden="true">·</span><span>{escape(_kst(snapshot.generated_at))} 생성</span></footer>'
        f"<script>{scripts}</script></body></html>"
    )
