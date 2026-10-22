"""One way to choose from every dropdown on the board: a field you can type into to narrow the list.

Owner 2026-10-22 "도리에게 할일 ui 에서 드롭 다운이 너무 길어. 찾기 편한방법 적용해. 모든 드롭 다운에 동일한 개선안
적용해". A phone's native picker shows a 40-row wheel with no search, so each ``<select>`` gets a combobox in front of it:
tap it for the whole list (groups kept, the chosen row marked), type to keep only rows whose words or group match,
pick with a tap or with the arrow keys and Enter. The ``<select>`` stays in the form, hidden, and keeps the value:
every existing listener still hears its ``change`` and form posts are unchanged. A short list (8 rows or fewer)
opens without the phone keyboard, since typing would not save a tap there.
"""

from __future__ import annotations

from typing import Final

SHORT: Final = 8

FIND_CSS: Final = (
    ".find{position:relative;display:block;min-width:0}"
    ".find>.find-native{position:absolute;left:0;top:0;width:1px;min-width:0;max-width:1px;height:1px;min-height:0;"
    "padding:0;margin:0;overflow:hidden;clip:rect(0 0 0 0);border:0;opacity:0;pointer-events:none}"
    ".find>.find-input{text-align:left;"
    "font:inherit;font-size:var(--t-md);color:var(--text);width:100%;min-height:var(--hit);box-sizing:border-box;"
    "background:var(--surface);border:1px solid var(--line-strong);border-radius:var(--r-md);padding:0 36px 0 var(--s3);"
    "text-overflow:ellipsis;cursor:pointer}"
    ".find>.find-input:hover{border-color:var(--text-3)}"
    ".find>.find-input:disabled{background:var(--surface-2);color:var(--text-3);cursor:not-allowed}"
    ".find-input::placeholder{color:var(--text-3)}"
    ".find>.find-ico{position:absolute;right:12px;top:0;height:var(--hit);display:flex;align-items:center;"
    "color:var(--text-3);pointer-events:none}"
    ".find-list{position:absolute;z-index:30;left:0;right:0;top:calc(100% + 4px);margin:0;padding:4px 0;list-style:none;"
    "max-height:min(55vh,360px);overflow:auto;overscroll-behavior:contain;background:var(--surface);"
    "border:1px solid var(--line-strong);border-radius:var(--r-md);box-shadow:0 8px 24px rgba(0,0,0,.18)}"
    ".find-list li{display:flex;align-items:center;gap:var(--s2);min-height:var(--hit);padding:6px var(--s3);"
    "font-size:var(--t-md);font-weight:400;color:var(--text);cursor:pointer;overflow-wrap:anywhere}"
    ".find-list li[aria-selected=true]{font-weight:600;color:var(--accent-ink)}"
    ".find-list li[aria-selected=true]::after{content:'\u2713';margin-left:auto;padding-left:var(--s2)}"
    ".find-list li.on{background:var(--accent-wash)}"
    ".find-list li[aria-disabled=true]{color:var(--text-3);cursor:default}"
    ".find-list li.group{min-height:0;padding:10px var(--s3) 4px;font-size:var(--t-sm);font-weight:600;color:var(--text-3);"
    "cursor:default}"
    ".find-list li.none{color:var(--text-3);cursor:default}"
)

FIND_JS: Final = """
(function(){
  var PB=window.PB=window.PB||{},n=0,SHORT=%(short)d,HINT='입력해서 찾기';
  function rows(sel){
    var out=[];
    [].forEach.call(sel.children,function(c){
      if(c.tagName==='OPTGROUP'){[].forEach.call(c.children,function(o){out.push({o:o,g:c.label});});}
      else{out.push({o:c,g:''});}
    });
    return out;
  }
  function chosenText(sel){var o=sel.options[sel.selectedIndex];return o?o.text:'';}
  function sync(box){
    var sel=box.querySelector('select'),inp=box.querySelector('.find-input');
    if(document.activeElement!==inp){inp.value=chosenText(sel);}
    inp.disabled=sel.disabled;
  }
  function build(sel){
    var id='pf'+(++n),label=sel.closest('label'),box=document.createElement('span'),inp=document.createElement('input'),
        list=document.createElement('ul'),all=rows(sel),short=all.length<=SHORT,active=-1,shown=[];
    box.className='find';sel.parentNode.insertBefore(box,sel);box.appendChild(inp);box.appendChild(sel);
    sel.classList.add('find-native');sel.tabIndex=-1;sel.setAttribute('aria-hidden','true');
    inp.type='text';inp.className='find-input';inp.autocomplete='off';inp.spellcheck=false;inp.setAttribute('enterkeyhint','done');
    inp.setAttribute('role','combobox');inp.setAttribute('aria-expanded','false');inp.setAttribute('aria-controls',id);
    inp.setAttribute('aria-autocomplete','list');
    var name=sel.getAttribute('aria-label')||(label?[].filter.call(label.childNodes,function(c){return c.nodeType===3;})
      .map(function(c){return c.textContent;}).join('').trim():'');
    if(name){inp.setAttribute('aria-label',name);}
    if(short){inp.readOnly=true;}else{inp.placeholder=HINT;}
    list.id=id;list.className='find-list';list.setAttribute('role','listbox');list.hidden=true;
    if(name){list.setAttribute('aria-label',name);}
    var mark=document.createElement('span');mark.className='find-ico';
    mark.innerHTML='<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-chevron-down"/></svg>';
    box.appendChild(mark);box.appendChild(list);
    function draw(q){
      q=(q||'').trim().toLowerCase();list.innerHTML='';shown=[];active=-1;var group=null;
      all.forEach(function(r){
        var t=r.o.text,hit=!q||t.toLowerCase().indexOf(q)>=0||r.g.toLowerCase().indexOf(q)>=0;
        if(!hit){return;}
        if(r.g&&r.g!==group){group=r.g;var h=document.createElement('li');h.className='group';h.setAttribute('role','presentation');
          h.textContent=r.g;list.appendChild(h);}
        var li=document.createElement('li');li.id=id+'-'+shown.length;li.setAttribute('role','option');li.textContent=t;
        li.setAttribute('aria-selected',String(r.o.selected));if(r.o.disabled){li.setAttribute('aria-disabled','true');}
        li.dataset.i=shown.length;list.appendChild(li);shown.push({r:r,li:li});
        if(r.o.selected){active=shown.length-1;}
      });
      if(!shown.length){var none=document.createElement('li');none.className='none';none.setAttribute('role','presentation');
        none.textContent='맞는 항목이 없습니다';list.appendChild(none);}
      if(q&&shown.length){active=0;}
      light();
    }
    function light(){
      shown.forEach(function(s,i){s.li.classList.toggle('on',i===active);});
      if(active>=0){inp.setAttribute('aria-activedescendant',shown[active].li.id);shown[active].li.scrollIntoView({block:'nearest'});}
      else{inp.removeAttribute('aria-activedescendant');}
    }
    // the list never runs under the bottom tab bar of a phone: lift the field when there is little room, then fit the list
    function fit(){
      var r=inp.getBoundingClientRect(),room=window.innerHeight-r.bottom-96;
      if(room<220&&r.top>140){window.scrollBy(0,r.top-110);r=inp.getBoundingClientRect();room=window.innerHeight-r.bottom-96;}
      list.style.maxHeight=Math.max(160,Math.min(360,room))+'px';
    }
    function open(){if(!list.hidden||inp.disabled){return;}draw('');list.hidden=false;inp.setAttribute('aria-expanded','true');fit();
      if(!short){inp.value='';inp.placeholder=chosenText(sel)||HINT;}}
    function close(){if(list.hidden){return;}list.hidden=true;inp.setAttribute('aria-expanded','false');
      inp.removeAttribute('aria-activedescendant');inp.value=chosenText(sel);}
    function choose(i){
      var s=shown[i];if(!s||s.r.o.disabled){return;}
      var changed=sel.value!==s.r.o.value;sel.value=s.r.o.value;close();
      if(changed){sel.dispatchEvent(new Event('change',{bubbles:true}));}
    }
    inp.addEventListener('focus',open);
    inp.addEventListener('click',function(){if(list.hidden){open();}});
    inp.addEventListener('input',function(){if(list.hidden){open();}draw(inp.value);});
    inp.addEventListener('keydown',function(e){
      if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();if(list.hidden){open();return;}
        if(shown.length){active=(active+(e.key==='ArrowDown'?1:-1)+shown.length)%%shown.length;light();}}
      else if(e.key==='Enter'){if(!list.hidden){e.preventDefault();choose(active);}}
      else if(e.key==='Escape'){if(!list.hidden){e.preventDefault();close();}}
      else if(e.key==='Tab'){close();}
    });
    list.addEventListener('mousedown',function(e){e.preventDefault();});
    // the list sits inside the label of the field: without preventDefault the label would refocus the field and reopen it
    list.addEventListener('click',function(e){e.preventDefault();var li=e.target.closest('li[role=option]');if(li){choose(+li.dataset.i);}});
    box.addEventListener('focusout',function(e){if(!box.contains(e.relatedTarget)){close();}});
    sel.addEventListener('focus',function(){inp.focus();});
    sel.addEventListener('change',function(){sync(box);});
    sync(box);
  }
  // every select on the page (and in a block the 2-minute refresh swapped in) gets the same find field
  PB.find=function(root){root.querySelectorAll('select:not(.find-native)').forEach(build);};
  // a value a script set (a link that pre-picks a todo, a filter that resets) shows in the field too
  PB.findSync=function(){document.querySelectorAll('.find').forEach(sync);};
  PB.find(document);
  window.addEventListener('hashchange',PB.findSync);
  document.addEventListener('pb-project',PB.findSync);
})();
""" % {"short": SHORT}
