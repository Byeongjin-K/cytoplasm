"""The dashboard's look: one token set (light, with a dark override), the stylesheet built on it, and icons.

Every colour, size, gap and radius the page uses is a custom property defined here, so the light
and dark appearances differ only in the token block. Icons are inline Lucide paths (ISC licence),
drawn with ``currentColor`` so they follow the text colour in both appearances. Nothing is
fetched: the page must open offline as a mail attachment.
"""

from __future__ import annotations

import base64
from html import escape
from typing import Final

_TOKENS: Final = """
:root{color-scheme:light dark;
--bg:#f6f7f9;--surface:#fff;--surface-2:#f0f2f5;--line:#e2e5ea;--line-strong:#c9ced6;
--text:#14171c;--text-2:#4a5160;--text-3:#5f6777;
--accent:#2f5bd3;--accent-ink:#2148b0;--accent-wash:#e9effc;--on-accent:#fff;
--danger:#b42318;--danger-wash:#fdecea;--warn:#9a4600;--warn-wash:#fff1df;
--ok:#16703a;--ok-wash:#e4f3e9;--neutral:#3d4452;--neutral-wash:#edeff3;
--shadow:0 1px 2px rgba(16,24,40,.05),0 1px 3px rgba(16,24,40,.04);--shadow-hover:0 4px 14px rgba(16,24,40,.08);
--header:rgba(246,247,249,.86);
--s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:20px;--s6:24px;--s8:32px;--s10:40px;
--r-sm:6px;--r-md:10px;--r-lg:14px;
--t-xs:12px;--t-sm:13px;--t-md:15px;--t-lg:17px;--t-xl:22px;--t-2xl:28px;
--hit:44px;--max:1200px;--ease:cubic-bezier(.2,.7,.2,1)}
@media (prefers-color-scheme:dark){:root{
--bg:#0e1014;--surface:#171a20;--surface-2:#1d2129;--line:#2a2f38;--line-strong:#3b414c;
--text:#eceef2;--text-2:#b4bac6;--text-3:#9aa2b1;
--accent:#8eabff;--accent-ink:#a9c0ff;--accent-wash:#1e2944;--on-accent:#0e1014;
--danger:#ff9a90;--danger-wash:#3b1815;--warn:#ffb46b;--warn-wash:#3a2611;
--ok:#76d9a0;--ok-wash:#12301f;--neutral:#cdd2db;--neutral-wash:#262b34;
--shadow:none;--shadow-hover:0 4px 14px rgba(0,0,0,.35);--header:rgba(14,16,20,.82)}}
"""

_BASE: Final = """
*{box-sizing:border-box}
html{scroll-padding-top:80px;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:var(--t-md)/1.55 system-ui,-apple-system,BlinkMacSystemFont,
"Apple SD Gothic Neo","Pretendard","Malgun Gothic","Noto Sans KR",sans-serif;letter-spacing:-.01em;
word-break:keep-all;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
a{color:var(--accent-ink);text-decoration:none}
a:hover{text-decoration:underline;text-underline-offset:3px}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:var(--r-sm)}
.ico{width:18px;height:18px;flex:none;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.skip{position:absolute;left:var(--s4);top:-60px;z-index:20;background:var(--surface);color:var(--text);
padding:var(--s2) var(--s4);border-radius:var(--r-md);box-shadow:var(--shadow-hover)}
.skip:focus{top:var(--s3)}
header{position:sticky;top:0;z-index:10;background:var(--header);backdrop-filter:saturate(1.4) blur(14px);
-webkit-backdrop-filter:saturate(1.4) blur(14px);border-bottom:1px solid var(--line)}
.bar{max-width:var(--max);margin:0 auto;padding:var(--s2) var(--s6);display:flex;align-items:center;gap:var(--s6);min-height:60px}
.brand{display:flex;align-items:center;gap:var(--s3);min-height:var(--hit);font-weight:700;font-size:var(--t-lg);color:var(--text);white-space:nowrap}
.mark{width:30px;height:30px;border-radius:9px;background:var(--accent);color:var(--on-accent);display:grid;place-items:center;
font-size:var(--t-md);font-weight:800}
nav{display:flex;gap:var(--s1)}
nav a{display:flex;align-items:center;gap:6px;min-height:var(--hit);padding:0 var(--s3);border-radius:var(--r-md);color:var(--text-2);
font-size:var(--t-md);font-weight:500;text-decoration:none;position:relative}
nav a .ico{display:none}
nav a:hover{background:var(--surface-2);color:var(--text);text-decoration:none}
nav a.on{background:var(--accent-wash);color:var(--accent-ink);font-weight:650}
main>[hidden],main [hidden]{display:none!important}
main{max-width:var(--max);margin:0 auto;padding:var(--s6) var(--s6) var(--s10)}
main>section{margin-top:0}
h2{font-size:var(--t-xl);line-height:1.3;margin:0;font-weight:700;letter-spacing:-.02em}
h3{font-size:var(--t-lg);margin:0;font-weight:650}
.head{display:flex;align-items:flex-end;justify-content:space-between;gap:var(--s4);flex-wrap:wrap;margin-bottom:var(--s5)}
.sub{color:var(--text-3);font-size:var(--t-sm);margin:var(--s1) 0 0}
.muted{color:var(--text-3)} .small{font-size:var(--t-sm)}
.note{font-size:var(--t-sm);color:var(--text-3);margin:var(--s2) 0 0}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--s4) var(--s5);box-shadow:var(--shadow)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:var(--s4)}
.stack>*+*{margin-top:var(--s3)}
"""

_PARTS: Final = """
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:var(--s4)}
.tile{display:flex;flex-direction:column;gap:var(--s2);padding:var(--s4) var(--s5);background:var(--surface);
border:1px solid var(--line);border-radius:var(--r-lg);box-shadow:var(--shadow);color:var(--text);text-decoration:none}
a.tile:hover{text-decoration:none;border-color:var(--line-strong);box-shadow:var(--shadow-hover)}
.tile-top{display:flex;align-items:center;gap:var(--s2);color:var(--text-2);font-size:var(--t-sm);font-weight:500}
.tile b{font-size:var(--t-2xl);line-height:1.1;font-weight:700;letter-spacing:-.02em}
.tile.is-danger .tile-top,.tile.is-danger b{color:var(--danger)}
.tile.is-warn .tile-top,.tile.is-warn b{color:var(--warn)}
.tile.is-info .tile-top,.tile.is-info b{color:var(--accent-ink)}
.callout{display:flex;gap:var(--s3);margin-top:var(--s5);padding:var(--s4) var(--s5);background:var(--surface-2);
border-radius:var(--r-lg);border:1px solid var(--line)}
.callout>.ico{margin-top:3px;color:var(--text-2)}
.callout ul{margin:var(--s2) 0 0;padding-left:var(--s5)}
.callout li{margin-top:var(--s1)}
.section-title{display:flex;align-items:baseline;gap:var(--s3);margin:var(--s8) 0 var(--s4)}
.section-title h3{font-size:var(--t-lg)}
.badge{display:inline-flex;align-items:center;gap:3px;padding:2px 8px;border-radius:var(--r-sm);font-size:var(--t-xs);
font-weight:600;white-space:nowrap;line-height:1.5}
.d-over,.g-2,.g-3{background:var(--danger-wash);color:var(--danger)}
.d-soon{background:var(--warn-wash);color:var(--warn)}
.d-far,.d-none,.g-1,.st-open,.st-held{background:var(--neutral-wash);color:var(--neutral)}
.st-doing{background:var(--accent-wash);color:var(--accent-ink)}
.st-done,.g-0{background:var(--ok-wash);color:var(--ok)}
a.pcard{display:flex;flex-direction:column;gap:var(--s2);color:inherit;text-decoration:none;transition:box-shadow .15s var(--ease),border-color .15s var(--ease)}
a.pcard:hover{border-color:var(--line-strong);box-shadow:var(--shadow-hover);text-decoration:none}
.pcard-top{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--s3)}
.pcard-top b{font-size:var(--t-lg);font-weight:650;line-height:1.35}
.pcard-top .ico{color:var(--text-3);margin-top:3px}
.meter{display:flex;align-items:center;gap:var(--s3);margin-top:var(--s1)}
progress.prog{flex:1;height:8px;border:0;border-radius:999px;background:var(--neutral-wash);overflow:hidden;-webkit-appearance:none;appearance:none}
progress.prog::-webkit-progress-bar{background:var(--neutral-wash);border-radius:999px}
progress.prog::-webkit-progress-value{background:var(--accent);border-radius:999px}
progress.prog::-moz-progress-bar{background:var(--accent);border-radius:999px}
.meter span{font-size:var(--t-sm);font-weight:600;color:var(--text-2);min-width:3.2em;text-align:right}
.facts{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:var(--t-sm);color:var(--text-2)}
.filters{display:grid;grid-template-columns:minmax(200px,280px) 1fr 160px auto;gap:var(--s3);align-items:end;margin-bottom:var(--s5)}
.field{display:flex;flex-direction:column;gap:6px;font-size:var(--t-sm);color:var(--text-2);font-weight:500;min-width:0}
.control{position:relative;display:flex;align-items:center}
.control .ico{position:absolute;left:12px;color:var(--text-3);pointer-events:none}
.field input,.field select{font:inherit;font-size:var(--t-md);color:var(--text);background:var(--surface);
border:1px solid var(--line-strong);border-radius:var(--r-md);min-height:var(--hit);padding:0 var(--s3);width:100%}
.field input{padding-left:38px}
.field input::placeholder{color:var(--text-3)}
.field select{cursor:pointer}
.field input:hover,.field select:hover{border-color:var(--text-3)}
.count{align-self:center;justify-self:end;font-size:var(--t-sm);color:var(--text-3);white-space:nowrap;padding-top:22px}
ul.todos{list-style:none;margin:0;padding:0}
ul.todos li{display:flex;gap:var(--s2);align-items:flex-start;padding:var(--s2) 0;border-top:1px solid var(--line)}
ul.todos li:first-child{border-top:0}
.check{display:grid;place-items:center;width:var(--hit);height:var(--hit);flex:none;margin:-4px 0 0 -10px;cursor:pointer;border-radius:var(--r-md)}
.check:hover{background:var(--surface-2)}
.check input{width:20px;height:20px;margin:0;accent-color:var(--accent);cursor:pointer}
.check input:disabled{cursor:default}
.todo-main{flex:1;min-width:0;padding-top:6px}
.todo-title{font-weight:600;line-height:1.45}
li.done .todo-title{text-decoration:line-through;color:var(--text-3);font-weight:500}
.meta{display:flex;gap:6px var(--s2);flex-wrap:wrap;align-items:center;font-size:var(--t-sm);color:var(--text-2);margin-top:6px}
.meta .sep{color:var(--line-strong)}
.src-link{display:inline-flex;align-items:center;gap:6px;max-width:100%;min-width:0;min-height:28px;color:var(--text-3);font-size:var(--t-sm);margin-top:2px}
.src-link .ico{width:15px;height:15px}
.src-link span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
a.src-link:hover{color:var(--accent-ink)}
.row-error{display:block;margin-top:6px;font-size:var(--t-sm);color:var(--danger);font-weight:600}
li.saving{opacity:.55}
.agenda ul{list-style:none;margin:0;padding:0}
.agenda li{padding:var(--s3) 0;border-top:1px solid var(--line);display:flex;gap:var(--s2) var(--s3);align-items:baseline;flex-wrap:wrap}
.agenda li:first-child{border-top:0}
.agenda li b{font-weight:600}
.agenda .where{flex-basis:100%;font-size:var(--t-sm);color:var(--text-3);padding-left:0}
.src-card .top{display:flex;flex-wrap:wrap;gap:6px var(--s2);align-items:center;font-size:var(--t-sm);color:var(--text-3)}
.src-card .title{display:block;margin-top:6px;font-size:var(--t-lg);font-weight:650;color:var(--text)}
a.title:hover{color:var(--accent-ink)}
.src-card p{margin:var(--s2) 0 0;color:var(--text-2)}
.names{display:flex;gap:6px;align-items:flex-start;margin-top:var(--s2);font-size:var(--t-sm);color:var(--text-3)}
.names .ico{width:15px;height:15px;margin-top:7px}
.names a{display:inline-flex;align-items:center;min-height:28px}
.names span{line-height:28px}
.empty{display:flex;flex-direction:column;align-items:center;text-align:center;gap:var(--s2);padding:var(--s10) var(--s6);color:var(--text-2)}
.empty .ico{width:28px;height:28px;color:var(--text-3)}
.empty b{color:var(--text);font-size:var(--t-lg)}
.empty p{margin:0;max-width:44ch}
.events{list-style:none;margin:0;padding:0}
.events li{display:flex;gap:var(--s3);padding:var(--s3) 0;border-top:1px solid var(--line);font-size:var(--t-md)}
.events li:first-child{border-top:0}
.events time{flex:none;width:5.5em;color:var(--text-3);font-size:var(--t-sm);padding-top:2px}
.events .what{min-width:0}
.events .who{color:var(--text-3);font-size:var(--t-sm)}
.picker{display:flex;flex-direction:column;gap:6px;margin:0;font-size:var(--t-sm);color:var(--text-2);font-weight:500}
.picker select{font:inherit;font-size:var(--t-md);color:var(--text);background:var(--surface);border:1px solid var(--line-strong);
border-radius:var(--r-md);min-height:var(--hit);padding:0 var(--s3);width:100%;cursor:pointer}
.picker select:hover{border-color:var(--text-3)}
.picker-row{max-width:320px;margin-bottom:var(--s5)}
.btn{font:inherit;font-size:var(--t-md);font-weight:600;display:inline-flex;align-items:center;gap:var(--s2);padding:0 var(--s4);
min-height:var(--hit);border-radius:var(--r-md);border:1px solid var(--line-strong);background:var(--surface);color:var(--text);cursor:pointer}
.btn:hover{background:var(--surface-2)} .btn:active{transform:translateY(1px)}
.btn:disabled{opacity:.5;cursor:default}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.btn.primary:hover{filter:brightness(1.06)}
.back{display:inline-flex;align-items:center;gap:var(--s1);min-height:var(--hit);font-size:var(--t-md);font-weight:500;margin-bottom:var(--s2)}
.pv-head{display:flex;gap:var(--s3);align-items:center;flex-wrap:wrap;margin-bottom:var(--s5)}
.pv-head .btn{margin-left:auto}
.shelf-note{margin-bottom:var(--s5)}
details.fold>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:var(--s2);min-height:var(--hit);user-select:none;border-radius:var(--r-md)}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary::before{content:"";width:8px;height:8px;margin:0 6px 0 4px;border-right:2px solid var(--text-3);
border-bottom:2px solid var(--text-3);transform:rotate(45deg) translate(-2px,-2px);transition:transform .15s var(--ease);flex:none}
details.fold:not([open])>summary::before{transform:rotate(-45deg)}
details.fold>summary h3{margin:0;display:flex;gap:var(--s2);align-items:center;font-size:var(--t-lg)}
details.fold>summary h3 a{color:inherit}
details.fold[open]>summary{margin-bottom:var(--s2)}
details.sect{margin-top:var(--s6)}
details.card{padding-top:var(--s2);padding-bottom:var(--s2)}
details.card+details.card{margin-top:var(--s3)}
.cnt{margin-left:2px}
footer{max-width:var(--max);margin:0 auto;padding:0 var(--s6) var(--s10);color:var(--text-3);font-size:var(--t-sm);display:flex;gap:var(--s2);flex-wrap:wrap}
"""

_RESPONSIVE: Final = """
@media (max-width:900px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}
.filters{grid-template-columns:1fr 1fr}.filters .field-search{grid-column:1/-1;order:-1}.count{grid-column:1/-1;justify-self:start;padding-top:0}}
@media (max-width:640px){html{scroll-padding-top:64px}
header{backdrop-filter:none;-webkit-backdrop-filter:none;background:var(--bg)}
.bar{padding:var(--s2) var(--s4);min-height:52px}
nav{position:fixed;left:0;right:0;bottom:0;z-index:10;display:grid;grid-template-columns:repeat(6,1fr);gap:0;
background:var(--surface);box-shadow:0 -1px 0 var(--line);
border-top:1px solid var(--line);padding:4px 4px calc(4px + env(safe-area-inset-bottom))}
nav a{flex-direction:column;justify-content:center;gap:2px;min-height:52px;padding:0;font-size:11px;font-weight:600;border-radius:var(--r-md)}
nav a .ico{display:block;width:22px;height:22px}
nav a .badge{position:absolute;top:2px;left:calc(50% + 6px);padding:0 5px;font-size:10px;min-width:16px;justify-content:center}
nav a .badge+.badge{display:none}
nav a .badge.d-none{background:var(--neutral);color:var(--surface)}
main{padding:var(--s5) var(--s4) calc(96px + env(safe-area-inset-bottom))}
footer{padding:0 var(--s4) calc(88px + env(safe-area-inset-bottom))}
.card{padding:var(--s3) var(--s4)}
.tile{padding:var(--s3) var(--s4)}
.tile b{font-size:24px}
.filters{grid-template-columns:1fr 1fr;gap:var(--s2) var(--s3)}
.picker-row{max-width:none}
.pv-head .btn{margin-left:0}
.events time{width:4.5em}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
@media print{header,nav,.filters,.picker-row,.check,.skip{display:none!important}body{background:var(--surface)}main{padding:0}}
"""

CSS: Final = _TOKENS + _BASE + _PARTS + _RESPONSIVE

_PATHS: Final = {
    "summary": '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/>'
               '<rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
    "projects": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "todos": '<path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/>',
    "schedule": '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>',
    "sources": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
               '<path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
    "archive": '<rect width="20" height="5" x="2" y="3" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/>'
             '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "mail": '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    "paperclip": '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
    "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    "list": '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
}


def icon(name: str) -> str:
    return f'<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">{_PATHS[name]}</svg>'


def brand_mark(brand: str) -> str:
    return f'<span class="mark" aria-hidden="true">{escape(brand[:1] or "P")}</span>'


def favicon(brand: str) -> str:
    """A data-URI icon (base64, so the offline copy carries no URL text) showing the brand's first letter."""
    letter = escape(brand[:1] or "P")
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='#2f5bd3'/>"
        f"<text x='16' y='22' text-anchor='middle' font-size='17' font-weight='700' fill='#fff' "
        f"font-family='system-ui,sans-serif'>{letter}</text></svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
