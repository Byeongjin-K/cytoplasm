"""The 도리 지시 screens: a box on 요약, the order form and list (``#orders``), one screen per order
(``#order/<n>``) and the orders of a todo on that todo's own screen.

Served view only: orders are the owner's private words, so the exported file never carries them.
Every value is escaped; the room post link is an https address checked when it was written.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from html import escape
from typing import Final

from .dashboard_theme import icon
from .models import BoardSnapshot, OPEN_STATUSES
from .orders import DEFAULT_RECEIVER, MAX_BODY, ORDER_STATUSES, Order, order_href

_SEP: Final = "\u00a0· "
_STATUS_CLASS: Final = {"받음": "st-open", "넘김": "st-held", "처리 중": "st-doing", "끝": "st-done"}
_EVENT_WORDS: Final = {"created": "대시보드에 지시를 남김", "delivered": "에게 전달됨", "used": "쓰기 근거로 확인됨"}
_FIELD_WORDS: Final = {"dori": "맡은 도리", "post_url": "도리 방 글", "note": "메모"}

ORDERS_CSS: Final = (
    ".order-form textarea{font:inherit;font-size:var(--t-md);color:var(--text);background:var(--surface);"
    "border:1px solid var(--line-strong);border-radius:var(--r-md);padding:var(--s3);min-height:112px;width:100%;"
    "resize:vertical;box-sizing:border-box}"
    ".order-form textarea:hover{border-color:var(--text-3)}"
    ".order-form{display:flex;flex-direction:column;gap:var(--s4)}"
    ".order-links{display:grid;grid-template-columns:1fr 1fr;gap:var(--s3)}"
    ".order-links select{width:100%;min-height:44px}"
    ".order-actions{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s3)}"
    ".order-actions .btn{min-height:44px}"
    ".order-actions .row-error{flex-basis:100%;margin-top:0}"
    ".order-form p{margin:0}"
    ".order-body{white-space:pre-wrap;overflow-wrap:anywhere;margin:0}"
    ".order-steps{display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center;font-size:var(--t-sm);color:var(--text-3)}"
    ".order-steps b{color:var(--accent-ink);background:var(--accent-wash);padding:2px 8px;border-radius:var(--r-sm)}"
    ".order-box-head{display:flex;gap:var(--s3);align-items:center;flex-wrap:wrap}"
    ".order-box-head>div{flex:1 1 180px;min-width:0}"
    ".order-box-head p{margin:2px 0 0}"
    ".order-box-head .btn{min-height:44px}"
    "a.order-new{display:inline-flex;align-items:center;gap:6px;min-height:44px}"
    "main>section[data-tab=orders],main>section[data-tab=order]{max-width:820px}"
    "#orders>.card,#orders>details.card{margin-top:var(--s4)}"
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
    var a=e.target.closest&&e.target.closest('a[data-order-todo]'),f=form();if(!a||!f){return;}
    var todo=f.querySelector('select[name=todo]');todo.value=a.dataset.orderTodo;linked(f);
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
      location.hash='order/'+d.number;location.reload();
    }).catch(function(error){btn.disabled=false;var w=PB.why?PB.why(error):'보내지 못했습니다.';note(f,w);if(PB.say){PB.say(w);}});
  });
  var f=form();if(f){count(f);linked(f);}
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


def _links(order: Order, titles: dict[str, str]) -> list[str]:
    from .dashboard import project_href, todo_href

    out = []
    if order.todo_id:
        title = titles.get(order.todo_id)
        out.append(f'<a href="{escape(todo_href(order.todo_id), quote=True)}">{escape(title)}</a>' if title
                   else '<span class="muted">지난 할\u00a0일</span>')
    if order.project:
        out.append(f'<a href="{escape(project_href(order.project), quote=True)}">{escape(order.project)}</a>')
    return out


def _row(order: Order, titles: dict[str, str]) -> str:
    where = [escape(_kst(order.created_at))]
    where += [f"맡은 도리 {escape(order.dori)}"] if order.dori else []
    where += _links(order, titles)
    if order.post_url:
        where.append(f'<a href="{escape(order.post_url, quote=True)}" target="_blank" rel="noopener noreferrer">도리 방 글</a>')
    return (
        f'<li><a class="ag-title" href="{escape(order_href(order.number), quote=True)}">#{order.number} '
        f'{escape(_first_line(order.body))}</a>{_badge(order.status)}'
        f'<span class="where">{_SEP.join(where)}</span></li>'
    )


def _titles(snapshot: BoardSnapshot) -> dict[str, str]:
    return {t.todo_id: t.title for t in snapshot.todos}


def summary_box(orders: Sequence[Order], snapshot: BoardSnapshot) -> str:
    open_ = [o for o in orders if o.is_open]
    done = len(orders) - len(open_)
    line = f"진행 중 {len(open_)}건{' · 끝 ' + str(done) + '건' if done else ''}" if orders else "아직 보낸 지시가 없습니다"
    titles = _titles(snapshot)
    items = "".join(_row(o, titles) for o in open_[:3])
    more = f'<div class="small"><a href="#orders">지시 {len(orders)}건 모두 보기</a></div>' if len(orders) > 3 else ""
    body = f'<ul>{items}</ul>{more}' if items else more
    return (
        '<section id="order-box" data-tab="summary"><div class="card agenda">'
        f'<div class="order-box-head">{icon("inbox")}<div><b>도리에게 지시</b><p class="small muted">{line}</p></div>'
        '<a class="btn primary" href="#orders">지시 쓰기</a></div>'
        f"{body}</div></section>"
    )


def _options(snapshot: BoardSnapshot) -> tuple[str, str]:
    groups: dict[str, list[str]] = {}
    for todo in snapshot.todos:
        if todo.status in OPEN_STATUSES:
            groups.setdefault(todo.project, []).append(
                f'<option value="{escape(todo.todo_id, quote=True)}" data-p="{escape(todo.project, quote=True)}">'
                f"{escape(todo.title)}</option>"
            )
    todos = "".join(f'<optgroup label="{escape(p, quote=True)}">{"".join(rows)}</optgroup>' for p, rows in sorted(groups.items()))
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
        f'<label class="field" for="order-project">프로젝트<select id="order-project" name="project"><option value="">없음</option>{projects}</select></label>'
        "</div>"
        f'<p class="small muted">{escape(_subject(who))} 받아 맞는 도리에게 넘기고, 누가 맡았는지와 어디까지 됐는지를 여기에 적습니다.</p>'
        '<div class="order-actions"><button class="btn primary" type="submit">도리에게 보내기</button>'
        '<span class="order-count small muted" aria-live="polite"></span></div></form>'
    )
    titles = _titles(snapshot)
    open_ = [o for o in orders if o.is_open]
    done = [o for o in orders if not o.is_open]
    lists = ""
    if open_:
        lists += (f'<div class="card agenda"><h3>진행 중인 지시 <span class="cnt">{len(open_)}</span></h3>'
                  f'<ul>{"".join(_row(o, titles) for o in open_)}</ul></div>')
    if done:
        lists += (f'<details class="card agenda" data-fold="orders-done"><summary><h3>끝난 지시 '
                  f'<span class="cnt">{len(done)}</span></h3></summary><ul>{"".join(_row(o, titles) for o in done)}</ul></details>')
    if not orders:
        lists = ('<div class="card empty">' + icon("inbox") + "<b>아직 보낸 지시가 없습니다</b>"
                 "<p>위 칸에 도리에게 맡길 일을 쓰면 여기에 쌓이고, 누가 맡아 어디까지 했는지 함께 보입니다.</p></div>")
    return (
        '<section id="orders" data-tab="orders"><a class="back" href="#summary" data-back>'
        f'{icon("chevron-left")}<span>요약</span></a>'
        f'<div class="pv-head"><h2>도리에게 지시</h2></div>{form}{lists}</section>'
    )


def _steps(status: str) -> str:
    return '<div class="order-steps">' + '<span aria-hidden="true">→</span>'.join(
        f"<b>{escape(s)}</b>" if s == status else f"<span>{escape(s)}</span>" for s in ORDER_STATUSES
    ) + "</div>"


def _event(order: Order, at: str, kind: str, detail: str, actor: str) -> str:
    if kind == "created":
        what = _EVENT_WORDS["created"]
    elif kind == "delivered":
        what = escape(detail) + _EVENT_WORDS["delivered"]
    elif kind == "used":
        what = _EVENT_WORDS["used"]
    else:
        parts = []
        for piece in detail.split(" · "):
            key, sep, value = piece.partition("=")
            if sep and key in _FIELD_WORDS:
                parts.append(f"{_FIELD_WORDS[key]} {'바꿈' if key == 'post_url' else escape(value)}")
            else:
                parts.append(f"상태 {escape(piece)}")
        what = ", ".join(parts)
    who = f' <span class="muted">{escape(actor)}</span>' if actor and kind != "used" else ""
    return f'<li><time datetime="{escape(at, quote=True)}">{escape(_kst(at))}</time><div class="what">{what}{who}</div></li>'


def order_pages(orders: Iterable[Order], snapshot: BoardSnapshot) -> str:
    titles = _titles(snapshot)
    return "".join(_order_page(order, titles) for order in orders)


def _order_page(order: Order, titles: dict[str, str]) -> str:
    from .dashboard import project_href, todo_href

    delivered = (f"{escape(order.delivered_to)} · {escape(_kst(order.delivered_at))} 전달됨" if order.delivered_at
                 else '<span class="muted">아직 전달 전</span>')
    rows = [
        ("상태", _steps(order.status)),
        ("보낸 때", escape(_kst(order.created_at))),
        ("받는 도리", f"{escape(order.receiver)}<div class=\"small muted\">{delivered}</div>"),
        ("맡은 도리", escape(order.dori) if order.dori else '<span class="muted">아직 정해지지 않음</span>'),
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
    if order.note:
        rows.append(("메모", escape(order.note)))
    facts = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    history = "".join(_event(order, e.at, e.kind, e.detail, e.actor) for e in reversed(order.events))
    return (
        f'<section data-tab="order" data-key="{order.number}" data-title="지시 #{order.number}">'
        f'<a class="back" href="#orders" data-back>{icon("chevron-left")}<span>도리에게 지시</span></a>'
        f'<div class="pv-head"><h2>지시 #{order.number}</h2></div>'
        f'<div class="card"><h3>지시 내용</h3><p class="order-body">{escape(order.body)}</p></div>'
        f'<div class="card"><dl class="td-facts">{facts}</dl></div>'
        f'<div class="card"><h3>기록</h3><ul class="td-log">{history}</ul></div></section>'
    )


def todo_orders(orders: Sequence[Order], todo_id: str) -> str:
    mine = [o for o in orders if o.todo_id == todo_id]
    items = "".join(
        f'<li><a href="{escape(order_href(o.number), quote=True)}">#{o.number} {escape(_first_line(o.body, 40))}</a>'
        f"{_badge(o.status)}</li>" for o in mine
    )
    listed = f'<ul class="td-list">{items}</ul>' if items else ""
    return (
        f'<div class="card"><h3>이 할\u00a0일에 대한 도리 지시</h3>{listed}'
        f'<a class="order-new" href="#orders" data-order-todo="{escape(todo_id, quote=True)}">'
        f'{icon("inbox")}이 할\u00a0일로 도리에게 지시</a></div>'
    )
