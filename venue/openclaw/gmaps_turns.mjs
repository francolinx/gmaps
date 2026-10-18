// GMAPS persistent agent session client (Franco, Oct 9). Runs INSIDE the NemoClaw sandbox, long-lived:
//   node /sandbox/gmaps/gmaps_turns.mjs [agentId]
// Pulls one message per transmission from the host tool server's turn queue (/agent/next, long-poll), runs it as a turn
// of the OpenClaw `gmaps` agent through the gateway's loopback /v1/chat/completions endpoint (same codepath as
// `openclaw agent`; the session key from the host keeps the session), and posts the reply to /agent/result.
// Credentials are read from the sandbox's openclaw.json and never printed. It sends nothing anywhere else.
import fs from 'node:fs';

const cfg = JSON.parse(fs.readFileSync('/sandbox/.openclaw/openclaw.json', 'utf8'));
const GW = `http://127.0.0.1:${cfg.gateway?.port || 18789}/v1/chat/completions`;
const GW_AUTH = `Bearer ${cfg.gateway.auth.token}`;
const MCP = cfg.mcp.servers.gmaps;
const BASE = MCP.url.replace(/\/mcp\/?$/, '');
const AUTH = MCP.headers.Authorization;
const AGENT = process.argv[2] || 'gmaps';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
console.log(JSON.stringify({ event: 'started', agent: AGENT, queue: BASE, pid: process.pid }));

for (;;) {
  let item;
  try {
    const r = await fetch(`${BASE}/agent/next?wait=20`, { headers: { Authorization: AUTH }, signal: AbortSignal.timeout(30000) });
    if (r.status === 204) continue;
    if (!r.ok) { await sleep(1000); continue; }
    item = await r.json();
  } catch (e) {
    await sleep(1000); continue;
  }
  fetch(`${BASE}/agent/ack`, { method: 'POST', headers: { Authorization: AUTH, 'Content-Type': 'application/json' },
                               body: JSON.stringify({ id: item.id }), signal: AbortSignal.timeout(5000) }).catch(() => {});
  const t0 = performance.now();
  let out;
  try {
    const g = await fetch(GW, {
      method: 'POST',
      headers: { Authorization: GW_AUTH, 'Content-Type': 'application/json', 'x-openclaw-session-key': item.session_key },
      body: JSON.stringify({ model: `openclaw/${AGENT}`, messages: [{ role: 'user', content: item.message }] }),
      signal: AbortSignal.timeout((item.timeout_s || 120) * 1000),
    });
    const j = await g.json().catch(() => ({}));
    out = { id: item.id, ok: g.ok, status: g.status, content: j.choices?.[0]?.message?.content ?? null,
            error: g.ok ? null : JSON.stringify(j).slice(0, 500) };
  } catch (e) {
    out = { id: item.id, ok: false, status: null, content: null, error: String(e).slice(0, 300) };
  }
  out.ms = Math.round(performance.now() - t0);
  out.session_key = item.session_key;
  await fetch(`${BASE}/agent/result`, { method: 'POST', headers: { Authorization: AUTH, 'Content-Type': 'application/json' },
                                         body: JSON.stringify(out), signal: AbortSignal.timeout(10000) }).catch(() => {});
}
