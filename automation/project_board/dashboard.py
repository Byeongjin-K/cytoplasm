"""The project dashboard as one self-contained HTML page.

One file serves two places: the loopback server (``mode="served"`` — checkboxes record
progress, sources link to the original mail and attachments) and the daily digest
attachment (``mode="file"`` — read-only, no links, opens offline). Nothing is fetched
from outside: no fonts, no CDN, inline CSS and a few lines of inline script.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, timedelta
from html import escape
from typing import Final, Literal
from urllib.parse import quote

from .models import GRADES, NEVER_EXPORTED, OPEN_STATUSES, BoardSnapshot, EventRow, ProjectView, SourceRow, TodoRow

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
nav a.on{background:var(--text);color:#fff}
main>[hidden],main [hidden]{display:none!important}
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
.g-0{background:var(--green-bg);color:var(--green)} .g-1{background:var(--gray-bg);color:var(--text2)}
.g-2,.g-3{background:var(--red-bg);color:var(--red)}
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
a.pcard{display:block;color:inherit;text-decoration:none}
a.pcard:hover{border-color:var(--accent);box-shadow:0 1px 6px rgba(0,0,0,.06)}
.go{color:#0058b0;font-size:13px;margin-top:6px}
h3 a{color:inherit;text-decoration:none} h3 a:hover{color:#0058b0}
.picker{display:flex;gap:8px;align-items:center;margin:0 0 12px}
.picker select{font:inherit;padding:8px 34px 8px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);
color:var(--text);min-width:240px;max-width:100%;min-height:40px;cursor:pointer}
.picker select:hover{border-color:var(--accent)}
.btn{font:inherit;font-size:14px;padding:6px 14px;min-height:36px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--text);cursor:pointer}
.btn:hover{border-color:var(--accent)} .btn:disabled{opacity:.5;cursor:default}
.pv-head .btn{margin-left:auto}
.shelf-note{margin-top:12px}
.back{display:inline-block;margin-bottom:8px;font-size:14px}
.pv-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
details.fold>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:6px;user-select:none}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary::before{content:"▾";color:var(--text2);font-size:13px;width:14px;flex:none;text-align:center}
details.fold:not([open])>summary::before{content:"▸"}
details.fold>summary h3{margin:0;display:flex;gap:8px;align-items:center}
details.fold[open]>summary{margin-bottom:8px}
details.sect{margin-top:18px}
details.sect>summary h3{font-size:16px}
.pv-head h2{margin:0}
@media (max-width:600px){.bar{padding:8px 12px;gap:6px}main{padding:12px 12px 48px}.chip{min-width:calc(50% - 4px)}
nav{flex-wrap:nowrap;overflow-x:auto;width:100%;-webkit-overflow-scrolling:touch}nav a{flex:none;padding:7px 12px}
.picker select{flex:1;min-width:0;width:100%}
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
_NAV: Final = "".join(f'<a href="#{tab}" data-tab="{tab}">{label}</a>' for tab, label in TABS)

EMPTY_SECTION: Final = (
    '<div class="card empty">아직 없음 — 자동 분석이 새 메일에서 할 일을 찾고 카드에서 ✅ 하면 여기 나와요.</div>'
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
    c.textContent=shown+' / '+total+' 건';
  }
  q.addEventListener('input',apply);s.addEventListener('change',apply);
  document.addEventListener('pb-project',apply);apply();
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
    return (
        f'<label class="picker"><span class="small muted">프로젝트</span>'
        f'<select data-picker aria-label="프로젝트 고르기">{"".join(options)}</select></label>'
    )


def _fold(key: str, title: str, count: int, body: str, *, cls: str = "fold sect") -> str:
    """A list block whose heading folds it; the count stays in the heading, the state is kept per browser."""
    return (
        f'<details class="{cls}" data-fold="{escape(key, quote=True)}" open><summary><h3>{title} '
        f'<span class="badge d-none cnt">{count}</span></h3></summary>{body}</details>'
    )


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
    return _NAV.replace('data-tab="archive">보관함</a>', f'data-tab="archive">보관함{badges}</a>')


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
                f"<li><b>{title}</b> {_grade_badge(p.grade)} · 할 일 {p.todo_count}건 · "
                f"근거 「{escape(p.source_subject)}」{link}</li>"
            )
        pending = (
            '<div class="card pending" style="margin-top:12px"><b>소유자 승인을 기다리는 제안</b>'
            '<p class="note">아직 프로젝트 데이터가 아닙니다 — 카드에서 ✅ 하면 반영됩니다.</p>'
            f'<ul class="small">{"".join(items)}</ul></div>'
        )
    withheld = (
        f'<p class="note">보안 등급(대외비·기관지정) 할 일 {snapshot.withheld}건은 이 사본에서 뺐습니다.</p>'
        if snapshot.withheld
        else ""
    )
    projects = ""
    if snapshot.projects:
        cards = "".join(_project_card(view, today) for view in snapshot.projects)
        projects = f'<h3>프로젝트 <span class="small muted">— 눌러서 그 프로젝트만 보기</span></h3><div class="grid">{cards}</div>'
    return (
        '<section id="summary" data-tab="summary"><h2>요약</h2>'
        f'<div class="chips">{chips}</div>{pending}{projects}{withheld}'
        f'<p class="note">기준일 {escape(snapshot.today)} · 생성 {escape(_kst(snapshot.generated_at))}</p></section>'
    )


def _project_card(view: ProjectView, today: date, note: str = "") -> str:
    percent = round(100 * view.done / view.total) if view.total else 0
    owner = f'<div class="muted small">담당 {escape(view.owner)}</div>' if view.owner else ""
    overdue = f' <span class="badge d-over">지연 {view.overdue}</span>' if view.overdue else ""
    if view.next_due:
        label, css = _dday(view.next_due, today)
        upcoming = f'다음 마감 {escape(view.next_due)} <span class="badge {css}">{label}</span>'
    else:
        upcoming = "다음 마감 없음"
    href = escape(project_href(view.name), quote=True)
    return (
        f'<a class="card pcard" href="{href}" data-project="{escape(view.name, quote=True)}"><b>{escape(view.name)}</b>{owner}'
        f'<div class="prog" role="img" aria-label="진척 {percent}%"><i style="width:{percent}%"></i></div>'
        f'<div class="small">{view.done}/{view.total} 완료{overdue}</div>'
        f'<div class="small muted" style="margin-top:4px">{upcoming}</div>{note}'
        '<div class="go">할 일·일정 보기 →</div></a>'
    )


def _projects(snapshot: BoardSnapshot, today: date) -> str:
    cards = "".join(_project_card(view, today) for view in snapshot.projects)
    body = f'<div class="grid">{cards}</div>' if cards else EMPTY_SECTION
    return f'<section id="projects" data-tab="projects"><h2>프로젝트</h2>{body}</section>'


def _todo_item(todo: TodoRow, mode: Mode, today: date, *, anchor: bool = True) -> str:
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
    ident = f'id="todo-{escape(todo.todo_id, quote=True)}" ' if anchor else ""
    return (
        f'<li {ident}data-project="{escape(todo.project, quote=True)}" data-status="{escape(todo.status, quote=True)}"'
        f"{done_class}>{box}<div class=\"todo-main\">"
        f'<div class="todo-title">{escape(todo.title)}</div><div class="meta">'
        f'<span class="badge {_STATUS_CLASS.get(todo.status, "st-open")}">{escape(todo.status)}</span>'
        f'<span class="badge {css}">{label}</span>{_grade_badge(todo.grade)}{owner}{due}{progress}<span>{source}</span>'
        f"</div>{note}</div></li>"
    )


def _todo_order(todo: TodoRow) -> tuple[bool, str, str]:
    return (todo.status == "완료", todo.due or "9999", todo.todo_id)


def _todos(snapshot: BoardSnapshot, mode: Mode, today: date) -> str:
    if not snapshot.todos:
        return f'<section id="todos" data-tab="todos"><h2>할 일</h2>{EMPTY_SECTION}</section>'
    groups: dict[str, list[TodoRow]] = defaultdict(list)
    for todo in snapshot.todos:
        groups[todo.project].append(todo)
    blocks = []
    for project in _in_project_order(groups, snapshot):
        items = sorted(groups[project], key=_todo_order)
        heading = f'<a href="{escape(project_href(project), quote=True)}">{escape(project)} →</a>'
        body = f'<ul class="todos">{"".join(_todo_item(t, mode, today) for t in items)}</ul>'
        fold = _fold(f"t:{project}", heading, len(items), body, cls="todo-group card fold")
        blocks.append(fold.replace("<details ", f'<details data-project="{escape(project, quote=True)}" style="margin-bottom:10px" ', 1))
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
    picker = _picker("todos", snapshot)
    return f'<section id="todos" data-tab="todos"><h2>할 일</h2>{picker}{filters}{note}{"".join(blocks)}</section>'


def _agenda_cards(todos: tuple[TodoRow, ...] | list[TodoRow], today: date, key: str = "s") -> str:
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
            items.append(
                f'<li data-project="{escape(todo.project, quote=True)}"><span class="badge {css}">{label}</span>'
                f'<b>{escape(todo.title)}</b>{_grade_badge(todo.grade)}'
                f'<span class="small muted">{escape(todo.project)}'
                f'{" · " + escape(todo.owner) if todo.owner else ""}'
                f'{" · " + escape(todo.due) if todo.due else ""}</span></li>'
            )
        fold = _fold(f"{key}:{name}", name, len(rows), f'<ul>{"".join(items)}</ul>', cls="card bucket fold")
        parts.append(fold.replace("<details ", '<details style="margin-bottom:10px" ', 1))
    return "".join(parts)


def _agenda(snapshot: BoardSnapshot, today: date) -> str:
    cards = _agenda_cards(snapshot.todos, today)
    fallback = '<div class="card empty">마감이 남은 할 일이 없습니다.</div>' if snapshot.todos else EMPTY_SECTION
    none = (
        '<div id="schedule-none" class="card empty" style="display:none">이 프로젝트에는 마감이 남은 할 일이 없습니다.</div>'
        if cards
        else ""
    )
    body = (cards + none) or fallback
    picker = _picker("schedule", snapshot)
    return f'<section id="schedule" data-tab="schedule" class="agenda"><h2>일정</h2>{picker}{body}</section>'


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
        f'{escape(row.project)} {_grade_badge(row.grade)}</div><b>{title}</b><p class="small" style="margin:6px 0">{escape(row.summary)}</p>'
        f'{attachments}<div class="names">만든 할 일: {todos}</div></div>'
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
              "".join(_source_card(row, by_proposal[row.proposal_id], mode) for row in rows))
        for project, rows in ((name, groups[name]) for name in _in_project_order(groups, snapshot))
    ) or EMPTY_SECTION
    hint = (
        '<p class="note">원문 메일과 첨부는 이 사본에 없습니다 — 대시보드(테일넷 주소)의 근거 탭에서 열립니다.</p>'
        if mode == "file" and snapshot.sources
        else ""
    )
    return f'<section id="sources" data-tab="sources"><h2>근거</h2>{hint}{body}</section>'


def _events(snapshot: BoardSnapshot, mode: Mode) -> str:
    if not snapshot.events:
        return ""
    kinds = {"approved": "승인 반영", "progress": "진행 기록", "grade": "보안 등급 변경",
             "archive": "보관", "unarchive": "보관 해제"}

    def detail(event: EventRow) -> str:
        text = f" · {escape(event.detail)}" if event.detail else ""
        # who acted is a login; the file copy may be mailed out, so only the owner's screen shows it
        return text + (f" · {escape(event.actor)}" if mode == "served" and event.actor else "")

    items = "".join(
        f'<li><span class="muted">{escape(_kst(e.at))}</span> · {kinds.get(e.kind, escape(e.kind))} · '
        f"{escape(e.project)}{detail(e)}</li>"
        for e in snapshot.events[:15]
    )
    return f'<section id="events" data-tab="summary"><h2>최근 기록</h2><div class="card"><ul class="small">{items}</ul></div></section>'


def _archived_line(view: ProjectView, mode: Mode) -> str:
    """When (and, on the owner's own screen, by whom) a project was archived."""
    who = f" · {escape(view.archived_by)}" if mode == "served" and view.archived_by else ""
    return f"보관 {escape(_kst(view.archived_at))}{who}"


def _archive(shelf: tuple[ProjectView, ...], mode: Mode, today: date, fresh: dict[str, int] | None = None) -> str:
    """보관함: archived projects only. Their todos and evidence are kept and open from the card."""
    note = (
        '<p class="note" style="margin-bottom:12px">보관한 프로젝트는 요약·프로젝트·할 일·일정·근거와 프로젝트 고르기에 나오지 않습니다. '
        "할 일과 근거는 지워지지 않고, 프로젝트 화면의 「보관 해제」로 되돌립니다.</p>"
    )
    def line(view: ProjectView) -> str:
        count = (fresh or {}).get(view.name, 0)
        flag = f' <span class="badge d-soon">보관 뒤 새 할 일 {count}</span>' if count else ""
        return f'<div class="small muted" style="margin-top:4px">{_archived_line(view, mode)}{flag}</div>'

    cards = "".join(_project_card(view, today, line(view)) for view in shelf)
    body = f'<div class="grid">{cards}</div>' if cards else '<div class="card empty">보관한 프로젝트가 없습니다.</div>'
    return f'<section id="archive" data-tab="archive"><h2>보관함</h2>{note}{body}</section>'


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
    chips = (
        f'<div class="chip over"><span class="muted small">지연</span><b>{overdue}</b></div>'
        f'<div class="chip soon"><span class="muted small">이번 주 마감</span><b>{week}</b></div>'
        f'<div class="chip"><span class="muted small">완료</span><b>{view.done}/{view.total}</b></div>'
        f'<div class="chip"><span class="muted small">검토 대기</span><b>{len(pending)}</b></div>'
    )
    items = "".join(_todo_item(t, mode, today, anchor=False) for t in sorted(todos, key=_todo_order))
    todo_block = f'<div class="card"><ul class="todos">{items}</ul></div>' if items else (
        '<div class="card empty">이 프로젝트의 할 일이 없습니다.</div>'
    )
    key = f"p:{view.name}"
    agenda = _agenda_cards(todos, today, f"{key}:s") or '<div class="card empty">마감이 남은 할 일이 없습니다.</div>'
    by_proposal: dict[str, list[TodoRow]] = defaultdict(list)
    for todo in todos:
        by_proposal[todo.proposal_id].append(todo)
    evidence = "".join(_source_card(row, by_proposal[row.proposal_id], mode) for row in sources) or (
        '<div class="card empty">근거가 없습니다.</div>'
    )
    todo_fold = _fold(key + ":t", "할 일", len(todos), todo_block)
    agenda_fold = _fold(key + ":s", "일정", len(open_todos), '<div class="agenda">' + agenda + "</div>")
    evidence_fold = _fold(key + ":e", "근거 (메일·회의록)", len(sources), evidence)
    name_attr = escape(view.name, quote=True)
    button = (
        f'<button type="button" class="btn" data-archive="{"0" if archived else "1"}" data-archive-project="{name_attr}">'
        f'{"보관 해제" if archived else "보관"}</button>'
        if mode == "served"
        else ""
    )
    back = '<a class="back" href="#archive">← 보관함</a>' if archived else '<a class="back" href="#projects">← 전체 프로젝트</a>'
    shelf = (
        f'<div class="card note shelf-note">보관한 프로젝트입니다 — 요약·할 일·일정·근거에 나오지 않습니다. '
        f"{_archived_line(view, mode)}</div>"
        if archived
        else ""
    )
    flag = ' data-archived="1"' if archived else ""
    return (
        f'<section id="pv-{index}" data-tab="project" data-project="{name_attr}"{flag}>{back}'
        f'<div class="pv-head"><h2>{escape(view.name)}</h2>{badges}{owner}{button}</div>{shelf}'
        f'<div class="chips" style="margin-top:12px">{chips}</div>'
        f"{todo_fold}{agenda_fold}{evidence_fold}</section>"
    )


def render_dashboard(snapshot: BoardSnapshot, *, mode: Mode, token: str = "") -> str:
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
            '<div class="card empty" data-tab="summary" style="margin-top:18px">아직 데이터가 없습니다. 메일·회의록 분석 제안을 '
            "소유자가 승인(✅)하면 프로젝트와 할 일이 여기에 나타납니다.</div>"
        )
    meta = f'<meta name="pb-token" content="{escape(token, quote=True)}">' if mode == "served" else ""
    scripts = _FOLD_JS + _TABS_JS + (_FILTER_JS if snapshot.todos else "") + (_SERVED_JS if mode == "served" else "")
    pages = "".join(
        _project_page(i, view, full, mode, today, archived=view in shelf)
        for i, view in enumerate(snapshot.projects + shelf, 1)
    )
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"{meta}<title>프로젝트 대시보드</title><style>{_CSS}</style></head><body>"
        f'<header><div class="bar"><h1>프로젝트 대시보드</h1><nav aria-label="화면">{_nav(shelf, fresh)}</nav></div></header><main>'
        f"{empty}{_summary(snapshot, today)}{_projects(snapshot, today)}{_todos(snapshot, mode, today)}"
        f"{_agenda(snapshot, today)}{_sources(snapshot, mode)}{_archive(shelf, mode, today, fresh)}{_events(snapshot, mode)}{pages}</main>"
        f"<script>{scripts}</script></body></html>"
    )
