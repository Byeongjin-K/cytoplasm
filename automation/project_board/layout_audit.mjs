// Layout audit of the project board: every tab and every detail screen, at phone and desktop width, light and dark.
//
// WHY: a phone (Safari, 390 wide) showed the 보안 등급 badge and a long 근거 mail subject drawn on the same spot
// (2026-10-21). The earlier audit measured overflow, small targets and dead rows only, never two boxes on one
// spot, and Chrome alone did not show it: WebKit placed a baseline-aligned grid row with a wrapped inline-flex
// link up into the row above. So this audit looks at what the reader sees, in WebKit as well as Chromium:
//   overlap    two visible text lines or chips (badge, button, field) from different elements share pixels
//   clip       text cut by overflow (an ellipsis counts: the reader cannot read the rest)
//   offscreen  a text line runs past the left or right edge of the screen
//   hscroll    the page scrolls sideways
//   smalltext  text under 11px
//   lead-sep   a 「·」 separator begins a line (it belongs at the end of the item before it)
//   under-nav  at the end of the page, content sits under the fixed bottom tab bar
//
// Usage (node 18+ with the playwright package; nothing is installed into the repository):
//   PLAYWRIGHT_DIR=<dir holding node_modules/playwright> node automation/project_board/layout_audit.mjs \
//     [--url <dashboard url with ?t=token>] [--engines webkit,chromium] [--shots <dir>] [--out <file.json>]
//     [--only '#summary,#todo/demo-1-5']   (only these screens)
// Screens: every tab of the bottom bar, #archive, #orders, and each #todo/ #person/ #project/ #order/ and /source/ link.
// Without --url it starts `python3 -m automation.project_board demo` (invented data, loopback, thrown away)
// and stops it at the end. With --url it only reads the page: it never ticks, saves or changes anything.
// Exit 0 when no screen has a finding, 1 when any does (each finding is printed), 2 when it cannot run.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith('--') ? [...acc, [a.slice(2), all[i + 1]]] : acc), []),
);
const ENGINES = (args.engines || 'webkit,chromium').split(',');
const SHOTS = args.shots || '';
const VIEWS = [
  { name: '390', width: 390, height: 844, mobile: true },
  { name: '390-bars', width: 390, height: 664, mobile: true }, // Safari with the address and tool bars expanded
  { name: '1440', width: 1440, height: 900, mobile: false },
];
const SCHEMES = ['light', 'dark'];

function loadPlaywright() {
  const dir = process.env.PLAYWRIGHT_DIR || process.cwd();
  try {
    return createRequire(path.join(path.resolve(dir), 'noop.js'))('playwright');
  } catch {
    console.error(`LAYOUT-AUDIT-UNAVAILABLE playwright not found under ${dir}; set PLAYWRIGHT_DIR`);
    process.exit(2);
  }
}

function startDemo() {
  const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
  const child = spawn('python3', ['-m', 'automation.project_board', 'demo', '--port', '0'], { cwd: repo, stdio: ['ignore', 'pipe', 'inherit'] });
  return new Promise((resolve, reject) => {
    let buf = '';
    child.stdout.on('data', (d) => {
      buf += d;
      const m = buf.match(/DEMO (http\S+)/);
      if (m) resolve({ url: m[1], child });
    });
    child.on('exit', (code) => reject(new Error(`demo exited ${code}`)));
  });
}

// Runs inside the page. Returns the findings for the screen as it is now.
function probe() {
  const vw = innerWidth;
  const visible = (el) => {
    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.position === 'absolute' && cs.clip && cs.clip !== 'auto') return false; // .sr-only
    }
    return true;
  };
  const pinned = (el) => { for (let e = el; e; e = e.parentElement) { const p = getComputedStyle(e).position; if (p === 'fixed' || p === 'sticky') return true; } return false; };
  const name = (el) => {
    const cls = typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).join('.') : '';
    return `${el.tagName.toLowerCase()}${cls} "${(el.textContent || '').trim().slice(0, 40)}"`;
  };
  const items = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) {
    if (!n.textContent.trim() || !n.parentElement || !visible(n.parentElement)) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) if (r.width > 1 && r.height > 1) items.push({ el: n.parentElement, r, text: true });
  }
  for (const el of document.body.querySelectorAll('*')) {
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    const chip = cs.display.startsWith('inline') && (cs.backgroundColor !== 'rgba(0, 0, 0, 0)' || parseFloat(cs.borderTopWidth) > 0);
    if (chip || ['svg', 'IMG', 'INPUT', 'SELECT', 'BUTTON'].includes(el.tagName)) items.push({ el, r: el.getBoundingClientRect(), text: false, icon: el.tagName === 'svg' });
  }
  const found = [];
  const flow = items.filter((i) => !pinned(i.el)).map((i) => ({ ...i, t: i.r.top + scrollY, b: i.r.bottom + scrollY, l: i.r.left, rt: i.r.right }));
  flow.sort((x, y) => x.t - y.t);
  for (let i = 0; i < flow.length; i++) {
    for (let j = i + 1; j < flow.length && flow[j].t < flow[i].b; j++) {
      const A = flow[i], B = flow[j];
      if (A.el === B.el || A.el.contains(B.el) || B.el.contains(A.el)) continue;
      if (!A.text && !B.text && (A.icon || B.icon)) continue; // an icon drawn inside a field's padding is by design
      const w = Math.min(A.rt, B.rt) - Math.max(A.l, B.l), h = Math.min(A.b, B.b) - Math.max(A.t, B.t);
      if (w > 2 && h > 2) found.push({ type: 'overlap', a: name(A.el), b: name(B.el), px: `${Math.round(w)}x${Math.round(h)}` });
    }
  }
  for (const el of document.querySelectorAll('main *, .reader *')) {
    if (!visible(el) || !(el.textContent || '').trim()) continue;
    const cs = getComputedStyle(el);
    if (!/(hidden|clip)/.test(cs.overflowX + cs.overflowY) || el.tagName === 'PROGRESS') continue;
    if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) found.push({ type: 'clip', a: name(el) });
  }
  for (const i of items) {
    if (!i.text) continue;
    if (!pinned(i.el) && (i.r.right > vw + 1 || i.r.left < -1)) found.push({ type: 'offscreen', a: name(i.el) });
    if (parseFloat(getComputedStyle(i.el).fontSize) < 11) found.push({ type: 'smalltext', a: name(i.el) });
  }
  for (const i of items) {
    if (!i.text) continue;
    const block = i.el.closest('li, p, dd, div, td');
    if (!block) continue;
    // the 「·」 glyphs of this text node that start their line: nothing of the same block sits left of them on that line
    const walk = document.createTreeWalker(i.el, NodeFilter.SHOW_TEXT);
    for (let n; (n = walk.nextNode());) {
      for (let k = n.textContent.indexOf('·'); k !== -1; k = n.textContent.indexOf('·', k + 1)) {
        const r = document.createRange(); r.setStart(n, k); r.setEnd(n, k + 1);
        const dot = r.getBoundingClientRect();
        const mid = (dot.top + dot.bottom) / 2;
        if (dot.width < 1 || mid < i.r.top || mid > i.r.bottom) continue;
        const before = items.some((o) => o !== i && block.contains(o.el) && o.r.right <= dot.left + 1 && o.r.top < mid && o.r.bottom > mid);
        const sameNodeBefore = (() => { if (k === 0) return false; const q = document.createRange(); q.setStart(n, 0); q.setEnd(n, k); return [...q.getClientRects()].some((x) => x.width > 1 && x.top < mid && x.bottom > mid && x.right <= dot.left + 1 && /\S/.test(n.textContent.slice(0, k).split('\n').pop())); })();
        if (!before && !sameNodeBefore) found.push({ type: 'lead-sep', a: name(i.el) });
      }
    }
  }
  if (document.scrollingElement.scrollWidth > vw + 1) found.push({ type: 'hscroll', a: `${document.scrollingElement.scrollWidth}px` });
  return found;
}

// Runs inside the page after scrolling to the end: the last line of the screen must clear the bottom tab bar.
function underNav() {
  const nav = [...document.querySelectorAll('nav')].find((n) => getComputedStyle(n).position === 'fixed' && n.getBoundingClientRect().height > 0);
  const screen = [...document.querySelectorAll('main > section')].find((s) => s.checkVisibility());
  if (!nav || !screen) return [];
  const top = nav.getBoundingClientRect().top;
  let bottom = 0, who = '';
  for (const e of screen.querySelectorAll('*')) {
    if (!e.checkVisibility() || !(e.textContent || '').trim()) continue;
    const b = e.getBoundingClientRect().bottom;
    if (b > bottom) { bottom = b; who = e.tagName.toLowerCase(); }
  }
  return bottom > top + 1 ? [{ type: 'under-nav', a: `${who} ends at ${Math.round(bottom)}, tab bar starts at ${Math.round(top)}` }] : [];
}

const { webkit, chromium } = loadPlaywright();
const demo = args.url ? null : await startDemo();
const base = args.url || demo.url;
const results = [];
try {
  for (const engineName of ENGINES) {
    const engine = engineName === 'webkit' ? webkit : chromium;
    const browser = await engine.launch(engineName === 'chromium' ? { args: ['--no-sandbox'] } : {});
    try {
      for (const view of VIEWS) for (const scheme of SCHEMES) {
        if (view.name === '390-bars' && scheme === 'dark') continue; // height only moves the tab-bar check
        const context = await browser.newContext({
          viewport: { width: view.width, height: view.height }, isMobile: view.mobile, hasTouch: view.mobile,
          deviceScaleFactor: view.mobile ? 3 : 1, colorScheme: scheme,
        });
        const page = await context.newPage();
        await page.goto(base, { waitUntil: 'networkidle' });
        const origin = new URL(page.url()).origin;
        const screens = await page.evaluate(() => {
          const keys = new Set([...document.querySelectorAll('nav a[href^="#"]')].map((a) => a.getAttribute('href')));
          keys.add('#archive');
          if (document.querySelector('a[href="#orders"]')) keys.add('#orders');
          for (const a of document.querySelectorAll('a[href^="#todo/"], a[href^="#person/"], a[href^="#project/"], a[href^="#order/"], a[href^="/source/"]')) keys.add(a.getAttribute('href'));
          return [...keys];
        });
        const only = args.only ? args.only.split(',') : null;
        for (const key of screens) {
          if (only && !only.includes(decodeURIComponent(key))) continue;
          if (key.startsWith('#')) {
            await page.goto(origin + '/', { waitUntil: 'networkidle' });
            await page.evaluate((h) => { location.hash = h; }, key);
            await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
          } else {
            await page.goto(origin + key, { waitUntil: 'networkidle' });
          }
          await page.evaluate(() => document.fonts.ready);
          const findings = await page.evaluate(probe);
          await page.evaluate(() => window.scrollTo(0, document.scrollingElement.scrollHeight));
          findings.push(...(await page.evaluate(underNav)));
          await page.evaluate(() => window.scrollTo(0, 0));
          const label = decodeURIComponent(key);
          if (SHOTS && findings.length) {
            fs.mkdirSync(SHOTS, { recursive: true });
            await page.screenshot({ path: path.join(SHOTS, `${engineName}-${view.name}-${scheme}-${label.replace(/[^\w가-힣-]+/g, '_')}.png`), fullPage: true });
          }
          results.push({ engine: engineName, view: view.name, scheme, screen: label, findings });
        }
        await context.close();
      }
    } finally {
      await browser.close();
    }
  }
} finally {
  if (demo) demo.child.kill('SIGINT');
}
if (args.out) fs.writeFileSync(args.out, JSON.stringify(results, null, 1));
const bad = results.filter((r) => r.findings.length);
for (const r of bad) for (const f of r.findings) console.log(`FINDING ${r.engine} ${r.view} ${r.scheme} ${r.screen} ${f.type} ${f.a}${f.b ? ' / ' + f.b : ''}`);
console.log(`LAYOUT-AUDIT screens=${results.length} with_findings=${bad.length}`);
process.exit(bad.length ? 1 : 0);
