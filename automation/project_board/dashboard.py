"""The project dashboard as one self-contained HTML page.

One file serves two places: the loopback server (``mode="served"`` — checkboxes record
progress, sources link to the original mail and attachments) and the daily digest
attachment (``mode="file"`` — read-only, no links, opens offline). Nothing is fetched
from outside: no fonts, no CDN, inline CSS and a few lines of inline script.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from html import escape
from typing import Final, Literal
from urllib.parse import quote

from .config import DEFAULT_BRAND
from .dashboard_theme import CSS, brand_mark, favicon, icon, sprite
from .models import (
    GRADES, NEVER_EXPORTED, OPEN_STATUSES, BoardSnapshot, EventRow, ProjectView, SourceRow, TodoExtra, TodoRow,
)
from .orders import Order
from .sessions import SessionsState
from .sessions_view import render_sessions, summary_line
from .ledger_view import NAV_CSS, LedgerView, render_ledger

Mode = Literal["served", "file"]

_STATUS_CLASS: Final = {"열림": "st-open", "진행": "st-doing", "완료": "st-done", "보류": "st-held"}

_SERVED_JS: Final = """
(function(){
  var PB=window.PB=window.PB||{},token=document.querySelector('meta[name=pb-token]').content,live=document.getElementById('live');
  function say(text){if(live){live.textContent='';live.textContent=text;}}
  PB.say=say;PB.post=post;PB.why=why;
  function attempt(path,body){
    return fetch(path,{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json','X-PB-Token':token},
      body:body});
  }
  function fresh(){
    return fetch(location.pathname,{credentials:'same-origin',cache:'no-store',headers:{'Accept':'text/html'}}).then(function(r){
      if(!r.ok){return false;}
      return r.text().then(function(html){
        var m=new DOMParser().parseFromString(html,'text/html').querySelector('meta[name=pb-token]');
        if(!m){return false;}token=m.content;return true;
      });
    },function(){return false;});
  }
  function post(path,body){
    return attempt(path,body).then(function(r){
      if(r.status!==401&&r.status!==403){return r;}
      return fresh().then(function(ok){return ok?attempt(path,body):r;});
    }).then(function(r){if(!r.ok){throw new Error(r.status);}return r;});
  }
  function why(error){
    var code=String(error&&error.message||'');
    if(code==='401'||code==='403'){return '권한이 없어 저장하지 못했습니다. 대시보드를 다시 열어 주세요.';}
    if(/^[0-9]+$/.test(code)){return '저장하지 못했습니다. 잠시 뒤 다시 시도해 주세요.';}
    return '연결이 끊겨 저장하지 못했습니다. 연결을\u00a0확인하고 다시 눌러 주세요.';
  }
  function note(after,text){
    var box=after.parentNode.querySelector('.row-error');
    if(!box){box=document.createElement('span');box.className='row-error';box.setAttribute('role','alert');after.parentNode.appendChild(box);}
    box.textContent=text;
  }
  var dialog=document.getElementById('archive-dialog'),pending=null;
  function send(b){
    var on=b.dataset.archive==='1',name=b.dataset.archiveProject;
    b.disabled=true;
    return post('/project/'+encodeURIComponent(name),'archived='+(on?'1':'0')).then(function(){
      location.hash=on?'archive':'project/'+encodeURIComponent(name);location.reload();
    }).catch(function(error){b.disabled=false;throw error;});
  }
  PB.bind=function(root){
  root.querySelectorAll('input[data-todo]').forEach(function(box){
    box.addEventListener('change',function(){
      var li=box.closest('li'),main=li.querySelector('.todo-main'),old=li.querySelector('.row-error');
      if(old){old.remove();}
      box.disabled=true;li.classList.add('saving');say('저장하는 중');
      post('/todo/'+encodeURIComponent(box.dataset.todo),'status='+encodeURIComponent(box.checked?'완료':'열림'))
        .then(function(){location.reload();})
        .catch(function(error){box.checked=!box.checked;box.disabled=false;li.classList.remove('saving');
          note(main.lastElementChild,why(error));say(why(error));});
    });
  });
  root.querySelectorAll('button[data-set-status]').forEach(function(b){
    b.addEventListener('click',function(){
      if(b.getAttribute('aria-pressed')==='true'){return;}
      var group=b.parentNode,old=group.parentNode.querySelector('.row-error');if(old){old.remove();}
      [].forEach.call(group.children,function(x){x.disabled=true;});say('저장하는 중');
      post('/todo/'+encodeURIComponent(b.dataset.statusTodo),'status='+encodeURIComponent(b.dataset.setStatus))
        .then(function(){location.reload();})
        .catch(function(error){[].forEach.call(group.children,function(x){x.disabled=false;});note(group,why(error));say(why(error));});
    });
  });
  root.querySelectorAll('button[data-archive]').forEach(function(b){
    b.addEventListener('click',function(){
      if(b.dataset.archive!=='1'||!dialog||!dialog.showModal){
        send(b).catch(function(error){note(b,why(error));say(why(error));});return;
      }
      pending=b;
      dialog.querySelector('[data-name]').textContent=b.dataset.archiveProject;
      dialog.querySelector('.dialog-error').textContent='';
      dialog.showModal();
    });
  });
  };
  PB.bind(document);
  if(dialog){
    dialog.querySelector('[data-confirm]').addEventListener('click',function(event){
      event.preventDefault();if(!pending){return;}
      var ok=event.currentTarget;ok.disabled=true;
      send(pending).catch(function(error){ok.disabled=false;dialog.querySelector('.dialog-error').textContent=why(error);});
    });
  }
})();
"""

_FOLD_JS: Final = """
(function(){
  var key='pb-fold:',PB=window.PB=window.PB||{};
  PB.fold=function(root){
  root.querySelectorAll('details[data-fold]').forEach(function(d){
    try{if(localStorage.getItem(key+d.dataset.fold)==='0'){d.open=false;}}catch(e){}
    d.addEventListener('toggle',function(){
      try{if(d.open){localStorage.removeItem(key+d.dataset.fold);}else{localStorage.setItem(key+d.dataset.fold,'0');}}catch(e){}
    });
  });
  };
  PB.fold(document);
})();
"""

_TABS_JS: Final = """
(function(){
  var PB=window.PB=window.PB||{},title=document.title,sticky='';
  function dec(s){try{return decodeURIComponent(s);}catch(e){return '';}}
  function parse(hash){
    var q=hash.indexOf('?p=');
    if(hash.indexOf('todo/')===0){return {tab:'todo',proj:dec(hash.slice(5))};}
    if(hash.indexOf('person/')===0){return {tab:'person',proj:dec(hash.slice(7))};}
    if(hash.indexOf('order/')===0){return {tab:'order',proj:dec(hash.slice(6))};}
    if(hash.indexOf('project/')===0){return {tab:'project',proj:dec(hash.slice(8))};}
    var sq=hash.indexOf('?s=');if(sq>=0){return {tab:hash.slice(0,sq),proj:'',status:dec(hash.slice(sq+3))};}
    if(q>=0){return {tab:hash.slice(0,q),proj:dec(hash.slice(q+3))};}
    return {tab:hash.indexOf('todo-')===0?'todos':hash.indexOf('sched-')===0?'schedule':hash.indexOf('ss-')===0?'sessions':hash==='events'?'summary':hash,proj:''};
  }
  function pick(panel,proj){
    var sel=panel?panel.querySelector('select[data-picker]'):null;if(!sel){return '';}
    var known=[].some.call(sel.options,function(o){return o.dataset.p===proj;});
    if(!known){proj='';}
    [].forEach.call(sel.options,function(o){if(o.dataset.p===proj){sel.value=o.value;}});
    return proj;
  }
  PB.pickers=function(root){
    root.querySelectorAll('select[data-picker]').forEach(function(sel){
      sel.addEventListener('change',function(){jump=true;location.hash=sel.value;});
    });
  };
  PB.pickers(document);
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
  function show(keep){
    var panels=document.querySelectorAll('main>[data-tab]'),links=document.querySelectorAll('nav a[data-tab]'),
        names=[].map.call(links,function(a){return a.dataset.tab;}).concat(['archive','orders']);
    var hash=location.hash.slice(1),r=parse(hash),tab=r.tab,page=null;
    if(tab==='project'||tab==='todo'||tab==='person'||tab==='order'){
      panels.forEach(function(s){if(s.dataset.tab===tab&&(tab==='project'?s.dataset.project:s.dataset.key)===r.proj){page=s;}});
      if(!page){tab=tab==='project'?'projects':tab==='todo'?'todos':tab==='order'&&names.indexOf('orders')>=0&&document.getElementById('orders')?'orders':'summary';}
    }else if(names.indexOf(tab)<0||(tab==='orders'&&!document.getElementById('orders'))){tab='summary';}
    var navTab=page?(page.dataset.tab==='project'?'projects':page.dataset.tab==='order'?'summary':'todos'):tab==='archive'?'projects':tab==='orders'?'summary':tab;
    panels.forEach(function(s){s.hidden=page?s!==page:s.dataset.tab!==tab;});
    links.forEach(function(a){var on=a.dataset.tab===navTab;a.classList.toggle('on',on);
      if(on){a.setAttribute('aria-current','page');}else{a.removeAttribute('aria-current');}});
    var ss=document.getElementById('todo-s');
    // a filter set by a link (진행 중 tile) lasts while that address is open; the plain 할 일 tab starts unfiltered again
    if(ss&&tab==='todos'&&r.status!==undefined){ss.value=r.status;linkedStatus=true;}
    else if(ss&&tab==='todos'&&linkedStatus){ss.value='';linkedStatus=false;}
    var t=filterTodos(tab==='todos'?r.proj:''),s=filterSchedule(tab==='schedule'?r.proj:'');
    if(page){if(page.dataset.tab==='project'){sticky=r.proj;}}else if(tab==='todos'){sticky=t||'';}else if(tab==='schedule'){sticky=s||'';}
    links.forEach(function(a){var n=a.dataset.tab;if(n==='todos'||n==='schedule'){
      a.setAttribute('href','#'+n+(sticky?'?p='+encodeURIComponent(sticky):''));}});
    document.title=page?(page.dataset.title||r.proj)+' · '+title:title;
    if(keep===true){return;}
    var here=page?(page.dataset.title||r.proj):'';
    if(!here){links.forEach(function(a){if(a.dataset.tab===navTab){here=[].filter.call(a.childNodes,function(n){return n.nodeType===3;})
      .map(function(n){return n.textContent;}).join('').trim();}});}
    var st=history.state||{};
    if(!('pbFrom' in st)){st={pbFrom:moves>0&&last!==here?last:''};try{history.replaceState(st,'');}catch(e){}}
    if(page){var b=page.querySelector('[data-back] span');if(b&&st.pbFrom){b.textContent=st.pbFrom;}}
    last=here;moves++;
    var target=(hash.indexOf('todo-')===0||hash.indexOf('sched-')===0||hash.indexOf('ss-')===0||hash==='pending'||hash==='events')&&document.getElementById(hash),fresh=jump;jump=false;
    if(!fresh&&hash in seen){window.scrollTo(0,seen[hash]);return;}
    if(target&&hash.indexOf('todo-')===0){reveal(target);}
    if(target&&target.tagName==='DETAILS'){target.open=true;}
    if(target){target.scrollIntoView({block:'center'});}else{window.scrollTo(0,0);}
  }
  // a todo opened from 일정·근거·기록 must be on screen: lift a search or status filter that hides it, open its fold
  function reveal(li){
    if(li.hidden){var q=document.getElementById('todo-q'),s=document.getElementById('todo-s');
      if(q){q.value='';}if(s){s.value='';}if(PB.filter){PB.filter();}}
    for(var d=li.closest('details');d;d=d.parentElement&&d.parentElement.closest('details')){d.open=true;}
  }
  // back and forward return to where that screen was left; a link or a picker opens its screen fresh
  var seen={},jump=false,moves=-1,last='',linkedStatus=false;
  // a detail screen back link returns to the screen it was opened from (its name on the link), else to its list
  document.addEventListener('click',function(e){
    var a=e.target.closest&&e.target.closest('a[data-back]');if(a&&(history.state||{}).pbFrom){e.preventDefault();history.back();}
  });
  document.addEventListener('click',function(e){
    var a=e.target.closest&&e.target.closest('a[href]');if(!a){return;}var h=a.getAttribute('href');
    if(h.charAt(0)==='#'&&!(a.hasAttribute('data-back')&&(history.state||{}).pbFrom)){jump=true;}
  },true);
  window.addEventListener('scroll',function(){seen[location.hash.slice(1)]=window.scrollY;},{passive:true});
  PB.show=show;
  window.addEventListener('hashchange',show);show();
})();
"""

_BACK_MARK_JS: Final = """
(function(){
  // a source opened from here remembers which screen to come back to (the reader back link says its name)
  document.addEventListener('click',function(e){
    var a=e.target.closest&&e.target.closest('a[href^="/source/"]');if(!a){return;}
    var on=document.querySelector('nav a.on'),pg=document.querySelector('main>[data-title]:not([hidden]),main>[data-tab=project]:not([hidden])'),name='대시보드';
    if(pg){name=pg.dataset.title||pg.dataset.project;}else if(on){name=[].filter.call(on.childNodes,function(n){return n.nodeType===3;})
      .map(function(n){return n.textContent;}).join('').trim()||name;}
    try{sessionStorage.setItem('pb-back',name);}catch(x){}
  },true);
})();
"""

_REFRESH_JS: Final = """
(function(){
  var PB=window.PB=window.PB||{},main=document.getElementById('main'),EVERY=120000,FLOOR=15000,DAY=86400000,KST=32400000,
      badge=document.getElementById('pb-fresh'),meta=document.querySelector('meta[name=pb-etag]'),WEEK='월화수목금토일',
      tag=meta?meta.content:'',timer=0,ticker=0,last=Date.now(),checked=Date.now(),fault='',held=null,waiting=false,busyFetch=false;
  function keyOf(n){
    if(n.dataset&&n.dataset.tab==='project'){return 'p:'+n.dataset.project;}
    if(n.dataset&&n.dataset.key){return n.dataset.tab+':'+n.dataset.key;}
    return n.id||(n.classList.contains('onboard')?'onboard':n.tagName);
  }
  function norm(n){var c=n.cloneNode(true);c.querySelectorAll('[data-live]').forEach(function(e){e.textContent='';});return c.outerHTML;}
  var base={};[].forEach.call(main.children,function(n){base[keyOf(n)]=norm(n);});
  var nav=document.querySelector('header nav'),navBase=nav?norm(nav):'';
  function kday(ms){return Math.floor((ms+KST)/DAY);}
  function md(ms){var d=new Date(ms+KST);return (d.getUTCMonth()+1)+'월\u00a0'+d.getUTCDate()+'일\u2060('+WEEK[(d.getUTCDay()+6)%7]+')';}
  function dur(ms){
    var m=Math.round(ms/60000);if(m<60){return m+'분';}
    var h=Math.floor(m/60),r=m%60;if(h<48){return r?h+'시간 '+r+'분':h+'시간';}
    var d=Math.floor(h/24);h=h%24;return h?d+'일 '+h+'시간':d+'일';
  }
  function ago(ms,now){
    var m=Math.floor((now-ms)/60000);if(m<1){return '방금';}if(m<60){return m+'분 전';}
    if(m<1440){return Math.floor(m/60)+'시간 전';}
    var d=kday(now)-kday(ms);return d===1?'어제':d<7?d+'일 전':md(ms);
  }
  function when(v){var n=Number(v);return isNaN(n)?Date.parse(v):n;}
  function tick(){
    var now=Date.now();
    document.querySelectorAll('[data-ago]').forEach(function(e){
      var t=when(e.dataset.ago);if(isNaN(t)){return;}
      var text=e.dataset.agoUnit==='dur'?dur(Math.max(0,now-t)):ago(t,now);
      if(e.textContent!==text){e.textContent=text;}
    });
    if(badge){
      var a=ago(checked,now),text=fault||(a==='방금'?'방금 갱신':a+' 갱신');
      if(badge.textContent!==text){badge.textContent=text;}
      badge.classList.toggle('is-error',!!fault);
    }
  }
  var WORDS={
    auth:['다시 로그인 필요','로그인이 만료되어 화면을 새로 받지 못했습니다. 대시보드를 다시 열어 주세요.'],
    net:['연결 끊김 · 다시 시도','연결이 끊겨 화면을 새로 받지 못했습니다. 2분 뒤 다시 시도합니다.'],
    http:['갱신 실패 · 다시 시도','화면을 새로 받지 못했습니다. 2분 뒤 다시 시도합니다.']
  };
  function fail(kind){
    var w=WORDS[kind]||WORDS.http,first=fault!==w[0];fault=w[0];
    if(badge){badge.title=w[1];}if(first&&PB.say){PB.say(w[1]);}tick();
  }
  function ok(){
    checked=Date.now();if(fault&&PB.say){PB.say('화면을 다시 최신으로 받았습니다.');}fault='';
    if(badge){badge.title='2분마다 바뀐 내용만 새로 받습니다';}tick();
  }
  function visible(e){return e.getClientRects().length>0;}
  function sel(e){return e.id?'#'+CSS.escape(e.id):'details[data-fold=\"'+CSS.escape(e.dataset.fold)+'\"]';}
  function pin(){
    var y=window.scrollY;if(y<=0){return null;}
    var list=main.querySelectorAll('[id],details[data-fold]');
    for(var i=0;i<list.length;i++){
      var e=list[i];if(!visible(e)){continue;}
      var t=e.getBoundingClientRect().top;if(t>=0){return {s:sel(e),t:t,y:y};}
    }
    return {s:'',t:0,y:y};
  }
  function restore(m){
    if(!m){return;}
    var e=null;
    if(m.s){[].some.call(main.querySelectorAll(m.s),function(x){if(visible(x)){e=x;return true;}return false;});}
    if(e){window.scrollBy(0,e.getBoundingClientRect().top-m.t);}else{window.scrollTo(0,m.y);}
  }
  function wire(node){['fold','pickers','bind','filterBind'].forEach(function(n){if(PB[n]){PB[n](node);}});}
  function folds(root){
    var out=[],i=0;
    root.querySelectorAll('details').forEach(function(d){out.push([d.dataset.fold?'f:'+d.dataset.fold:'i:'+(i++),d]);});
    return out;
  }
  function carry(old,node){
    wire(node);
    var open={};folds(old).forEach(function(p){open[p[0]]=p[1].open;});
    folds(node).forEach(function(p){if(p[0] in open&&p[1].open!==open[p[0]]){p[1].open=open[p[0]];}});
    old.querySelectorAll('input[id],select[id],textarea[id]').forEach(function(e){
      if(e.type==='checkbox'){return;}
      var n=node.querySelector('#'+CSS.escape(e.id));if(n&&n.value!==e.value){n.value=e.value;}
    });
  }
  function topOf(e){while(e&&e.parentNode!==main){e=e.parentNode;}return e;}
  function apply(){
    if(!held){return;}
    var doc=held.doc,next=[].map.call(doc.getElementById('main').children,function(n){return {k:keyOf(n),n:n,s:norm(n)};});
    var f=document.activeElement,typing=f&&main.contains(f)&&/^(INPUT|TEXTAREA|SELECT)$/.test(f.tagName)&&f.type!=='checkbox';
    var top=typing?topOf(f):null;
    if(top&&next.some(function(p){return p.k===keyOf(top)&&p.s!==base[p.k];})){
      if(!waiting){waiting=true;f.addEventListener('blur',function(){waiting=false;setTimeout(apply,0);},{once:true});}
      return;
    }
    var mark=pin(),olds={},prev=null,changed=0;
    [].forEach.call(main.children,function(n){olds[keyOf(n)]=n;});
    next.forEach(function(p){
      var old=olds[p.k],node=old;
      if(!old||base[p.k]!==p.s){node=document.importNode(p.n,true);changed++;}
      var at=prev?prev.nextSibling:main.firstChild;
      if(node!==at){main.insertBefore(node,at);}
      if(old&&node!==old){carry(old,node);old.remove();}else if(!old){wire(node);}
      delete olds[p.k];base[p.k]=p.s;prev=node;
    });
    Object.keys(olds).forEach(function(k){olds[k].remove();delete base[k];changed++;});
    var nn=doc.querySelector('header nav');
    if(nav&&nn){var s=norm(nn);if(s!==navBase){nav.innerHTML=nn.innerHTML;navBase=s;changed++;}}
    var foot=document.querySelector('body>footer'),nf=doc.querySelector('body>footer');
    if(foot&&nf&&foot.innerHTML!==nf.innerHTML){foot.innerHTML=nf.innerHTML;}
    tag=held.tag||tag;held=null;
    if(changed){if(PB.show){PB.show(true);}if(PB.filter){PB.filter();}restore(mark);}
    tick();
  }
  function busy(){
    var d=document.getElementById('archive-dialog');
    return busyFetch||waiting||(d&&d.open)||!!document.querySelector('main li.saving,main button[data-archive]:disabled');
  }
  function schedule(ms){clearTimeout(timer);timer=0;if(!document.hidden){timer=setTimeout(check,ms);}}
  function check(){
    timer=0;
    if(document.hidden){return;}
    if(busy()){schedule(EVERY);return;}
    last=Date.now();busyFetch=true;
    var head={'Accept':'text/html'};if(tag){head['If-None-Match']=tag;}
    fetch(location.pathname,{credentials:'same-origin',cache:'no-store',headers:head}).then(function(r){
      if(r.status===304){ok();return;}
      if(r.status===401||r.status===403){throw new Error('auth');}
      if(!r.ok){throw new Error('http');}
      var next=r.headers.get('ETag')||'';
      return r.text().then(function(html){
        var doc=new DOMParser().parseFromString(html,'text/html');
        if(!doc.getElementById('main')){throw new Error('http');}
        held={doc:doc,tag:next};apply();ok();
      });
    },function(){throw new Error('net');}).catch(function(e){fail(e&&e.message);}).then(function(){
      busyFetch=false;schedule(EVERY);
    });
  }
  function start(){clearInterval(ticker);ticker=setInterval(tick,30000);}
  document.addEventListener('visibilitychange',function(){
    if(document.hidden){clearTimeout(timer);timer=0;clearInterval(ticker);ticker=0;return;}
    tick();start();var gap=Date.now()-last;schedule(gap>=FLOOR?0:FLOOR-gap);
  });
  PB.refresh=check;
  if(!document.hidden){start();schedule(EVERY);}
})();
"""

_LIVE_TEXT: Final = re.compile(r"(<(span|time)\b[^>]*\bdata-live\b[^>]*>)[^<]*(</\2>)")
_ETAG_META: Final = '<meta name="pb-etag" content="">'


def stamp_etag(page: str) -> tuple[str, str]:
    """The page's ETag and the page carrying it in ``meta[name=pb-etag]``.

    Text the open page keeps current by itself (``data-live``: relative times, the generation stamp) is left
    out, so the tag changes only when what the screen shows changes and an unchanged poll costs a 304.
    """
    digest = hashlib.sha256(_LIVE_TEXT.sub(r"\1\3", page).encode("utf-8")).hexdigest()[:32]
    etag = f'"{digest}"'
    return etag, page.replace(_ETAG_META, f'<meta name="pb-etag" content="{escape(etag, quote=True)}">', 1)

TABS: Final = (
    ("summary", "요약"), ("projects", "프로젝트"), ("todos", "할 일"), ("schedule", "일정"), ("sources", "근거"),
    ("sessions", "세션"),
)
_NAV: Final = "".join(f'<a href="#{tab}" data-tab="{tab}">{icon(tab)}{label}</a>' for tab, label in TABS)
LOCAL_ACTOR: Final = "노드 로컬 접속"

EMPTY_SECTION: Final = (
    f'<div class="card empty">{icon("inbox")}<b>아직 비어 있습니다</b>'
    "<p>새 메일과 회의록을 분석한 제안이 승인되면 이곳에 할\u00a0일과 일정이 채워집니다.</p></div>"
)

_FILTER_JS: Final = """
(function(){
  var PB=window.PB=window.PB||{};
  function apply(){
    var q=document.getElementById('todo-q'),s=document.getElementById('todo-s'),c=document.getElementById('todo-count');
    if(!q||!s||!c){return;}
    var rows=document.querySelectorAll('#todos ul.todos li'),text=q.value.trim().toLowerCase(),want=s.value,proj=document.body.dataset.project||'',shown=0,total=0;
    rows.forEach(function(li){
      var mine=!proj||li.dataset.project===proj;if(mine){total++;}
      var ok=mine&&(!want||li.dataset.status===want)&&(!text||li.textContent.replace(/\\u2060/g,'').toLowerCase().indexOf(text)>=0);
      li.hidden=!ok;if(ok){shown++;}
    });
    document.querySelectorAll('#todos .todo-group').forEach(function(g){
      g.hidden=!g.querySelector('li:not([hidden])');
    });
    c.textContent=total+'건 중 '+shown+'건';
  }
  PB.filter=apply;
  PB.filterBind=function(root){
    var q=root.querySelector('#todo-q'),s=root.querySelector('#todo-s');
    if(q){q.addEventListener('input',apply);}if(s){s.addEventListener('change',apply);}
  };
  PB.filterBind(document);
  document.addEventListener('pb-project',apply);apply();
})();
"""

_WEEKDAYS: Final = "월화수목금토일"

ONBOARDING: Final = (
    f'<div class="card onboard" data-tab="summary"><div class="onboard-head">{icon("inbox")}<div>'
    "<b>처음 오셨군요. 대시보드는 승인된\u00a0할\u00a0일로 채워집니다</b>"
    "<p>세 단계로 데이터가 들어옵니다. 지금은 아무것도 하지 않아도 됩니다.</p></div></div>"
    '<ol class="steps"><li><b>메일과 회의록을 읽습니다</b>'
    "<span>기관 메일과 확정 회의록에서 할\u00a0일, 담당, 마감을 찾아 제안합니다.</span></li>"
    "<li><b>승인\u00a0카드로 확인합니다</b><span>제안은 승인\u00a0카드로 올라오고, 승인 카드에서 승인하면 데이터가 됩니다.</span></li>"
    "<li><b>여기서 진행을 챙깁니다</b><span>프로젝트별 할\u00a0일, 일정, 근거를 보고 체크로 완료를 남깁니다.</span></li></ol>"
    '<p class="note">실제 데이터 없이 둘러보려면 이 명령으로 데모\u00a0보드를 띄웁니다.</p>'
    '<pre class="cmd"><code>python3 -m automation.project_board demo</code></pre></div>'
)

_ARCHIVE_DIALOG: Final = (
    '<dialog id="archive-dialog" aria-labelledby="archive-title"><form method="dialog">'
    '<h2 id="archive-title" tabindex="-1" autofocus>이 프로젝트를 보관할까요?</h2>'
    '<p>「<b data-name></b>」 프로젝트는 요약, 할\u00a0일, 일정, 근거에서 빠지고 보관함에서만 보입니다. '
    "할\u00a0일과\u00a0근거는 지워지지 않고 프로젝트 화면의 <span class=\"q\">「보관 해제」로</span> 언제든 되돌립니다.</p>"
    '<p class="dialog-error" role="alert"></p>'
    '<div class="dialog-actions"><button class="btn" value="cancel">취소</button>'
    '<button class="btn primary" value="ok" data-confirm>보관하기</button></div></form></dialog>'
)


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
    return f"{moment.month}월\u00a0{moment.day}일\u2060({_WEEKDAYS[moment.weekday()]})"


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


_SEP: Final = "\u00a0· "  # a separator never starts a line


def _glued(items: list[str]) -> str:
    """``_SEP.join`` for rows whose items are links: a link drawn inline-block (for its touch height) leaves a line
    break after itself that a no-break space does not close, so Safari began a line with 「·」. Each item and the
    separator after it share one no-wrap span; the item itself still wraps inside."""
    head = "".join(f'<span class="glue">{item}\u00a0·</span> ' for item in items[:-1])
    return head + (items[-1] if items else "")


def title_text(text: str) -> str:
    """A title or mail subject as written: a word joiner before each 「·」 so no line begins with it (Chrome broke
    「조직문화확산·법정의무교육·연구윤리교육」 before the dot under keep-all). Search ignores the joiner."""
    return escape(text).replace("·", "\u2060·")


def _keep(text: str) -> str:
    """Escaped text in which 「할 일」 never breaks across lines (Korean reads it as one word)."""
    return escape(text).replace("할 일", "할\u00a0일")


def _quoted(title: str) -> str:
    """「title」 kept on one line when short enough to fit any screen; long titles may still wrap."""
    kept = _keep(title).replace("·", "\u2060·")
    inner = f"「{kept}」"
    return f'<span class="q">{inner}</span>' if len(title) <= 18 else inner


def _quoted_plain(title: str) -> str:
    """A short title kept on one line inside a comma list (no brackets)."""
    kept = _keep(title).replace("·", "\u2060·")
    return f'<span class="q">{kept}</span>' if len(title) <= 18 else kept


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
    restricted = grade in NEVER_EXPORTED
    hint = " · 외부 비공개" if restricted else ""
    lock = icon("lock") if restricted else ""
    return f'<span class="badge grade {css}" title="업무 보안 등급">{lock}{escape(grade)}{hint}</span>'


def project_href(name: str, tab: str = "project") -> str:
    """``#project/<name>`` opens one project's page; ``#todos?p=<name>`` filters a tab to it."""
    encoded = quote(name, safe="")
    return f"#project/{encoded}" if tab == "project" else f"#{tab}?p={encoded}"


def todo_href(todo_id: str) -> str:
    """``#todo/<id>``: the one screen of a todo. 일정·할 일·근거·기록·사람 rows all open this same screen."""
    return f"#todo/{quote(todo_id, safe='')}"


def person_href(name: str) -> str:
    """``#person/<name>``: everything one person owns, across projects."""
    return f"#person/{quote(name, safe='')}"


def _project_link(name: str) -> str:
    return f'<a href="{escape(project_href(name), quote=True)}">{escape(name)}</a>'


def _person_link(name: str) -> str:
    return f'<a class="who-link" href="{escape(person_href(name), quote=True)}">{escape(name)}</a>'


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


def _shelf_link(shelf: tuple[ProjectView, ...], fresh: dict[str, int]) -> str:
    """보관함 lives at the foot of 프로젝트 (owner Q347); it carries its counts so archived work is still noticed."""
    if not shelf:
        return ""
    new = sum(fresh.values())
    badges = f' <span class="badge d-none">{len(shelf)}</span>' + (
        f' <span class="badge d-soon">새 {new}</span>' if new else ""
    )
    return (f'<a class="card ss-strip shelf-link" href="#archive">{icon("archive")}<span><b>보관함{badges}</b>'
            f'<span class="ss-hubsub">보관한 프로젝트를 보고 되살립니다</span></span>{icon("chevron-right")}</a>')


def _long_day(today: date) -> str:
    return f"{today.year}년 {today.month}월 {today.day}일 {_WEEKDAYS[today.weekday()]}요일"


def _summary(snapshot: BoardSnapshot, today: date, sessions: str = "") -> str:
    open_todos = [t for t in snapshot.todos if t.status in OPEN_STATUSES]
    overdue = sum(1 for t in open_todos if _bucket(t.due, today) == "지연")
    week = sum(1 for t in open_todos if _bucket(t.due, today) == "이번 주")
    doing = sum(1 for t in snapshot.todos if t.status == "진행")
    waiting = len(snapshot.pending)
    week_ago = (today - timedelta(days=7)).isoformat()
    fresh = sum(1 for e in snapshot.events if e.kind == "approved" and e.at[:10] >= week_ago)
    tiles = (
        _tile("alert", "지연", str(overdue), "danger" if overdue else "", "#sched-late" if overdue else "#schedule")
        + _tile("schedule", "이번 주 마감", str(week), "warn" if week else "", "#sched-week" if week else "#schedule")
        + _tile("activity", "진행 중", str(doing), "info" if doing else "", "#todos?s=" + quote("진행", safe=""))
        + (_tile("inbox", "승인 대기", str(waiting), "info", "#pending") if waiting
           else _tile("inbox", "최근 7일 새 할\u00a0일", str(fresh), "info" if fresh else "", "#events"))
    )
    undated = sum(1 for t in open_todos if not t.due)
    undated_line = (
        f'<a class="card ss-strip" href="#sched-none">{icon("schedule")}<span><b>마감이 정해지지 않은 할\u00a0일 {undated}건</b>'
        f'<span class="ss-hubsub">일정의 「미정」 묶음에서 봅니다</span></span>{icon("chevron-right")}</a>'
        if undated else ""
    )
    pending = ""
    if snapshot.pending:
        items = []
        known = {view.name for view in snapshot.projects}
        for p in snapshot.pending:
            name = (f'<a href="{escape(project_href(p.project), quote=True)}"><b>{escape(p.project)}</b></a>'
                    if p.project in known else f"<b>{escape(p.project)}</b>")
            link = f' · <a href="{escape(p.thread_url, quote=True)}">승인 스레드 열기</a>' if p.thread_url else ""
            items.append(
                f"<li>{name} <span class=\"glue\">{_grade_badge(p.grade)}\u00a0·</span> 할\u00a0일\u00a0{p.todo_count}건 · "
                f"근거 {_quoted(p.source_subject)}{link}</li>"
            )
        pending = (
            f'<div class="callout" id="pending">{icon("inbox")}<div><b>승인을 기다리는 제안 {len(items)}건</b>'
            '<p class="note">아직 할 일로 들어오지 않았습니다. 승인 카드에서 승인하면 반영됩니다.</p>'
            f'<ul class="small">{"".join(items)}</ul></div></div>'
        )
    withheld = (
        f'<p class="note">보안 등급(대외비·기관지정) 할\u00a0일\u00a0{snapshot.withheld}건은 이 사본에서 뺐습니다.</p>'
        if snapshot.withheld
        else ""
    )
    projects = ""
    if snapshot.projects:  # owner Q346: the cards live on 프로젝트 only; the summary keeps one line to them
        late = sum(1 for view in snapshot.projects if view.overdue)
        note = f", 지연이 있는 프로젝트 {late}개" if late else ""
        projects = (
            f'<a class="card ss-strip" href="#projects">{icon("projects")}<span><b>프로젝트 {len(snapshot.projects)}개 보기</b>'
            f'<span class="ss-hubsub">진행 중인 프로젝트{note}</span></span>{icon("chevron-right")}</a>'
        )
    return (
        f'<section id="summary" data-tab="summary">{_head("오늘의 현황", "기준일 " + _long_day(today))}'
        f'<div class="tiles">{tiles}</div>{undated_line}{projects}{sessions}{pending}{withheld}</section>'
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
        f'<div class="pcard-top"><b>{escape(view.name)}<span class="sr-only"> 열기</span></b>{icon("chevron-right")}</div>{owner}'
        f'<div class="meter"><progress class="prog" value="{view.done}" max="{max(view.total, 1)}" '
        f'aria-label="진척 {percent}%"></progress><span>{percent}%</span></div>'
        f'<div class="facts"><span>{view.done}/{view.total} 완료</span>{overdue}</div>'
        f"{upcoming_row}{note}</a>"
    )


def _projects(snapshot: BoardSnapshot, today: date, shelf: str = "") -> str:
    cards = "".join(_project_card(view, today) for view in snapshot.projects)
    body = f'<div class="grid">{cards}</div>' if cards else EMPTY_SECTION
    sub = f"진행 중인 프로젝트 {len(snapshot.projects)}개" if cards else ""
    return f'<section id="projects" data-tab="projects">{_head("프로젝트", sub)}{body}{shelf}</section>'


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
    due = f"<span>{_md(todo.due)}</span>" if todo.due else ""
    if not done:
        label, css = _dday(todo.due, today)
        when = escape(project_href(todo.project, "schedule"), quote=True)
        badges += f'<a class="due" href="{when}" title="일정에서 보기"><span class="badge {css}">{label}</span>{due}</a>'
        due = ""
    owner = _person_link(todo.owner) if todo.owner else '<span class="muted">담당 미정</span>'
    href = _source_href(mode, todo.source_kind, todo.source_id)
    subject = title_text(todo.source_subject or todo.source_id)
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
        f'{done_class}>{box}<div class="todo-main"><a class="todo-title" href="{escape(todo_href(todo.todo_id), quote=True)}">{title_text(todo.title)}</a>'
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
    have = Counter(t.status for t in snapshot.todos)
    options = "".join(f'<option value="{s}">{s} {have[s]}건</option>' for s in ("열림", "진행", "완료", "보류") if have[s] or s != "보류")
    filters = (
        f'<div class="filters">{_picker("todos", snapshot)}'
        f'<label class="field field-search">검색<span class="control">{icon("search")}'
        '<input id="todo-q" type="search" placeholder="제목, 담당, 프로젝트" autocomplete="off"></span></label>'
        f'<label class="field">상태<select id="todo-s"><option value="">전체 상태</option>{options}</select></label>'
        '<span id="todo-count" class="count" aria-live="polite"></span></div>'
    )
    return f'<section id="todos" data-tab="todos">{_head("할 일", sub)}{filters}{"".join(blocks)}</section>'


_BUCKET_IDS: Final = {"지연": "late", "이번 주": "week", "다음 주": "next", "이후": "later", "미정": "none"}


def _agenda_cards(
    todos: tuple[TodoRow, ...] | list[TodoRow], today: date, key: str = "s", *, with_project: bool = True,
    mode: Mode = "served",
) -> str:
    """Open todos bucketed by due date (지연·이번 주·다음 주·이후·미정), one card per bucket.

    Every part of a row leads somewhere: the title to that todo's own screen, the project to its page, the owner
    to everything they own, the subject to the source.
    """
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
            project = f'<a href="{escape(project_href(todo.project), quote=True)}">{escape(todo.project)}</a>'
            where = [project] if with_project else []
            where += [_person_link(todo.owner)] if todo.owner else []
            where += [_md(todo.due)] if todo.due else []
            title = f'<a class="ag-title" href="{escape(todo_href(todo.todo_id), quote=True)}">{title_text(todo.title)}</a>'
            href = _source_href(mode, todo.source_kind, todo.source_id)
            kind = "mail" if todo.source_kind == "mail" else "sources"
            subject = title_text(todo.source_subject or todo.source_id)
            source = (f'<a class="src-link" href="{escape(href, quote=True)}" title="근거 원문 열기">{icon(kind)}<span>{subject}</span></a>'
                      if href else f'<span class="src-link">{icon(kind)}<span>{subject}</span></span>')
            items.append(
                f'<li data-project="{escape(todo.project, quote=True)}"><span class="badge {css}">{label}</span>'
                f'{title}{_grade_badge(todo.grade)}'
                f'<span class="where">{_glued(where)}</span>{source}</li>'
            )
        fold = _fold(f"{key}:{name}", name, len(rows), f'<ul>{"".join(items)}</ul>', cls="card bucket fold")
        parts.append(fold.replace("<details ", f'<details id="sched-{_BUCKET_IDS[name]}" ', 1) if key == "s" else fold)
    return "".join(parts)


def _agenda(snapshot: BoardSnapshot, today: date, mode: Mode = "served") -> str:
    cards = _agenda_cards(snapshot.todos, today, mode=mode)
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


def _source_card(
    row: SourceRow, produced: list[TodoRow], mode: Mode, *, with_project: bool = True
) -> str:
    href = _source_href(mode, row.source_kind, row.source_id)
    subject = title_text(row.source_subject or row.source_id)
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
    todos = ", ".join(
        f'<a href="{escape(todo_href(t.todo_id), quote=True)}">{_quoted_plain(t.title)}</a>'
        for t in produced
    ) or "없음"
    top = [_md(row.source_date), kind] + ([escape(_sender(row.source_sender))] if row.source_sender else [])
    top += [f'<a href="{escape(project_href(row.project), quote=True)}">{escape(row.project)}</a>'] if with_project else []
    return (
        f'<div class="card src-card"><div class="top"><span>{_SEP.join(top)}</span>{_grade_badge(row.grade)}</div>'
        f'{title}<p>{_keep(row.summary)}</p>{attachments}'
        f'<div class="names">{icon("list")}<span>만든 할\u00a0일: {todos}</span></div></div>'
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
    anchor = f'href="{escape(todo_href(event.todo_id), quote=True)}"'
    named = f"<a {anchor}>{_quoted(title)}</a> " if title else ""
    if event.kind == "approved":
        added = f"<a {anchor}>{_quoted(event.detail)}</a>" if title else _quoted(event.detail)
        return f"할\u00a0일\u00a0추가 {added}"
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

    def row(e: EventRow) -> str:
        return (
            f'<li><time data-live data-ago="{escape(e.at, quote=True)}" datetime="{escape(e.at, quote=True)}" title="{escape(_kst(e.at), quote=True)}">'
            f"{_ago(e.at, snapshot.generated_at)}</time><div class=\"what\"><span class=\"glue\"><a class=\"ev-project\" "
            f"href=\"{escape(project_href(e.project), quote=True)}\">{escape(e.project)}</a>\u00a0·</span> "
            f"{_event_text(e, titles)}{who(e)}</div></li>"
        )

    ordered = sorted(snapshot.events, key=lambda event: event.at, reverse=True)
    first = "".join(row(e) for e in ordered[:8])
    rest = ordered[8:30]
    more = (
        f'<details class="more"><summary>기록 {len(rest)}건 더 보기</summary>'
        f'<ul class="events">{"".join(row(e) for e in rest)}</ul></details>'
        if rest
        else ""
    )
    return (
        '<section id="events" data-tab="summary"><div class="section-title"><h3>최근 기록</h3></div>'
        f'<div class="card"><ul class="events">{first}</ul>{more}</div></section>'
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
    sub = ("보관한 프로젝트는 요약, 프로젝트, 할\u00a0일, 일정, 근거와 프로젝트 고르기에 나오지 않습니다. "
           "할\u00a0일과\u00a0근거는 지워지지 않고, 프로젝트 화면의 <span class=\"q\">「보관 해제」로</span> 되돌립니다.")

    def line(view: ProjectView) -> str:
        count = (fresh or {}).get(view.name, 0)
        flag = f' <span class="badge d-soon">보관 뒤 새 할 일 {count}</span>' if count else ""
        return f'<div class="small muted">{_archived_line(view, mode)}{flag}</div>'

    cards = "".join(_project_card(view, today, line(view)) for view in shelf)
    body = f'<div class="grid">{cards}</div>' if cards else (
        f'<div class="card empty">{icon("archive")}<b>보관한 프로젝트가 없습니다</b>'
        "<p>끝난 프로젝트는 프로젝트 화면의 <span class=\"q\">「보관」으로</span> 이곳에\u00a0옮길\u00a0수\u00a0있습니다.</p></div>"
    )
    back = f'<a class="back" href="#projects">{icon("chevron-left")}프로젝트</a>'
    return f'<section id="archive" data-tab="archive">{back}{_head("보관함", sub)}{body}</section>'


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
    schedule = escape(project_href(view.name, "schedule"), quote=True)
    tiles = (
        _tile("alert", "지연", str(overdue), "danger" if overdue else "", schedule if not archived else "")
        + _tile("schedule", "이번 주 마감", str(week), "warn" if week else "", schedule if not archived else "")
        + _tile("todos", "완료", f"{view.done}/{view.total}", "", escape(project_href(view.name, "todos"), quote=True) if todos and not archived else "")
        + (_tile("inbox", "승인 대기", str(len(pending)), "info", "#pending" if not archived else "") if pending
           else _tile("schedule", "마감 미정", str(sum(1 for t in open_todos if not t.due)), "", schedule if not archived else ""))
    )
    items = "".join(_todo_item(t, mode, today, anchor=False) for t in sorted(todos, key=_todo_order))
    todo_block = f'<div class="card"><ul class="todos">{items}</ul></div>' if items else (
        f'<div class="card empty">{icon("todos")}<b>할 일이 없습니다</b><p>이 프로젝트에서 승인된 할 일이 아직 없습니다.</p></div>'
    )
    key = f"p:{view.name}"
    agenda = _agenda_cards(todos, today, f"{key}:s", with_project=False, mode=mode) or (
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


_SETTABLE: Final = ("열림", "진행", "완료")


def _sender(raw: str) -> str:
    """A mail header's sender as people write it: ``"이름" <주소>`` (even with escaped quotes) reads 이름 <주소>."""
    text = raw.replace('\\"', '"').strip()
    match = re.match(r'^"([^"]*)"\s*(<[^>]*>)?$', text)
    return f"{match.group(1)} {match.group(2) or ''}".strip() if match else text


def _calendar_day(due: str) -> str:
    """The owner's calendar opened on that day — the place to see what else is on it, or the meeting itself."""
    day = date.fromisoformat(due)
    return f"https://calendar.google.com/calendar/r/day/{day.year}/{day.month}/{day.day}"


def _todo_page(
    todo: TodoRow, full: BoardSnapshot, mode: Mode, today: date, extra: TodoExtra | None = None, orders: str = "",
) -> str:
    """A todo's own screen: when, who, which project, why (the source and its summary), what else came with it, history."""
    source = next((s for s in full.sources if (s.source_kind, s.source_id) == (todo.source_kind, todo.source_id)), None)
    status = f"{todo.status} {todo.progress}%" if todo.status == "진행" else todo.status
    badge = f'<span class="badge {_STATUS_CLASS.get(todo.status, "st-open")}">{escape(status)}</span>'
    if mode == "served":
        buttons = "".join(
            f'<button type="button" class="btn seg-btn" data-set-status="{s}" data-status-todo="{escape(todo.todo_id, quote=True)}"'
            f' aria-pressed="{"true" if todo.status == s else "false"}">{s}</button>'
            for s in _SETTABLE
        )
        done = f'<span class="small muted">진행률 {todo.progress}%</span>' if todo.status == "진행" and todo.progress else ""
        badge = f'<div class="seg" role="group" aria-label="상태 바꾸기">{buttons}</div>{done}'
    schedule = escape(project_href(todo.project, "schedule"), quote=True)
    if todo.due:
        label, css = _dday(todo.due, today)
        flag = f' <span class="badge {css}">{label}</span>' if todo.status in OPEN_STATUSES else ""
        clock = f" {escape(extra.time)}" if extra and extra.time else ""
        due = f'<a href="{schedule}">{_md(todo.due)}{clock}</a>{flag}'
        if mode == "served":
            due += (f'<div><a href="{escape(_calendar_day(todo.due), quote=True)}" target="_blank" rel="noopener">'
                    f'{icon("schedule")}캘린더에서 그날 보기</a></div>')
    else:
        due = f'<a href="{schedule}">마감 미정</a>'
    owner = _person_link(todo.owner) if todo.owner else '<span class="muted">담당 미정</span>'
    href = _source_href(mode, todo.source_kind, todo.source_id)
    subject = title_text(todo.source_subject or todo.source_id)
    where = f'<a href="{escape(href, quote=True)}">{icon("mail" if todo.source_kind == "mail" else "sources")}{subject}</a>' if href else subject
    if source is not None:
        where += f'<div class="small muted">{_SEP.join([_md(source.source_date)] + ([escape(_sender(source.source_sender))] if source.source_sender else []))}</div>'
    rows = [("상태", badge), ("마감", due)]
    rows += [("마감 근거", escape(extra.due_reason))] if extra and extra.due_reason else []
    rows += [("장소", escape(extra.place))] if extra and extra.place else []
    rows += [("담당", owner),
            ("프로젝트", f'<a href="{escape(project_href(todo.project), quote=True)}">{escape(todo.project)}</a>'),
            ("보안 등급", _grade_badge(todo.grade)), ("근거", where)]
    rows += [("메모", escape(todo.note))] if todo.note else []
    rows += [("근거 요약", _keep(source.summary))] if source is not None and source.summary else []
    facts = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    siblings = [t for t in full.todos if t.proposal_id == todo.proposal_id and t.todo_id != todo.todo_id]
    same = ""
    if siblings:
        items = "".join(
            f'<li><a href="{escape(todo_href(t.todo_id), quote=True)}">{title_text(t.title)}</a>'
            f'<span class="badge {_STATUS_CLASS.get(t.status, "st-open")}">{escape(t.status)}</span></li>'
            for t in siblings
        )
        same = f'<div class="card"><h3>같은 근거에서 나온 할\u00a0일</h3><ul class="td-list">{items}</ul></div>'
    titles = {t.todo_id: t.title for t in full.todos}
    mine = sorted((e for e in full.events if e.todo_id == todo.todo_id), key=lambda e: e.at, reverse=True)
    history = "".join(f'<li><time datetime="{escape(e.at, quote=True)}">{_keep(_kst(e.at))}</time>'
                      f'<div class="what">{_event_text(e, titles)}</div></li>' for e in mine[:12])
    log = f'<div class="card"><h3>기록</h3><ul class="td-log">{history}</ul></div>' if history else ""
    return (
        f'<section data-tab="todo" data-key="{escape(todo.todo_id, quote=True)}" data-title="{escape(todo.title, quote=True)}">'
        f'<a class="back" href="#todos" data-back>{icon("chevron-left")}<span>할\u00a0일</span></a>'
        f'<div class="pv-head"><h2>{title_text(todo.title)}</h2></div>'
        f'<div class="card"><dl class="td-facts">{facts}</dl></div>{same}{orders}{log}</section>'
    )


def _person_page(name: str, todos: list[TodoRow], today: date) -> str:
    """Everything one person owns: open first by due date, then done, each opening its own screen."""
    ordered = sorted(todos, key=_todo_order)
    items = "".join(
        f'<li data-project="{escape(t.project, quote=True)}"><a class="ag-title" href="{escape(todo_href(t.todo_id), quote=True)}">'
        f'{title_text(t.title)}</a><span class="where">'
        f'{_glued([_project_link(t.project)] + ([_md(t.due)] if t.due else []))}</span>'
        f'<span class="badge {_STATUS_CLASS.get(t.status, "st-open")}">{escape(t.status)}</span></li>'
        for t in ordered
    )
    open_count = sum(1 for t in todos if t.status in OPEN_STATUSES)
    return (
        f'<section data-tab="person" data-key="{escape(name, quote=True)}" data-title="{escape(name, quote=True)}">'
        f'<a class="back" href="#todos" data-back>{icon("chevron-left")}<span>할\u00a0일</span></a>'
        f'<div class="pv-head"><h2>{escape(name)}</h2><span class="small muted">맡은 할\u00a0일 {len(todos)}건 · 열림 {open_count}건</span></div>'
        f'<div class="card agenda"><ul>{items}</ul></div></section>'
    )


def render_dashboard(
    snapshot: BoardSnapshot, *, mode: Mode, token: str = "", brand: str = DEFAULT_BRAND,
    sessions: SessionsState = "missing", now: datetime | None = None,
    ledger: LedgerView | None = None, extras: Mapping[str, TodoExtra] | None = None,
    orders: Sequence[Order] | None = None, order_receiver: str = "",
) -> str:
    from .orders_view import ORDERS_CSS, ORDERS_JS, order_pages, orders_section, summary_box, todo_orders

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
        pending=tuple(p for p in full.pending if p.project not in hidden),
    )
    new_in_shelf = _fresh_in_shelf(full, shelf)
    now_ms = int((now or datetime.now(UTC)).timestamp() * 1000)
    empty = ONBOARDING if not snapshot.todos and not snapshot.pending else ""
    served = mode == "served"
    orders = orders if served else None  # the owner's own words never leave in the exported file
    meta = f'<meta name="pb-token" content="{escape(token, quote=True)}">{_ETAG_META}' if served else ""
    # the refresh script runs first: it notes the page as the server sent it, before the others change it
    scripts = (_REFRESH_JS if served else "") + _FOLD_JS + _TABS_JS + _FILTER_JS + (_SERVED_JS + _BACK_MARK_JS if served else "")
    scripts += ORDERS_JS if orders is not None else ""
    fresh = '<span id="pb-fresh" class="fresh" title="2분마다 바뀐 내용만 새로 받습니다">방금 갱신</span>' if served else ""
    pages = "".join(
        _project_page(i, view, full, mode, today, archived=view in shelf)
        for i, view in enumerate(snapshot.projects + shelf, 1)
    ) + "".join(
        _todo_page(t, full, mode, today, (extras or {}).get(t.todo_id),
                   todo_orders(orders, t.todo_id) if orders is not None else "")
        for t in full.todos
    )
    order_html = ""
    if orders is not None:
        pages += order_pages(orders, full)
        order_html = orders_section(orders, snapshot, order_receiver)
    people: dict[str, list[TodoRow]] = defaultdict(list)
    for todo in full.todos:
        if todo.owner:
            people[todo.owner].append(todo)
    pages += "".join(_person_page(person, rows, today) for person, rows in sorted(people.items()))
    name = escape(brand or DEFAULT_BRAND)
    ledger = ledger if served else None
    ledger_nav = f'<a href="#ledger" data-tab="ledger">{icon("todos")}장부</a>' if ledger is not None else ""
    nav_class = ' class="ledger-nav"' if ledger is not None else ""
    ledger_css = NAV_CSS if ledger is not None else ""
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'{meta}<title>{name}</title><link rel="icon" href="{favicon(brand or DEFAULT_BRAND)}">'
        f"<style>{CSS}{ledger_css}{ORDERS_CSS if orders is not None else ''}</style></head><body>"
        f'{sprite()}<a class="skip" href="#main">본문으로 건너뛰기</a>'
        f'<header><div class="bar"><a class="brand" href="#summary">{brand_mark(brand or DEFAULT_BRAND)}{name}</a>'
        f'<nav aria-label="화면"{nav_class}>{_NAV}{ledger_nav}</nav>{fresh}</div></header><main id="main">'
        f"{empty}{_summary(snapshot, today, '' if mode == 'file' else summary_line(sessions))}"
        f"{summary_box(orders, snapshot) if orders is not None else ''}{_projects(snapshot, today, _shelf_link(shelf, new_in_shelf))}"
        f"{_todos(snapshot, mode, today)}{_agenda(snapshot, today, mode)}{_sources(snapshot, mode)}{_archive(shelf, mode, today, new_in_shelf)}"
        f"{render_sessions(sessions, now_ms=now_ms, exported=mode == 'file')}{render_ledger(ledger)}{order_html}{_events(snapshot, mode)}{pages}</main>"
        f'<footer><span>{name}</span><span aria-hidden="true">·</span><span>기준일 {_md(snapshot.today)}</span>'
        f'<span aria-hidden="true">·</span><span data-live>{escape(_kst(snapshot.generated_at))} 생성</span></footer>'
        f"{_ARCHIVE_DIALOG if mode == 'served' else ''}"
        '<div id="live" class="sr-only" role="status" aria-live="polite"></div>'
        f"<script>{scripts}</script></body></html>"
    )
