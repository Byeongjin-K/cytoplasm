// Collects the 7-day session board data (metadata only) into data.json for gen.mjs.
//
// Usage:
//   bun collect.mjs <out-data.json>
// Environment (all required unless marked optional):
//   SB_LANE_DIRS      colon-separated lane registry folders (each holds <key>.json lane files);
//                     earlier folders win when two registries hold the same key
//   SB_LANE_RULES     JSON file: [["<regex on lane key>", "<dept id>", "<generic theme label>"], ...]
//                     dept id is one of ops|assistant|research|web|study|infra; unmatched lanes become etc / "세션 N"
//   SB_SERVICES       JSON file: the hand-made service table (see the guide, section 2.4), times as ISO strings
//   SB_SESSION_FINDER path to the coding-agent-sessions skill's find-agent-sessions.py
//   SB_NOW            optional ISO time to freeze "now" (default: the current time)
//   SB_LEAD_WINDOWS   optional regex for aoe_ window names that are lead windows (default: none)
//   SB_PROJECT_WINDOWS optional regex for aoe_ window names that are project windows (default: none)
//
// Privacy: lane keys and titles, session prompts and paths never reach the output. The output keeps only
// dept/theme labels from the rules file, statuses, times, durations and counts.

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

// ---- coding-agent sessions (metadata; the finder's message fields are dropped immediately)
const day = new Date(W0 - 864e5).toISOString().slice(0, 10);
const found = await Bun.$`python3 -I ${env('SB_SESSION_FINDER')} list --from ${day} --include-subagents --limit 5000 --workers 32`.quiet().cwd('/tmp');
const parsed = JSON.parse(found.stdout.toString());
const list = (Array.isArray(parsed) ? parsed : parsed.results ?? parsed.sessions ?? [])
  .map(({ platform, model, created_at, updated_at, path }) => ({ platform, model, created_at, updated_at, path }));
const fam = m => !m ? '기타 모델' : /opus-5-5/.test(m) ? 'Opus 5.5' : /sonnet/.test(m) ? 'Sonnet 5.5' : /haiku/.test(m) ? 'Haiku' : /fable/.test(m) ? 'Fable 5.1' : '기타 모델';
const inWindow = list.filter(s => Date.parse(s.created_at) <= NOW && Date.parse(s.updated_at || s.created_at) >= W0);
const agents = [];
for (let i = 0; i < inWindow.length; i += 40) {
  await Promise.all(inWindow.slice(i, i + 40).map(async s => {
    // Response count = lines carrying an assistant marker; the body is never parsed.
    const marker = s.platform === 'claude' ? '"type":"assistant"' : '"role":"assistant"';
    const r = await Bun.$`grep -c -F ${marker} ${s.path}`.nothrow().quiet();
    const updated = Date.parse(s.updated_at || s.created_at);
    const a = Math.max(Date.parse(s.created_at), W0), b = Math.min(updated, NOW);
    agents.push({ platform: s.platform, fam: fam(s.model), start: a, end: b, dur: Math.max(0, b - a), calls: parseInt(r.stdout.toString()) || 0, live: updated > NOW - 15 * 60e3 });
  }));
}

// ---- live aoe windows (names are classified, never emitted)
const lead = process.env.SB_LEAD_WINDOWS ? new RegExp(process.env.SB_LEAD_WINDOWS) : null;
const proj = process.env.SB_PROJECT_WINDOWS ? new RegExp(process.env.SB_PROJECT_WINDOWS) : null;
const tmux = (await Bun.$`tmux ls -F ${'#{session_name} #{session_created}'}`.nothrow().text()).trim().split('\n').filter(Boolean).map(x => x.split(' '));
const aoe = tmux.filter(([n, c]) => n.startsWith('aoe_') && +c * 1000 <= NOW).map(([n, c]) => {
  const core = n.replace(/^aoe_(term_)?/, '').replace(/_[0-9a-f]{8}$/, '');
  return { kind: lead?.test(core) ? 'lead' : proj?.test(core) ? 'proj' : 'lane', start: +c * 1000 };
});

// ---- services: the hand-made table, times converted to epoch ms
const t = v => v == null ? null : Date.parse(v);
const services = JSON.parse(await Bun.file(env('SB_SERVICES')).text()).map(s => ({ ...s, start: t(s.start), last: t(s.last) }));

await Bun.write(outPath, JSON.stringify({ w0: W0, now: NOW, lanes, agents, aoe, services }));
console.log(JSON.stringify({ lanes: lanes.length, unclassified: unknown, agents: agents.length, aoe: aoe.length, services: services.length }));
