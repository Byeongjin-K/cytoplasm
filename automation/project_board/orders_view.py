"""The 도리 지시 screens: a box on 요약, the order form and list (``#orders``), one screen per order
(``#order/<n>``) and the orders of a todo on that todo's own screen.

Served view only: orders are the owner's private words, so the exported file never carries them.
Every value is escaped; the room post link is an https address checked when it was written.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from html import escape
from typing import Final

from .dashboard_theme import fold, icon
from .models import BoardSnapshot, OPEN_STATUSES
from .orders import DEFAULT_RECEIVER, LOCAL, MAX_BODY, ORDER_STATUSES, OWNER_DEVICE, Order, order_href

_STATUS_CLASS: Final = {"받음": "st-open", "넘김": "st-held", "처리 중": "st-doing", "끝": "st-done"}
_EVENT_WORDS: Final = {"created": "대시보드에 지시를 남김", "delivered": "에게 전달됨", "used": "쓰기 근거로 확인됨"}
_FIELD_WORDS: Final = {"dori": "맡은 도리 ", "post_url": "도리 방 글 연결", "note": "메모: "}
_SHOWN: Final = 3  # open orders on the summary box; below them only the latest finished one (its result)
# A trial order checks the path and carries no work (lead 2026-10-22: "[시험]" in the words, or the relay marked it
# as not sent). It stays in the store and on its own screen, but the owner's lists keep it apart.
_TRIAL_WORD: Final = "[시험]"
_TRIAL_TARGET: Final = "시험"
# A memo is the receiving Dori's words. Status codes in brackets and clauses about the gate are machine talk the owner
# does not read (owner 2026-10-22 "부자연스러워"), so the screens drop them; the store keeps the memo as written.
_CODE: Final = re.compile(r"\s*\((?:HTTP\s*)?\d{3}\)")
_MACHINE: Final = re.compile(r"게이트|\b(?:gate|exit|http|api|sha\d*|token|json)\b", re.IGNORECASE)
_CLAUSE: Final = re.compile(r"\s*[,;]\s*")

ORDERS_CSS: Final = (
    ".order-form textarea{font:inherit;font-size:var(--t-md);color:var(--text);background:var(--surface);"
    "border:1px solid var(--line-strong);border-radius:var(--r-md);padding:var(--s3);min-height:112px;width:100%;"
    "resize:vertical;box-sizing:border-box}"
    ".order-form textarea:hover{border-color:var(--text-3)}"
    ".order-form{display:flex;flex-direction:column;gap:var(--s4)}"
    ".order-links{display:grid;grid-template-columns:1fr 1fr;gap:var(--s3)}"
    ".order-hint{font-weight:400}"
    ".order-steps .past{color:var(--text-2)}"
    ".order-steps .step{display:inline-flex;gap:6px;align-items:center;white-space:nowrap}"
    ".order-steps .past::before{content:'\u2713\u00a0'}"
    ".agenda li>.order-note{flex-basis:100%;margin:0;color:var(--text-2);font-size:var(--t-sm);"
    "white-space:pre-wrap;overflow-wrap:anywhere}"
    ".agenda li>.order-rel{flex-basis:100%;display:flex;flex-direction:column;gap:0;font-size:var(--t-sm);color:var(--text-3)}"
    ".order-rel a{display:inline-block;padding:3px 0;color:var(--text-2);text-decoration:underline;"
    "text-decoration-color:var(--line-strong);text-underline-offset:3px;overflow-wrap:anywhere}"
    ".order-rel a:hover{color:var(--accent-ink)}"
    ".order-rel>div{display:flex;gap:8px;align-items:flex-start}"
    ".order-rel .lbl{flex:none;padding:3px 0}"
    ".order-last-label{margin:var(--s3) 0 0;padding-top:var(--s3);border-top:1px solid var(--line)}"
    ".order-more{display:inline-flex;align-items:center;gap:4px;min-height:44px;font-size:var(--t-sm)}"
    ".order-trial-why{margin:0 0 var(--s2)}"
    ".order-card{margin-top:var(--gap-card)}"
    ".order-sent{margin-top:var(--gap-card);background:var(--accent-wash);border-color:transparent;color:var(--accent-ink)}"
    "main>section[data-tab=order]>.card+.card{margin-top:var(--gap-card)}"
    ".order-actions{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s3)}"
    ".order-actions .btn{min-height:44px}"
    ".order-actions .row-error{flex-basis:100%;margin-top:0}"
    ".order-form p{margin:0}"
    ".order-body{white-space:pre-wrap;overflow-wrap:anywhere;margin:0}"
    ".order-steps{display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center;font-size:var(--t-sm);color:var(--text-3)}"
    ".order-steps b{color:var(--accent-ink);background:var(--accent-wash);padding:2px 8px;border-radius:var(--r-sm)}"
    "a.order-new{display:inline-flex;align-items:center;gap:6px;min-height:44px}"
    "main>section[data-tab=orders],main>section[data-tab=order]{max-width:820px}"
    "#orders>.card,#orders>details.card{margin-top:var(--gap-card)}#order-box{margin-top:var(--gap-card)}"
    "@media (max-width:640px){.order-links{grid-template-columns:1fr}}"
)

ORDERS_JS: Final = """
(function(){
  var PB=window.PB=window.PB||{};
  function form(){return document.getElementById('order-form');}
  function count(f){var t=f.querySelector('textarea'),c=f.querySelector('.order-count');if(t&&c){c.textContent=t.value.length+'/'+t.maxLength;}}
  function linked(f){
    var todo=f.querySelector('select[name=todo]'),proj=f.querySelector('select[name=project]');if(!todo||!proj){return;}
    var o=todo.options[todo.selectedIndex];if(o&&o.dataset.p){proj.value=o.dataset.p;}proj.disabled=!!todo.value;
    var hint=f.querySelector('.order-hint');if(hint){hint.hidden=!todo.value;}
    if(PB.findSync){PB.findSync();}
  }
  // a sent order opens on its own screen with one line saying it went out (the reload drops any live message)
  function sent(){
    var n='';try{n=sessionStorage.getItem('pb-order-sent')||'';sessionStorage.removeItem('pb-order-sent');}catch(x){}
    var page=n&&document.querySelector('main>section[data-tab=order][data-key="'+n+'"]');if(!page||page.querySelector('.order-sent')){return;}
    var b=document.createElement('div');b.className='card note order-sent';b.setAttribute('role','status');
    b.textContent='보냈습니다. '+(page.dataset.receiver||'받는 도리')+'에게 곧 전달되고, 누가 맡았는지와 진행이 이 화면에 적힙니다.';
    page.querySelector('.pv-head').after(b);
  }
  function note(f,text){
    var old=f.querySelector('.row-error');if(old){old.remove();}if(!text){return;}
    var b=document.createElement('span');b.className='row-error';b.setAttribute('role','alert');b.textContent=text;
    f.querySelector('.order-actions').appendChild(b);
  }
  // delegated, so a form the 2-minute refresh swapped in keeps working
  document.addEventListener('input',function(e){var f=form();if(f&&f.contains(e.target)){count(f);}});
  document.addEventListener('change',function(e){var f=form();if(f&&e.target.name==='todo'&&f.contains(e.target)){linked(f);}});
  document.addEventListener('click',function(e){
    var a=e.target.closest&&e.target.closest('a[data-order-todo],a[data-order-project]'),f=form();if(!a||!f){return;}
    var todo=f.querySelector('select[name=todo]');todo.value=a.dataset.orderTodo||'';linked(f);
    if(a.dataset.orderProject){f.querySelector('select[name=project]').value=a.dataset.orderProject;}
    window.requestAnimationFrame(function(){f.querySelector('textarea').focus();});
  });
  document.addEventListener('submit',function(e){
    var f=form();if(!f||e.target!==f){return;}
    e.preventDefault();note(f,'');
    var text=f.querySelector('textarea'),todo=f.querySelector('select[name=todo]'),proj=f.querySelector('select[name=project]'),
        btn=f.querySelector('button[type=submit]');
    if(!text.value.trim()){note(f,'지시 내용을 써 주세요.');text.focus();return;}
    btn.disabled=true;if(PB.say){PB.say('보내는 중');}
    var body='text='+encodeURIComponent(text.value)+'&todo='+encodeURIComponent(todo.value)
      +'&project='+encodeURIComponent(todo.value?'':proj.value);
    PB.post('/order/new',body).then(function(r){return r.json();}).then(function(d){
      try{sessionStorage.setItem('pb-order-sent',String(d.number));}catch(x){}
      location.hash='order/'+d.number;location.reload();
    }).catch(function(error){btn.disabled=false;var w=PB.why?PB.why(error):'보내지 못했습니다.';note(f,w);if(PB.say){PB.say(w);}});
  });
  var f=form();if(f){count(f);linked(f);}sent();
})();
"""


def _subject(name: str) -> str:
    """``name`` with 이/가 (이 after a final consonant)."""
    code = ord(name[-1:] or "가") - 0xAC00
    return f"{name}{'이' if 0 <= code < 11172 and code % 28 else '가'}"


def _kst(stamp: str) -> str:
    from .dashboard import _kst as kst

    return kst(stamp)


def _first_line(body: str, limit: int = 60) -> str:
    line = body.split("\n", 1)[0].strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _badge(status: str) -> str:
    return f'<span class="badge {_STATUS_CLASS.get(status, "st-open")}">{escape(status)}</span>'


def is_trial(order: Order) -> bool:
    """A path check with no work in it: its words start with [시험] or the relay marked it as not sent."""
    return order.body.lstrip().startswith(_TRIAL_WORD) or order.delivered_to.startswith(_TRIAL_TARGET)


def plain_note(note: str) -> str:
    """The memo in the owner's words: bracketed status codes go, and so does any clause about the gate."""
    kept = [c for c in _CLAUSE.split(_CODE.sub("", note)) if c.strip() and not _MACHINE.search(c)]
    return ", ".join(c.strip() for c in kept)


def _related(order: Order, titles: dict[str, str]) -> str:
    """The todo, project and room post of an order, one per line under the row: a long todo title wraps on its own
    line instead of dragging a 「·」 to the far edge of the meta line (owner phone 2026-10-22)."""
    from .dashboard import project_href, todo_href

    lines = []
    if order.todo_id:
        title = titles.get(order.todo_id)
        target = (f'<a href="{escape(todo_href(order.todo_id), quote=True)}">{escape(title)}</a>' if title
                  else '<span class="muted">지난 할\u00a0일</span>')
        lines.append(f'<div><span class="lbl">할\u00a0일</span>{target}</div>')
    if order.project:
        lines.append(f'<div><span class="lbl">프로젝트</span><a href="{escape(project_href(order.project), quote=True)}">'
                     f"{escape(order.project)}</a></div>")
    if order.post_url:
        lines.append(f'<div><a href="{escape(order.post_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
                     "도리 방 글 보기</a></div>")
    return f'<div class="order-rel">{"".join(lines)}</div>' if lines else ""


def _who(order: Order) -> str:
    """Who has it now, said once: the Dori that finished or took it, else the receiver once it got there, else not
    yet. A trial the relay did not send says so in plain words."""
    def name(text: str) -> str:  # a Dori name never splits across two lines
        return escape(text).replace(" ", "\u00a0")

    if order.delivered_to.startswith(_TRIAL_TARGET):
        return "시험이라 보내지 않음"
    if not order.is_open:
        return f"{name(_subject(order.dori or order.delivered_to or order.receiver))} 끝냄"
    if order.dori:
        return f"맡은\u00a0도리\u00a0{name(order.dori)}"
    if order.delivered_at:
        return f"{name(order.delivered_to or order.receiver)}에게 전달됨"
    return "아직 전달 전"


def _row(order: Order, titles: dict[str, str], *, related: bool = True) -> str:
    from .dashboard import _glued

    stamp = order.created_at if order.is_open else order.updated_at
    where = [_badge(order.status), escape(_kst(stamp)), _who(order)]
    memo = plain_note(order.note)
    note = f'<p class="order-note">{escape(memo)}</p>' if memo else ""
    return (
        f'<li><a class="ag-title" href="{escape(order_href(order.number), quote=True)}">#{order.number} '
        f'{escape(_first_line(order.body))}</a>'
        f'<span class="where">{_glued(where)}</span>{note}{_related(order, titles) if related else ""}</li>'
    )


def _latest_first(orders: Iterable[Order]) -> list[Order]:
    return sorted(orders, key=lambda o: (o.updated_at, o.number), reverse=True)


def _titles(snapshot: BoardSnapshot) -> dict[str, str]:
    return {t.todo_id: t.title for t in snapshot.todos}


def summary_box(orders: Sequence[Order], snapshot: BoardSnapshot) -> str:
    """Short on a phone (owner 2026-10-22 "부자연스러워"): the open orders, then one line for the latest result, and a
    way to the full list. Finished ones stay folded in the orders tab and trials stay out of the box."""
    real = [o for o in orders if not is_trial(o)]
    open_ = [o for o in real if o.is_open]
    done = _latest_first(o for o in real if not o.is_open)
    line = (f"진행 중 {len(open_)}건" if open_ else "진행 중 없음") if real else "아직 보낸 지시가 없습니다"
    titles = _titles(snapshot)
    body = f'<ul>{"".join(_row(o, titles, related=False) for o in open_[:_SHOWN])}</ul>' if open_ else ""
    if done:
        body += (f'<p class="small muted order-last-label">최근 결과</p>'
                 f'<ul>{_row(done[0], titles, related=False)}</ul>')
    shown = min(len(open_), _SHOWN) + (1 if done else 0)
    if len(orders) > shown:  # the rest, trials included, are in the tab; the count names only real orders not shown
        more = len(real) - shown
        count = f'<span class="muted">({more}건 더)</span>' if more else ""
        body += f'<a class="order-more" href="#orders">지시 탭에서 전부 보기{count}{icon("chevron-right")}</a>'
    if not real:
        body = '<p class="small muted order-none">「지시 쓰기」로 맡긴 일이 여기에 쌓이고, 누가 맡아 어디까지 했는지 보입니다.</p>'
    write = '<a class="btn primary" href="#orders">지시 쓰기</a>'
    box = fold("orders-box", "도리에게 지시", len(open_), body, cls="agenda", glyph="inbox", sub=line, trail=write)
    return f'<section id="order-box" data-tab="summary">{box}</section>'


def _options(snapshot: BoardSnapshot) -> tuple[str, str]:
    """Open todos under their project (board order), the ones in progress first, then the nearest deadline."""
    groups: dict[str, list[str]] = {}
    open_todos = sorted((t for t in snapshot.todos if t.status in OPEN_STATUSES),
                        key=lambda t: (t.status != "진행", t.due or "9999", t.title))
    for todo in open_todos:
        groups.setdefault(todo.project, []).append(
            f'<option value="{escape(todo.todo_id, quote=True)}" data-p="{escape(todo.project, quote=True)}">'
            f"{escape(todo.title)}</option>"
        )
    rank = {view.name: i for i, view in enumerate(snapshot.projects)}
    todos = "".join(f'<optgroup label="{escape(p, quote=True)}">{"".join(groups[p])}</optgroup>'
                    for p in sorted(groups, key=lambda name: (rank.get(name, len(rank)), name)))
    projects = "".join(
        f'<option value="{escape(v.name, quote=True)}">{escape(v.name)}</option>' for v in snapshot.projects
    )
    return todos, projects


def orders_section(orders: Sequence[Order], snapshot: BoardSnapshot, receiver: str) -> str:
    who = receiver or DEFAULT_RECEIVER
    todos, projects = _options(snapshot)
    form = (
        '<form id="order-form" class="card order-form" novalidate>'
        f'<label class="field" for="order-text">지시 내용<textarea id="order-text" name="text" rows="4" maxlength="{MAX_BODY}" '
        'required placeholder="예: 공정 회의 자료를 모아서 요약해 줘"></textarea></label>'
        '<div class="order-links">'
        f'<label class="field" for="order-todo">관련 할\u00a0일<select id="order-todo" name="todo"><option value="">없음</option>{todos}</select></label>'
        f'<label class="field" for="order-project">프로젝트<select id="order-project" name="project"><option value="">없음</option>{projects}</select>'
        '<span class="order-hint small muted" hidden>고른 할\u00a0일의 프로젝트로 정해집니다</span></label>'
        "</div>"
        f'<p class="small muted">{escape(_subject(who))} 받아 맞는 도리에게 넘기고, 누가 맡았는지와 어디까지 됐는지를 여기에 적습니다.</p>'
        '<div class="order-actions"><button class="btn primary" type="submit">도리에게 보내기</button>'
        '<span class="order-count small muted" aria-live="polite"></span></div></form>'
    )
    titles = _titles(snapshot)
    open_ = [o for o in orders if o.is_open and not is_trial(o)]
    done = _latest_first(o for o in orders if not o.is_open and not is_trial(o))
    trials = _latest_first(o for o in orders if is_trial(o))
    lists = ""
    if open_:
        lists += fold("orders-open", "진행 중인 지시", len(open_), f'<ul>{"".join(_row(o, titles) for o in open_)}</ul>',
                      cls="agenda")
    if done:
        lists += fold("orders-done", "끝난 지시", len(done), f'<ul>{"".join(_row(o, titles) for o in done)}</ul>',
                      cls="agenda", open_=not open_)
    if trials:
        lists += fold("orders-trial", "시험 지시", len(trials),
                      '<p class="small muted order-trial-why">도리 경로를 확인하려고 보낸 것이라 맡길 일은 없습니다.</p>'
                      f'<ul>{"".join(_row(o, titles) for o in trials)}</ul>', cls="agenda", open_=False)
    if not orders:
        lists = ('<div class="card empty">' + icon("inbox") + "<b>아직 보낸 지시가 없습니다</b>"
                 "<p>위 칸에 도리에게 맡길 일을 쓰면 여기에 쌓이고, 누가 맡아 어디까지 했는지 함께 보입니다.</p></div>")
    return (
        # a bottom tab of its own (owner Q360); the back link shows only when a todo or project screen opened it
        '<section id="orders" data-tab="orders" data-title="도리에게 지시"><a class="back" href="#summary" data-back hidden>'
        f'{icon("chevron-left")}<span>요약</span></a>'
        f'<div class="pv-head"><h2>도리에게 지시</h2></div>{form}{lists}</section>'
    )


def _steps(status: str) -> str:
    at = ORDER_STATUSES.index(status) if status in ORDER_STATUSES else 0
    steps = (f"<b>{escape(s)}</b>" if i == at else f'<span class="past">{escape(s)}</span>' if i < at else f"<span>{escape(s)}</span>"
             for i, s in enumerate(ORDER_STATUSES))
    # each arrow travels with the step after it, so a narrow screen never ends a line on a bare arrow
    return '<div class="order-steps">' + "".join(
        step if i == 0 else f'<span class="step"><span aria-hidden="true">→</span>{step}</span>' for i, step in enumerate(steps)
    ) + "</div>"


def _event(at: str, kind: str, detail: str, actor: str, times: int = 1) -> str:
    """One history line in plain words. The sender is on the 보낸 기기 row and the receiver is named in the line,
    so only a change says who made it, on its own line (a login address or 127.0.0.1 is not a name)."""
    who = ""
    if kind == "created":
        what = _EVENT_WORDS["created"]
    elif kind == "delivered":
        what = ("시험이라 도리에게 보내지 않음" if detail.startswith(_TRIAL_TARGET)
                else escape(detail) + _EVENT_WORDS["delivered"])
    elif kind == "used":
        what = _EVENT_WORDS["used"] + (f" ({times}번)" if times > 1 else "")
    else:
        parts = []
        for piece in detail.split(" · "):
            key, sep, value = piece.partition("=")
            if sep and key in _FIELD_WORDS:
                shown = plain_note(value) if key == "note" else value
                parts.append(_FIELD_WORDS[key] + ("" if key == "post_url" else escape(shown)))
            else:
                parts.append(f"상태 {escape(piece)}")
        what = ", ".join(parts)
        who = f'<div class="small muted">{escape(actor)}</div>' if actor else ""
    return f'<li><time datetime="{escape(at, quote=True)}">{escape(_kst(at))}</time><div class="what">{what}</div>{who}</li>'


def _history(order: Order) -> str:
    """Newest first; the gate's uses in one stretch are one line with a count, not a row each."""
    rows: list[str] = []
    events = list(reversed(order.events))
    i = 0
    while i < len(events):
        e, run = events[i], 1
        while e.kind == "used" and i + run < len(events) and events[i + run].kind == "used":
            run += 1
        rows.append(_event(e.at, e.kind, e.detail, e.actor, run))
        i += run
    return "".join(rows)


def order_pages(orders: Iterable[Order], snapshot: BoardSnapshot) -> str:
    titles = _titles(snapshot)
    return "".join(_order_page(order, titles) for order in orders)


def _device(order: Order) -> str:
    label = escape(order.device_name or order.device_ip or "")
    if order.via == OWNER_DEVICE:
        return f"형 기기 · {label}"
    if order.via == LOCAL:
        return '<span class="muted">노드에서 직접</span>'
    return (f'다른 기기{" · " + label if label else ""}'
            '<div class="small muted">형 기기에서 온 지시가 아니라 형 명령 근거로 쓰지 않습니다</div>')


def _order_page(order: Order, titles: dict[str, str]) -> str:
    from .dashboard import project_href, todo_href

    delivered = ("시험이라 보내지 않음" if order.delivered_to.startswith(_TRIAL_TARGET)
                 else f"{escape(_kst(order.delivered_at))} 전달됨" if order.delivered_at else "아직 전달 전")
    rows = [
        ("상태", _steps(order.status)),
        ("보낸 때", escape(_kst(order.created_at))),
        ("보낸 기기", _device(order)),
        ("받는 도리", f"{escape(order.receiver)}<div class=\"small muted\">{delivered}</div>"),
        ("맡은 도리", escape(order.dori) if order.dori
         else '<span class="muted">없음</span>' if is_trial(order)
         else f'{escape(order.delivered_to or order.receiver)} <span class="muted">(직접 처리)</span>' if not order.is_open
         else '<span class="muted">아직 정해지지 않음</span>'),
    ]
    if order.post_url:
        rows.append(("도리 방 글", f'<a href="{escape(order.post_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
                                 "도리 방에서 보기</a>"))
    if order.todo_id:
        title = titles.get(order.todo_id)
        rows.append(("관련 할\u00a0일", f'<a href="{escape(todo_href(order.todo_id), quote=True)}">{escape(title)}</a>' if title
                     else '<span class="muted">지난 할\u00a0일(보관 또는 정리됨)</span>'))
    if order.project:
        rows.append(("프로젝트", f'<a href="{escape(project_href(order.project), quote=True)}">{escape(order.project)}</a>'))
    if plain_note(order.note):
        rows.append(("메모", f'<span class="order-body">{escape(plain_note(order.note))}</span>'))
    if is_trial(order):
        rows.append(("시험", '<span class="muted">도리 경로를 확인하려고 보낸 지시라 맡길 일은 없습니다</span>'))
    facts = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    history = _history(order)
    log = fold(f"order-log:{order.number}", "기록", history.count("<li"), f'<ul class="td-log">{history}</ul>')
    return (
        f'<section data-tab="order" data-key="{order.number}" data-title="지시 #{order.number}" '
        f'data-receiver="{escape(order.receiver, quote=True)}">'
        f'<a class="back" href="#orders" data-back>{icon("chevron-left")}<span>도리에게 지시</span></a>'
        f'<div class="pv-head"><h2>지시 #{order.number}</h2></div>'
        f'<div class="card"><h3>지시 내용</h3><p class="order-body">{escape(order.body)}</p></div>'
        f'<div class="card"><dl class="td-facts">{facts}</dl></div>'
        f"{log}</section>"
    )


def _order_card(key: str, mine: Sequence[Order], heading: str, link: str, words: str) -> str:
    items = "".join(
        f'<li><a href="{escape(order_href(o.number), quote=True)}">#{o.number} {escape(_first_line(o.body, 40))}</a>'
        f"{_badge(o.status)}</li>" for o in mine
    )
    listed = f'<ul class="td-list">{items}</ul>' if items else ""
    return fold(key, heading, len(mine), f'{listed}<a class="order-new" href="#orders" {link}>{icon("inbox")}{words}</a>',
                cls="order-card")


def todo_orders(orders: Sequence[Order], todo_id: str) -> str:
    return _order_card(f"todo-orders:{todo_id}", [o for o in orders if o.todo_id == todo_id], "이 할\u00a0일에 대한 도리 지시",
                       f'data-order-todo="{escape(todo_id, quote=True)}"', "이 할\u00a0일로 도리에게 지시")


def project_orders(orders: Sequence[Order], project: str) -> str:
    """The orders about one project (directly or through one of its todos) and a way to write one more."""
    return _order_card(f"project-orders:{project}", [o for o in orders if o.project == project], "이 프로젝트에 대한 도리 지시",
                       f'data-order-project="{escape(project, quote=True)}"', "이 프로젝트로 도리에게 지시")
