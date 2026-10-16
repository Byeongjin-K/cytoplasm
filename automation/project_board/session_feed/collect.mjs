// Collects the 7-day session summary (metadata only) that the dashboard's 세션 tab reads as sessions.json.
//
// Usage:
//   bun collect.mjs <out.json>        (feed.sh runs it and copies the result to the node)
// Environment (all required unless marked optional):
//   SB_LANE_DIRS      colon-separated lane registry folders (each holds <key>.json lane files);
//                     earlier folders win when two registries hold the same key
//   SB_LANE_RULES     JSON file: [["<regex on lane key>", "<dept id>", "<generic theme label>"], ...]
//                     dept id is one of ops|assistant|research|web|study|infra; unmatched lanes become etc / "세션 N"
//   SB_SERVICES       JSON file: [{"label", "group": "watch|web|timer", "tag", "units": ["<user unit>", ...], "every"?}];
//                     state, start, restarts, runs, fails and last run are read from systemd --user and its journal
//   SB_CACHE          optional private JSON file that remembers, per session file, how far it was read and what it held
//                     (paths stay in that file only). Without it every run reads the last week's transcripts in full.
//   SB_NOW            optional ISO time to freeze "now" (default: the current time)
//   SB_LEAD_WINDOWS   optional regex for aoe_ window names that are lead windows (default: none)
//   SB_PROJECT_WINDOWS optional regex for aoe_ window names that are project windows (default: none)
//
// Privacy: lane keys and titles, session prompts and paths never reach the output. The output keeps only
// dept/theme labels from the rules file, statuses, times, durations and counts.

import { existsSync } from 'node:fs';

const [, , outPath] = process.argv;
const env = k => { const v = process.env[k]; if (!v) throw new Error(`missing ${k}`); return v; };
const NOW = process.env.SB_NOW ? Date.parse(process.env.SB_NOW) : Date.now();
// Window start: 00:00 KST seven days before today (KST = UTC+9, no daylight saving).
const KST = 9 * 36e5;
const W0 = Math.floor((NOW + KST) / 864e5) * 864e5 - 7 * 864e5 - KST;

// ---- lanes
const raw = new Map();
for (const dir of env('SB_LANE_DIRS').split(':')) {
  for await (const f of new Bun.Glob('*.json').scan(dir)) {
    const j = JSON.parse(await Bun.file(`${dir}/${f}`).text());
    if (!raw.has(j.key)) raw.set(j.key, j);
  }
}
const rules = JSON.parse(await Bun.file(env('SB_LANE_RULES')).text()).map(([re, dept, theme]) => [new RegExp(re), dept, theme]);
const ST = { closed: 'done', 'done-claimed': 'review', open: 'run', paused: 'hold', 'not-done': 'fail' };
let unknown = 0;
const lanes = [...raw.values()]
  .filter(l => Date.parse(l.openedAt) <= NOW)
  .map(l => {
    const closed = l.closedAt && Date.parse(l.closedAt) <= NOW ? Date.parse(l.closedAt) : null;
    // Closed after the frozen "now": it was still running then.
    const st = l.closedAt && !closed ? 'run' : (ST[l.status ?? 'open'] ?? 'run');
    return { key: l.key, start: Date.parse(l.openedAt), end: closed, st };
  })
  .filter(l => l.end == null || l.end >= W0)
  .map(l => {
    const m = rules.find(([re]) => re.test(l.key));
    return {
      dept: m ? m[1] : 'etc', theme: m ? m[2] : `세션 ${++unknown}`, st: l.st, start: l.start, end: l.end,
      dur: (l.end ?? NOW) - Math.max(l.start, W0), carried: l.start < W0,
    };
  });

// ---- coding-agent sessions: Claude Code and Senpi JSONL transcripts, read incrementally.
// The rules are the coding-agent-sessions finder's (created = first timestamp, updated = last, model = the first
// model field, one session per platform + session id, created on or after the day before the window), but a file
// is read only from where the previous run stopped: transcripts are append-only, so the week's 3-4 GB is read
// once and later runs read the few lines written since. Files last written before the window are not opened.
const H = process.env.HOME;
const cachePath = process.env.SB_CACHE;
const cache = new Map(cachePath && await Bun.file(cachePath).exists() ? Object.entries(JSON.parse(await Bun.file(cachePath).text())) : []);
const SOURCES = [
  { platform: 'claude', marker: '"type":"assistant"', id: p => p.split('/').pop().replace(/\.jsonl$/, ''),
    globs: [[`${H}/.claude/projects`, '*/*.jsonl'], [`${H}/.claude/transcripts`, '*.jsonl'], [`${H}/.claude/pre-compact-session-histories`, '*.jsonl']] },
  { platform: 'claude', marker: '"type":"assistant"', sub: true, id: p => p.split('/').pop().replace(/\.jsonl$/, '').replace(/^agent-/, ''),
    globs: [[`${H}/.claude/projects`, '*/*/subagents/**/agent-*.jsonl']] },
  ...['.omo', '.senpi', '.pi'].map(d => ({ platform: 'senpi', marker: '"role":"assistant"', id: p => p.split('/').pop().replace(/\.jsonl$/, '').split('_').pop(),
    globs: [[`${H}/${d}/agent/sessions`, '**/*.jsonl'], [`${H}/${d}/profiles`, '*/agent/sessions/**/*.jsonl']] })),
];
const str = v => typeof v === 'string' && v ? v : null;
const readLines = async (path, from, to, onLine) => {
  // Complete lines only: a line still being written is read on the next run. Returns the new offset.
  const reader = Bun.file(path).slice(from, to).stream().getReader();
  const dec = new TextDecoder();
  let carry = new Uint8Array(0), done = from;
  for (;;) {
    const { value, done: end } = await reader.read();
    if (end) break;
    const buf = carry.length ? Buffer.concat([carry, value]) : value;
    let start = 0;
    for (let i = buf.indexOf(10); i !== -1; i = buf.indexOf(10, start)) { onLine(dec.decode(buf.subarray(start, i))); start = i + 1; }
    done += start; carry = buf.subarray(start);
  }
  return done;
};
const seen = new Set(), files = [];
for (const src of SOURCES) for (const [root, pattern] of src.globs) {
  if (!existsSync(root)) continue;
  for await (const rel of new Bun.Glob(pattern).scan({ cwd: root, onlyFiles: true })) {
    const path = `${root}/${rel}`;
    if (seen.has(path)) continue;
    seen.add(path); files.push([src, path]);
  }
}
const rows = [];
for (const [src, path] of files) {
  const st = await Bun.file(path).stat().catch(() => null);
  if (!st || st.mtimeMs < W0) continue;
  let c = cache.get(path);
  if (!c || st.size < c.offset) c = { offset: 0, sid: null, created: null, updated: null, model: null, calls: 0 };
  if (st.size > c.offset) {
    c.offset = await readLines(path, c.offset, st.size, line => {
      if (line.includes(src.marker)) c.calls++;
      let d;
      try { d = JSON.parse(line); } catch { return; }
      if (!d || typeof d !== 'object') return;
      const ts = str(d.timestamp);
      if (ts) { c.created ??= ts; c.updated = ts; }
      c.sid = str(d.sessionId) ?? (d.type === 'session' ? str(d.id) : null) ?? c.sid;
      const msg = (d.message && typeof d.message === 'object' ? d.message : null) ?? (d.payload && typeof d.payload === 'object' ? d.payload : {});
      c.model ??= str(d.modelId) ?? str(d.model) ?? str(msg.model);
    });
  }
  cache.set(path, c);
  const mtime = new Date(st.mtimeMs).toISOString();
  const created = c.created ?? mtime;
  rows.push({ platform: src.platform, path, sid: src.sub ? src.id(path) : (c.sid ?? src.id(path)), sub: !!src.sub, created,
    updated: src.sub ? mtime : (c.updated ?? created), model: src.sub ? null : c.model, calls: c.calls });
}
if (cachePath) {
  const kept = new Set(rows.map(r => r.path));
  for (const p of cache.keys()) if (!kept.has(p)) cache.delete(p);
  await Bun.write(`${cachePath}.tmp`, JSON.stringify(Object.fromEntries(cache)));
  await Bun.$`chmod 600 ${cachePath}.tmp && mv -f ${cachePath}.tmp ${cachePath}`.quiet();
}
const unique = new Map();
for (const r of rows.sort((a, b) => a.path.localeCompare(b.path))) {
  const key = `${r.platform}\n${r.sid}`, cur = unique.get(key);
  if (!cur || (r.sub && !cur.sub)) unique.set(key, r);
}
const fromDay = Date.parse(`${new Date(W0 - 864e5).toISOString().slice(0, 10)}T00:00:00Z`);
const fam = m => !m ? '기타 모델' : /opus-5-5/.test(m) ? 'Opus 5.5' : /sonnet/.test(m) ? 'Sonnet 5.5' : /haiku/.test(m) ? 'Haiku' : /fable/.test(m) ? 'Fable 5.1' : '기타 모델';
const agents = [...unique.values()]
  .filter(s => !(Date.parse(s.created) < fromDay) && Date.parse(s.created) <= NOW && Date.parse(s.updated) >= W0)
  .map(s => {
    const updated = Date.parse(s.updated), a = Math.max(Date.parse(s.created), W0), b = Math.min(updated, NOW);
    return { platform: s.platform, fam: fam(s.model), start: a, end: b, dur: Math.max(0, b - a), calls: s.calls, live: updated > NOW - 15 * 60e3 };
  });

// ---- live aoe windows (names are classified, never emitted)
const lead = process.env.SB_LEAD_WINDOWS ? new RegExp(process.env.SB_LEAD_WINDOWS) : null;
const proj = process.env.SB_PROJECT_WINDOWS ? new RegExp(process.env.SB_PROJECT_WINDOWS) : null;
const tmux = (await Bun.$`tmux ls -F ${'#{session_name} #{session_created}'}`.nothrow().text()).trim().split('\n').filter(Boolean).map(x => x.split(' '));
const aoe = tmux.filter(([n, c]) => n.startsWith('aoe_') && +c * 1000 <= NOW).map(([n, c]) => {
  const core = n.replace(/^aoe_(term_)?/, '').replace(/_[0-9a-f]{8}$/, '');
  return { kind: lead?.test(core) ? 'lead' : proj?.test(core) ? 'proj' : 'lane', start: +c * 1000 };
});

// ---- services: labels from the config, everything else from systemd (unit names are never emitted)
const since = `@${Math.floor(W0 / 1000)}`;
const journal = async unit => (await Bun.$`journalctl --user -u ${unit} --since ${since} -o short-unix --no-pager`.nothrow().quiet().text())
  .split('\n').map(line => { const m = line.match(/^(\d+(?:\.\d+)?) \S+ \S+: (.*)$/); return m ? { at: Math.round(+m[1] * 1000), text: m[2] } : null; })
  .filter(Boolean);
const services = [];
for (const s of JSON.parse(await Bun.file(env('SB_SERVICES')).text())) {
  const units = await Promise.all(s.units.map(async unit => {
    const show = Object.fromEntries((await Bun.$`systemctl --user show ${unit} -p ActiveState -p ActiveEnterTimestamp --timestamp=unix`.nothrow().quiet().text())
      .trim().split('\n').map(l => l.split('=')));
    const lines = await journal(unit);
    const entered = /^@\d+/.test(show.ActiveEnterTimestamp ?? '') ? +show.ActiveEnterTimestamp.slice(1) * 1000 : null;
    return {
      active: show.ActiveState === 'active', entered,
      starts: lines.filter(l => /^Started /.test(l.text)).length,
      finished: lines.filter(l => /^Finished /.test(l.text)),
      failed: lines.filter(l => /Failed with result/.test(l.text)),
    };
  }));
  const row = { label: s.label, group: s.group, tag: s.tag, units: s.units.length, every: s.every ?? '' };
  if (s.group === 'timer') {
    const done = units.flatMap(u => u.finished), failed = units.flatMap(u => u.failed);
    const last = Math.max(0, ...done.map(l => l.at), ...failed.map(l => l.at));
    Object.assign(row, { st: failed.length ? 'warn' : 'ok', runs: done.length + failed.length || null, fails: failed.length, last: last || null });
  } else {
    const entered = units.map(u => u.entered).filter(v => v != null);
    Object.assign(row, {
      st: units.every(u => u.active) ? 'live' : 'hold', start: entered.length ? Math.min(...entered) : null,
      restarts: units.reduce((a, u) => a + u.starts, 0),
    });
  }
  services.push(row);
}

await Bun.write(outPath, JSON.stringify({ w0: W0, now: NOW, lanes, agents, aoe, services }));
console.log(JSON.stringify({ lanes: lanes.length, unclassified: unknown, agents: agents.length, aoe: aoe.length, services: services.length }));
