// Generates board.html from data.json. Usage: bun gen.mjs <data.json> <out.html>
const [, , dataPath, outPath] = process.argv;
const D = JSON.parse(await Bun.file(dataPath).text());
const NOW = D.now, W0 = D.w0;

const KST = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, weekday: 'short' });
const parts = t => Object.fromEntries(KST.formatToParts(new Date(t)).map(p => [p.type, p.value]));
const fDay = t => { const p = parts(t); return `${p.month}월 ${p.day}일`; };
const fTime = t => { const p = parts(t); return `${p.hour === '24' ? '00' : p.hour}:${p.minute}`; };
const fFull = t => `${fDay(t)} ${fTime(t)}`;
const fWk = t => parts(t).weekday;
const fRange = (a, b) => {
  if (b == null) return `${fFull(a)} → 진행 중`;
  const pa = parts(a), pb = parts(b);
  const end = pa.month === pb.month ? (pa.day === pb.day ? fTime(b) : `${pb.day}일 ${fTime(b)}`) : fFull(b);
  return `${fFull(a)} → ${end}`;
};
const n = x => x.toLocaleString('ko-KR');
const fDur = ms => {
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m}분`;
  const h = Math.floor(m / 60), r = m % 60;
  if (h < 48) return r ? `${h}시간 ${r}분` : `${h}시간`;
  const d = Math.floor(h / 24), rh = h % 24;
  return rh ? `${d}일 ${rh}시간` : `${d}일`;
};
const fHours = ms => { const h = ms / 36e5; return h < 1 ? `${Math.round(ms / 60000)}분` : `${n(Math.round(h))}시간`; };
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const ICON = {
  hub: 'M12 2l9 5v10l-9 5-9-5V7z M12 22V12 M21 7l-9 5-9-5',
  layers: 'M12 2 2 7l10 5 10-5-10-5Z M2 17l10 5 10-5 M2 12l10 5 10-5',
  bot: 'M12 8V4H8 M4 8h16v12H4z M2 14h2 M20 14h2 M15 13v2 M9 13v2',
  eye: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8Z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.8 1.2V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3.1 14H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 3.1V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 20.9 10H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z',
  mail: 'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z M22 6l-10 7L2 6',
  flask: 'M9 3h6 M10 3v6L4.5 18.5A2 2 0 0 0 6.2 21.5h11.6a2 2 0 0 0 1.7-3L14 9V3 M7 15h10',
  layout: 'M3 3h18v18H3z M3 9h18 M9 21V9',
  book: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20 M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z',
  cpu: 'M6 6h12v12H6z M9.5 9.5h5v5h-5z M9 2v4 M15 2v4 M9 18v4 M15 18v4 M18 9h4 M18 15h4 M2 9h4 M2 15h4',
  chat: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  refresh: 'M23 4v6h-6 M1 20v-6h6 M3.5 9a9 9 0 0 1 14.9-3.4L23 10 M1 14l4.6 4.4A9 9 0 0 0 20.5 15',
  check: 'M22 11.1V12a10 10 0 1 1-5.9-9.1 M22 4 12 14l-3-3',
  doc: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M8 13h8 M8 17h6',
  window: 'M2 4h20v16H2z M2 9h20 M6 6.5h.01 M9 6.5h.01',
  globe: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z M2 12h20 M12 2a15 15 0 0 1 4 10 15 15 0 0 1-4 10 15 15 0 0 1-4-10 15 15 0 0 1 4-10Z',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z M12 6v6l4 2',
  bolt: 'M13 2 3 14h9l-1 8 10-12h-9l1-8z',
  plus: 'M12 5v14 M5 12h14',
  mic: 'M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z M19 10v2a7 7 0 0 1-14 0v-2 M12 19v3',
  chart: 'M3 3v18h18 M7 15l4-4 3 3 6-6',
  key: 'M21 2l-2 2 M15.5 7.5l3 3L22 7l-3-3 M11.4 11.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8Zm0 0L15.5 7.5',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z M23 21v-2a4 4 0 0 0-3-3.9 M16 3.1a4 4 0 0 1 0 7.8',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z M21 21l-4.3-4.3',
};
const svg = (k, s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ICON[k]}"/></svg>`;

const STATUS = {
  done: ['완료', '#4ade80'], run: ['진행', '#60a5fa'], review: ['확인 대기', '#c084fc'], hold: ['멈춤', '#fbbf24'],
  fail: ['미완', '#f87171'], live: ['가동 중', '#4ade80'], ok: ['정상', '#4ade80'], warn: ['실패 있음', '#fbbf24'],
  ended: ['종료', '#8b93a1'], unknown: ['기록 없음', '#8b93a1'],
};
const PRI = ['fail', 'hold', 'warn', 'run', 'review', 'live', 'ok', 'done', 'ended', 'unknown'];
const worst = sts => PRI.find(p => sts.includes(p)) ?? 'unknown';

// ---------- lanes
const DEPTS = [
  { id: 'ops', en: 'OPERATIONS', ko: '에이전트 운영', c: '#5b8cff', icon: 'gear' },
  { id: 'assistant', en: 'ASSISTANT', ko: '업무 비서·메일', c: '#ec4899', icon: 'mail' },
  { id: 'research', en: 'RESEARCH', ko: '연구·실험', c: '#a78bfa', icon: 'flask' },
  { id: 'web', en: 'WEB', ko: '웹·대시보드', c: '#22b8cf', icon: 'layout' },
  { id: 'study', en: 'STUDY', ko: '조사·문서', c: '#34d399', icon: 'book' },
  { id: 'infra', en: 'INFRA', ko: '장비·인프라', c: '#f59e0b', icon: 'cpu' },
  { id: 'etc', en: 'OTHER', ko: '기타', c: '#9ca3af', icon: 'plus' },
];
const THEME_ICON = {
  '지침·브리프': 'doc', '알림·스레드 표시': 'chat', '세션 관리·복구': 'refresh', '품질 점검': 'check', '도구 업데이트': 'bolt', '카톡 연동': 'chat',
  '메일 정리·전달': 'mail', '릴리스·점검': 'check', '비서 기능 개선': 'gear', '음성 입력': 'mic',
  '강화학습 실험': 'flask', '실차 데이터 준비': 'chart', '실험 문서 정정': 'doc', '논문·자료 검토': 'search',
  '팀 태스크 허브': 'users', '프로젝트 대시보드': 'layout', '대회 안내 페이지': 'globe', '세션 보드(UI 작업)': 'layout',
  '비용·시장 조사': 'chart', '에이전트 기억 연구': 'search', '노트 정리': 'doc', '뉴스 정리': 'book', '채용 동향 조사': 'search', '세미나 준비': 'users', '외부 협력 지원': 'mail',
  'GPU 관리': 'cpu', '정전 자동 복구': 'shield', '서명 키 정리': 'key',
};
const stCount = (arr) => { const c = {}; for (const x of arr) c[x] = (c[x] || 0) + 1; return c; };
const stLine = (c) => ['done', 'run', 'review', 'hold', 'fail'].filter(k => c[k]).map(k => `${STATUS[k][0]} ${c[k]}`).join(' · ');

function laneDept(d) {
  const ls = D.lanes.filter(l => l.dept === d.id);
  const themes = [...new Set(ls.map(l => l.theme))].map(t => {
    const g = ls.filter(l => l.theme === t);
    const sts = g.map(l => l.st);
    const open = g.some(l => l.end == null);
    return {
      label: t, icon: THEME_ICON[t] ?? d.icon, tag: '수동', count: `${g.length}건`, st: worst(sts), stText: stLine(stCount(sts)),
      range: fRange(Math.min(...g.map(l => l.start)), open ? null : Math.max(...g.map(l => l.end))),
      foot: `걸린 시간 합계 ${fDur(g.reduce((a, l) => a + l.dur, 0))}`, n: g.length,
    };
  }).sort((a, b) => b.n - a.n);
  return { ...d, count: ls.length, unit: '건', summary: ls.length ? stLine(stCount(ls.map(l => l.st))) : '', members: themes };
}

// ---------- agent sessions
const FAM_ORDER = ['Opus 5.5', 'Sonnet 5.5', 'Fable 5.1', 'Haiku', '기타 모델'];
function agentDept(platform, meta) {
  const ss = D.agents.filter(s => s.platform === platform);
  const members = FAM_ORDER.map(f => {
    const g = ss.filter(s => s.fam === f);
    if (!g.length) return null;
    const live = g.filter(s => s.live).length;
    return {
      label: f, icon: 'bot', tag: '에이전트', count: `${n(g.length)}개`, st: live ? 'run' : 'ended', stText: live ? `진행 ${live} · 종료 ${g.length - live}` : `모두 종료`,
      range: fRange(Math.min(...g.map(s => s.start)), live ? null : Math.max(...g.map(s => s.end))),
      foot: `합계 ${fHours(g.reduce((a, s) => a + s.dur, 0))} · 응답 ${n(g.reduce((a, s) => a + s.calls, 0))}회`, n: g.length,
    };
  }).filter(Boolean);
  const live = ss.filter(s => s.live).length;
  return { ...meta, count: ss.length, unit: '개', summary: `진행 ${live} · 종료 ${ss.length - live}`, members };
}
const AOE_KIND = { lead: ['리드 창', 'bot'], lane: ['레인 작업 창', 'layers'], proj: ['프로젝트 창', 'window'] };
function aoeDept() {
  const members = Object.entries(AOE_KIND).map(([k, [label, icon]]) => {
    const g = D.aoe.filter(a => a.kind === k);
    if (!g.length) return null;
    const first = Math.min(...g.map(a => a.start));
    return { label, icon, tag: '상주', count: `${g.length}개`, st: 'live', stText: `가동 중 ${g.length}`, range: `${fFull(first)}부터 지금까지`, foot: `가장 오래 켜진 창 ${fDur(NOW - first)}`, n: g.length };
  }).filter(Boolean);
  return { id: 'aoe', en: 'LIVE WINDOWS', ko: '상주 작업 창', c: '#38bdf8', icon: 'window', count: D.aoe.length, unit: '개', summary: `지금 가동 중 ${D.aoe.length}`, members };
}

// ---------- services
function svcDept(group, meta) {
  const ss = D.services.filter(s => s.group === group);
  const members = ss.map(s => {
    if (group === 'timer') {
      const st = s.runs == null ? 'unknown' : s.fails ? 'warn' : 'ok';
      return {
        label: s.label, icon: 'clock', tag: s.tag, count: s.every, st, stText: s.runs == null ? '실행 기록 없음' : s.fails ? `실패 ${n(s.fails)}회` : '실패 없음',
        range: s.last ? `마지막 실행 ${fFull(s.last)}` : (s.runs == null ? '이번 주 실행 기록을 찾지 못함' : `${s.every} 실행`),
        foot: s.runs == null ? '확인 필요' : `7일간 실행 ${n(s.runs)}회`, n: s.units,
      };
    }
    return {
      label: s.label, icon: group === 'watch' ? 'eye' : 'globe', tag: s.tag, count: `${s.units}개`, st: 'live', stText: '가동 중',
      range: `${fFull(Math.max(s.start, W0))}부터 지금까지`,
      foot: s.restarts == null ? '다시 시작 기록 없음' : `7일간 시작 ${n(s.restarts)}회`, n: s.units,
    };
  });
  return { ...meta, count: ss.length, unit: '종', summary: group === 'timer' ? `실행 ${n(ss.reduce((a, s) => a + (s.runs || 0), 0))}회` : `가동 중 ${ss.length}`, members };
}

const laneDepts = DEPTS.map(laneDept).filter(d => d.id !== 'etc' || d.count);
const agentDepts = [
  agentDept('claude', { id: 'claude', en: 'CLAUDE CODE', ko: '클로드 코드', c: '#f0883e', icon: 'bot' }),
  agentDept('senpi', { id: 'senpi', en: 'SENPI · OMO', ko: '센피(omo)', c: '#818cf8', icon: 'bot' }),
  aoeDept(),
  { id: 'other', en: 'OTHER AGENTS', ko: '다른 에이전트', c: '#9ca3af', icon: 'bot', count: 0, unit: '개', summary: '', empty: ['없음', 'Codex·OpenCode 등 기록 없음'], members: [] },
];
const svcDepts = [
  svcDept('watch', { id: 'watch', en: 'WATCHERS', ko: '상시 감시', c: '#4ade80', icon: 'eye' }),
  svcDept('web', { id: 'websvc', en: 'WEB SERVICES', ko: '웹 서비스', c: '#22b8cf', icon: 'globe' }),
  svcDept('timer', { id: 'timer', en: 'SCHEDULED', ko: '예약·자동 실행', c: '#f59e0b', icon: 'clock' }),
];

const laneTotal = D.lanes.length, agentTotal = D.agents.length, svcTotal = D.services.length;
const laneSt = stCount(D.lanes.map(l => l.st));
const agentLive = D.agents.filter(s => s.live).length;
const agentHours = D.agents.reduce((a, s) => a + s.dur, 0);
const agentCalls = D.agents.reduce((a, s) => a + s.calls, 0);

// ---------- daily trend (KST days)
const dayKey = t => { const p = parts(t); return `${p.month}-${p.day}`; };
const days = []; for (let t = W0; t < NOW; t += 864e5) days.push(t);
const laneByDay = days.map(t => D.lanes.filter(l => !l.carried && dayKey(l.start) === dayKey(t)).length);
const agentByDay = days.map(t => D.agents.filter(s => s.start > W0 && dayKey(s.start) === dayKey(t)).length);
const carriedLanes = D.lanes.filter(l => l.carried).length;
const carriedAgents = D.agents.filter(s => s.start <= W0).length;
const peakIdx = laneByDay.indexOf(Math.max(...laneByDay));
const bars = (vals, color, unit) => {
  const max = Math.max(...vals, 1);
  return `<div class="bars">${vals.map((v, i) => `<div class="bar-col"><span class="bar-val">${n(v)}</span><div class="bar-track"><div class="bar" style="height:${Math.max(3, Math.round(v / max * 100))}%;background:${color}"></div></div><span class="bar-day${i === peakIdx ? ' peak' : ''}">${parts(days[i]).day}일 ${fWk(days[i])}</span></div>`).join('')}</div>`;
};

// ---------- renderers
const chip = (t, cls = '') => `<span class="chip ${cls}">${esc(t)}</span>`;
const member = (m, c) => `<div class="member" style="--c:${c}">
  <div class="m-icon">${svg(m.icon, 16)}</div>
  <div class="m-body">
    <div class="m-head"><span class="m-name">${esc(m.label)}</span><span class="m-count">${esc(m.count)}</span></div>
    <div class="m-chips">${chip(m.tag, 'tag')}<span class="m-st"><i class="dot" style="background:${STATUS[m.st][1]}"></i>${esc(m.stText)}</span></div>
    <div class="m-line">${esc(m.range)}</div>
    <div class="m-line dim">${esc(m.foot)}</div>
  </div>
</div>`;
const dept = (d) => `<div class="kid" style="--c:${d.c}"><section class="dept" data-dept="${d.id}" data-count="${d.count}">
  <div class="d-icon">${svg(d.icon, 20)}</div>
  <p class="d-en">${d.en}</p>
  <h3 class="d-ko">${esc(d.ko)}</h3>
  <p class="d-sum">${d.empty ? '기록 없음' : `<b>${n(d.count)}${d.unit}</b>${d.summary ? ' · ' + esc(d.summary) : ''}`}</p>
  <div class="members">${d.empty ? `<div class="empty">${svg('plus', 16)}<div><b>${esc(d.empty[0])}</b><span>${esc(d.empty[1])}</span></div></div>` : d.members.map(m => member(m, d.c)).join('')}</div>
</section></div>`;
const hub = (icon, title, sub, c) => `<div class="node" style="--c:${c}"><div class="n-icon">${svg(icon, 20)}</div><div><p class="n-title">${title}</p><p class="n-sub">${sub}</p></div><i class="dot live-dot" style="background:${c}"></i></div>`;
const branch = (id, icon, title, sub, c, depts, total) => `<section class="branch" data-kind="${id}" data-count="${total}">
  <div class="tree"><div class="parent">${hub(icon, title, sub, c)}</div>
  <div class="kids" style="--n:${depts.length}">${depts.map(dept).join('')}</div></div>
</section>`;

const pillar = (icon, label, big, unit, sub, c) => `<div class="kid" style="--c:${c}"><div class="pillar"><div class="p-top"><span class="p-icon">${svg(icon, 18)}</span><span class="p-label">${label}</span></div><p class="p-big">${big}<small>${unit}</small></p><p class="p-sub">${sub}</p></div></div>`;

const html = `<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>지난 7일 세션 보드</title>
<style>
:root{--bg:#141518;--panel:#1c1d21;--card:#24262b;--line:#3a3d45;--text:#f2f3f5;--muted:#b3b8c2;--dim:#9198a4;color-scheme:dark}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:"Pretendard","Noto Sans CJK KR","Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;-webkit-font-smoothing:antialiased;word-break:keep-all}
.board{width:100%;max-width:1720px;margin:0 auto;padding:40px 32px 56px}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;margin-bottom:28px}
.eyebrow{font-size:12px;letter-spacing:.14em;color:var(--dim);font-weight:600}
h1{font-size:30px;font-weight:800;letter-spacing:-.02em;margin-top:6px}
.range{font-size:14px;color:var(--muted);margin-top:6px}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;color:var(--muted);max-width:560px;justify-content:flex-end}
.legend span{display:inline-flex;align-items:center;gap:6px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:none}
/* tree */
.tree{display:flex;flex-direction:column;align-items:center}
.parent{position:relative;padding-bottom:26px}
.parent::after{content:"";position:absolute;left:50%;bottom:0;height:26px;border-left:2px solid var(--line)}
.kids{position:relative;display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:16px;width:100%;padding-top:26px}
.kids::before{content:"";position:absolute;top:0;left:calc(100% / (var(--n) * 2));right:calc(100% / (var(--n) * 2));border-top:2px solid var(--line)}
.kid{position:relative;min-width:0}
.kid::before{content:"";position:absolute;top:-26px;left:50%;height:26px;border-left:2px solid color-mix(in srgb,var(--c) 70%,transparent)}
.node{display:flex;align-items:center;gap:12px;padding:12px 20px 12px 12px;border-radius:999px;background:var(--card);border:1.5px solid color-mix(in srgb,var(--c) 55%,var(--line));min-width:250px}
.n-icon,.p-icon{display:grid;place-items:center;width:40px;height:40px;border-radius:50%;background:var(--c);color:#111}
.n-title{font-size:17px;font-weight:800}
.n-sub{font-size:13px;color:var(--muted);margin-top:2px}
.live-dot{margin-left:auto;width:10px;height:10px}
.overview{margin-bottom:34px}
.overview .kids{max-width:1180px;margin:0 auto}
.pillar{background:var(--card);border:1.5px solid color-mix(in srgb,var(--c) 50%,var(--line));border-radius:20px;padding:18px 20px}
.p-top{display:flex;align-items:center;gap:10px}
.p-icon{width:32px;height:32px}
.p-label{font-size:15px;font-weight:700}
.p-big{font-size:40px;font-weight:800;letter-spacing:-.02em;margin-top:10px;line-height:1.1}
.p-big small{font-size:18px;font-weight:700;margin-left:4px;color:var(--muted)}
.p-sub{font-size:14px;color:var(--muted);margin-top:6px;line-height:1.45}
/* trend */
.trend{background:var(--panel);border:1px solid #2a2c32;border-radius:20px;padding:20px 24px;margin-bottom:40px;display:grid;grid-template-columns:260px 1fr 1fr;gap:28px;align-items:end}
.trend h2{font-size:18px;font-weight:800}
.trend p{font-size:14px;color:var(--muted);margin-top:8px;line-height:1.5}
.chart h4{font-size:13px;color:var(--muted);font-weight:600;margin-bottom:8px;display:flex;align-items:center;gap:6px}
.bars{display:grid;grid-template-columns:repeat(${days.length},1fr);gap:8px;height:130px}
.bar-col{display:flex;flex-direction:column;align-items:center;gap:4px;min-width:0}
.bar-val{font-size:12px;font-weight:700;color:var(--text)}
.bar-track{flex:1;width:100%;max-width:34px;display:flex;align-items:flex-end}
.bar{width:100%;border-radius:6px 6px 2px 2px}
.bar-day{font-size:12px;color:var(--dim);white-space:nowrap}
.bar-day.peak{color:var(--text);font-weight:700}
/* sections */
.branch{margin-bottom:44px}
.sec-head{display:flex;align-items:baseline;gap:12px;margin:0 0 18px}
.sec-head h2{font-size:20px;font-weight:800}
.sec-head p{font-size:14px;color:var(--muted)}
.dept{height:100%;border-radius:20px;padding:18px 12px 14px;background:linear-gradient(180deg,color-mix(in srgb,var(--c) 12%,var(--panel)) 0%,var(--panel) 160px);border:1px solid color-mix(in srgb,var(--c) 22%,#2a2c32);display:flex;flex-direction:column;align-items:center}
.d-icon{display:grid;place-items:center;width:44px;height:44px;border-radius:50%;background:color-mix(in srgb,var(--c) 26%,var(--card));color:var(--c)}
.d-en{font-size:11px;letter-spacing:.16em;color:var(--muted);margin-top:10px;font-weight:600}
.d-ko{font-size:18px;font-weight:800;margin-top:4px}
.d-sum{font-size:13px;color:var(--muted);margin-top:4px;text-align:center;line-height:1.4}
.d-sum b{color:var(--text)}
.members{display:flex;flex-direction:column;gap:8px;width:100%;margin-top:14px}
.member{display:flex;gap:10px;padding:11px 11px 11px 10px;border-radius:14px;background:var(--card);border:1px solid #33363d}
.m-icon{flex:none;display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:color-mix(in srgb,var(--c) 22%,#2b2d33);color:var(--c)}
.m-body{min-width:0;flex:1}
.m-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.m-name{font-size:15px;font-weight:700;line-height:1.3}
.m-count{flex:none;font-size:13px;font-weight:700;color:var(--text)}
.m-chips{display:flex;align-items:center;gap:8px;margin-top:5px;flex-wrap:wrap}
.chip{font-size:12px;padding:1px 7px;border-radius:6px;background:#33363d;color:#d5d8de;line-height:18px}
.m-st{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;color:var(--muted)}
.m-st .dot{width:8px;height:8px}
.m-line{font-size:12.5px;color:var(--muted);margin-top:4px;line-height:1.4}
.m-line.dim{color:var(--dim);margin-top:2px}
.empty{display:flex;gap:10px;align-items:center;padding:12px;border-radius:14px;border:1.5px dashed #4a4d55;color:var(--muted)}
.empty b{display:block;color:var(--text);font-size:15px}
.empty span{font-size:12.5px}
footer{font-size:12.5px;color:var(--dim);line-height:1.6;border-top:1px solid #2a2c32;padding-top:16px}
/* mobile */
@media (max-width:640px){
 .board{padding:24px 14px 36px}
 header{flex-direction:column;align-items:flex-start;gap:14px;margin-bottom:20px}
 h1{font-size:24px}
 .legend{justify-content:flex-start;gap:6px 12px}
 .tree{align-items:stretch}
 .parent{padding-bottom:14px}
 .parent::after{left:28px;height:14px}
 .node{min-width:0;border-radius:18px}
 .kids,.overview .kids{grid-template-columns:1fr;gap:12px;padding:0 0 0 22px;margin-left:27px;width:auto;border-left:0}
 .kids::before{left:0;right:auto;top:-1px;bottom:auto;height:var(--spine,100%);border-top:0;border-left:2px solid var(--line)}
 .kid::before{top:30px;left:-22px;width:20px;height:0;border-left:0;border-top:2px solid color-mix(in srgb,var(--c) 70%,transparent)}
 .kid:last-child::after{content:"";position:absolute;left:-23px;top:31px;bottom:-14px;width:4px;background:var(--bg)}
 .pillar{padding:14px 16px}
 .p-big{font-size:32px}
 .trend{grid-template-columns:1fr;gap:18px;padding:16px}
 .bars{height:110px;gap:4px}
 .bar-day{font-size:11px}
 .dept{padding:16px 10px 12px}
}
</style></head>
<body><main class="board">
<header>
  <div><p class="eyebrow">SESSION BOARD · 지난 7일</p><h1>이번 주 에이전트가 한 일</h1>
  <p class="range">${fDay(W0)}(${fWk(W0)}) ${fTime(W0)} ~ ${fDay(NOW)}(${fWk(NOW)}) ${fTime(NOW)} · 한국 시간 · 이 컴퓨터 한 대 기준</p></div>
  <div class="legend">${['done', 'run', 'review', 'hold', 'fail', 'live', 'warn'].map(k => `<span><i class="dot" style="background:${STATUS[k][1]}"></i>${STATUS[k][0]}</span>`).join('')}</div>
</header>

<section class="overview" data-kind="overview">
  <div class="tree"><div class="parent">${hub('hub', '지난 7일 · 모든 세션', `${n(laneTotal + agentTotal + svcTotal)}개를 세 갈래로 정리`, '#e5e7eb')}</div>
  <div class="kids" style="--n:3">
    ${pillar('layers', '작업 레인', n(laneTotal), '건', `완료 ${laneSt.done ?? 0} · 진행 ${(laneSt.run ?? 0)} · 확인 대기 ${laneSt.review ?? 0} · 멈춤 ${laneSt.hold ?? 0} · 미완 ${laneSt.fail ?? 0}`, '#5b8cff')}
    ${pillar('bot', '에이전트 세션', n(agentTotal), '개', `누적 ${fHours(agentHours)} · 응답 ${n(agentCalls)}회 · 상주 창 ${D.aoe.length}개`, '#f0883e')}
    ${pillar('eye', '감시·서비스', n(svcTotal), '종', `상시 가동 ${D.services.filter(s => s.tag === '상시').length}종 · 예약·자동 실행 ${n(D.services.reduce((a, s) => a + (s.runs || 0), 0))}회`, '#4ade80')}
  </div></div>
</section>

<section class="trend" data-kind="trend">
  <div><h2>하루별 새로 시작한 일</h2><p>가장 바빴던 날은 ${fDay(days[peakIdx])}(${fWk(days[peakIdx])})로 레인 ${laneByDay[peakIdx]}건이 열렸습니다. 기간 전에 시작해 이어진 레인 ${carriedLanes}건, 에이전트 세션 ${n(carriedAgents)}개는 막대에서 뺐습니다.</p></div>
  <div class="chart"><h4><i class="dot" style="background:#5b8cff"></i>새 작업 레인 (건)</h4>${bars(laneByDay, '#5b8cff')}</div>
  <div class="chart"><h4><i class="dot" style="background:#f0883e"></i>새 에이전트 세션 (개)</h4>${bars(agentByDay, '#f0883e')}</div>
</section>

${branch('lanes', 'layers', '작업 레인', `${n(laneTotal)}건 · 맡긴 일을 비슷한 일끼리 묶음`, '#5b8cff', laneDepts, laneTotal)}

${branch('agents', 'bot', '에이전트 세션', `${n(agentTotal)}개 · 코딩 에이전트 실행을 모델별로`, '#f0883e', agentDepts, agentTotal)}

${branch('services', 'eye', '감시·서비스', `${n(svcTotal)}종 · 사람 없이 도는 감시·웹·예약`, '#4ade80', svcDepts, svcTotal)}

<footer>기준: 작업 레인 기록, 코딩 에이전트 세션 기록(시작·끝 시각, 모델, 응답 수만 사용, 대화 내용은 읽지 않음), 서비스 상태와 실행 기록. 걸린 시간은 이 기간 안의 시간만 셉니다. 에이전트 세션의 누적 시간은 여러 세션이 동시에 돈 시간을 모두 더한 값입니다. 만든 시각 ${fFull(NOW)}.</footer>
</main></body></html>`;

await Bun.write(outPath, html);
console.log(JSON.stringify({ bytes: html.length, lanes: laneTotal, agents: agentTotal, services: svcTotal, aoe: D.aoe.length, laneDepts: laneDepts.map(d => [d.id, d.count]), agentDepts: agentDepts.map(d => [d.id, d.count]), svcDepts: svcDepts.map(d => [d.id, d.count]) }));
