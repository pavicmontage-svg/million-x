/* QUANT TITAN — mobile-first control center. No external dependencies (CSP: script-src 'self'). */
"use strict";
(() => {
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const view = $("#view");
const S = { token: null, status: null, ws: null, wsOk: false, route: "overview", param: null, lastAlertId: 0, timer: null, cfg: null };

// ---------------------------------------------------------------- storage / formatting
const store = {
  get(k) { try { return sessionStorage.getItem(k) || localStorage.getItem(k); } catch { return null; } },
  set(k, v, persist) { try { (persist ? localStorage : sessionStorage).setItem(k, v); } catch {} },
  del(k) { try { sessionStorage.removeItem(k); localStorage.removeItem(k); } catch {} },
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (x) => typeof x === "number" && isFinite(x);
const fmt = (x, d = 2) => (isNum(x) ? x.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }) : "—");
const px = (x) => (isNum(x) ? (Math.abs(x) >= 1000 ? fmt(x, 2) : Math.abs(x) >= 1 ? fmt(x, 4) : x.toPrecision(5)) : "—");
const sgn = (x, d = 2) => (isNum(x) ? `<span class="num ${x > 0 ? "pos" : x < 0 ? "neg" : ""}">${x > 0 ? "+" : ""}${fmt(x, d)}</span>` : "—");
const pct = (x, d = 2) => (isNum(x) ? `${fmt(x, d)}%` : "—");
const time = (ms) => (ms ? new Date(ms).toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const dirPill = (d) => `<span class="pill ${String(d).toLowerCase()}">${esc(d)}</span>`;
const empty = (t = "Nothing here yet.") => `<div class="empty">${esc(t)}</div>`;

// ---------------------------------------------------------------- API
async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: { "Content-Type": "application/json" } };
  if (S.token) o.headers.Authorization = `Bearer ${S.token}`;
  if (opts.body !== undefined) o.body = JSON.stringify(opts.body);
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch {}
  if (r.status === 401 && !path.startsWith("/api/auth")) { S.token = null; store.del("qt_token"); renderAuth(); throw new Error("authentication required"); }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}
const post = (p, body = {}) => api(p, { method: "POST", body });

function toast(msg, sev = "info", ms = 5000) {
  const el = document.createElement("div");
  el.className = `toast ${sev}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// ---------------------------------------------------------------- modal / confirmations
function modal(html) {
  $("#modalCard").innerHTML = html;
  $("#modal").classList.remove("hidden");
  const f = $("#modalCard input, #modalCard button");
  if (f) f.focus();
}
function closeModal() { $("#modal").classList.add("hidden"); $("#modalCard").innerHTML = ""; }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
function confirmDialog({ title, text, typed, danger = true, okLabel = "Confirm", extra = "" }) {
  return new Promise((resolve) => {
    modal(`<h3>${esc(title)}</h3><p>${text}</p>${extra}
      ${typed ? `<div class="field"><label>Type <b class="mono">${esc(typed)}</b> to confirm</label><input id="cfTyped" autocomplete="off" autocapitalize="characters"></div>` : ""}
      <div class="row" style="margin-top:14px"><span class="spacer"></span><button class="btn" id="cfNo">Cancel</button>
      <button class="btn ${danger ? "danger" : "primary"}" id="cfYes" ${typed ? "disabled" : ""}>${esc(okLabel)}</button></div>`);
    if (typed) $("#cfTyped").addEventListener("input", (e) => { $("#cfYes").disabled = e.target.value.trim() !== typed; });
    $("#cfNo").onclick = () => { closeModal(); resolve(null); };
    $("#cfYes").onclick = () => {
      const vals = {};
      $$("#modalCard [data-k]").forEach((i) => { vals[i.dataset.k] = i.type === "checkbox" ? i.checked : i.value; });
      closeModal(); resolve({ typed: typed ? typed : null, vals });
    };
  });
}

// ---------------------------------------------------------------- charts
function lineChart(canvas, xs, ys, { color = "--green", fill = true, zero = false } = {}) {
  if (!canvas || ys.length < 2) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const g = canvas.getContext("2d"); g.scale(dpr, dpr);
  const cs = getComputedStyle(document.documentElement);
  const col = cs.getPropertyValue(color).trim() || color, line = cs.getPropertyValue("--line").trim(), muted = cs.getPropertyValue("--muted").trim();
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = { l: 8, r: 58, t: 10, b: 18 };
  const X = (i) => pad.l + (i / (ys.length - 1)) * (w - pad.l - pad.r);
  const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b);
  g.strokeStyle = line; g.lineWidth = 1; g.fillStyle = muted; g.font = "11px ui-monospace, monospace";
  for (let k = 0; k <= 3; k++) {
    const v = lo + (k / 3) * (hi - lo), y = Y(v);
    g.beginPath(); g.moveTo(pad.l, y); g.lineTo(w - pad.r, y); g.stroke();
    g.fillText(Math.abs(v) >= 1000 ? v.toFixed(0) : v.toFixed(2), w - pad.r + 6, y + 4);
  }
  if (xs && xs.length) { g.fillText(new Date(xs[0]).toLocaleDateString(), pad.l, h - 4); }
  g.beginPath(); ys.forEach((v, i) => (i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))));
  g.strokeStyle = col; g.lineWidth = 2; g.stroke();
  if (fill) { g.lineTo(X(ys.length - 1), h - pad.b); g.lineTo(X(0), h - pad.b); g.closePath(); g.globalAlpha = 0.12; g.fillStyle = col; g.fill(); g.globalAlpha = 1; }
}

// ---------------------------------------------------------------- config forms
const ENUMS = {
  trading_mode: ["PAPER", "SEMI_AUTOMATIC", "AUTOMATIC"], direction: ["BOTH", "LONG", "SHORT"], execution_timeframe: ["3m", "1m", "5m", "15m"],
  market_data_source: ["binance_live", "binance_testnet", "replay"], exchange_env: ["PAPER", "TESTNET"], entry_order_type: ["MARKET", "LIMIT_IOC"],
  margin_type: ["ISOLATED", "CROSSED"], conditional_endpoint: ["algo", "legacy"], working_type: ["MARK_PRICE", "CONTRACT_PRICE"],
  on_unexpected_position: ["alert_only", "protect", "flatten"], policy: ["regime_gated", "majority_vote", "confidence_average"],
  orderflow_confirmation: ["off", "optional", "required"], breaker_reset: ["auto_next_period", "manual"], session_anchor: ["utc_day", "rolling"],
  entry_mode: ["breakout_close", "retest"], stop_mode: ["box_mid", "box_opposite"], role: ["confirmation", "standalone"],
  timeframe: ["5m", "15m", "1h"], mode: ["dynamic", "fixed"], min_severity: ["info", "warning", "critical"],
};
const label = (k) => k.replace(/_/g, " ").replace(/\bpct\b/, "%").replace(/\br\b/, "R");
function configForm(section, obj, { skip = [] } = {}) {
  const f = Object.entries(obj).filter(([k]) => !skip.includes(k)).map(([k, v]) => {
    const id = `cf_${section}_${k}`.replace(/\./g, "_");
    if (typeof v === "boolean") return `<div class="field"><label for="${id}">${esc(label(k))}</label><label class="switch"><input type="checkbox" id="${id}" data-key="${k}" data-type="bool" ${v ? "checked" : ""}><span></span></label></div>`;
    if (ENUMS[k] && typeof v === "string") {
      const opts = ENUMS[k].includes(v) ? ENUMS[k] : [v, ...ENUMS[k]];
      return `<div class="field"><label for="${id}">${esc(label(k))}</label><select id="${id}" data-key="${k}" data-type="str">${opts.map((o) => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></div>`;
    }
    if (typeof v === "number") return `<div class="field"><label for="${id}">${esc(label(k))}</label><input id="${id}" type="number" step="any" inputmode="decimal" data-key="${k}" data-type="num" value="${v}"></div>`;
    if (Array.isArray(v)) return `<div class="field"><label for="${id}">${esc(label(k))} (comma separated)</label><input id="${id}" data-key="${k}" data-type="${v.every(isNum) ? "numlist" : "list"}" value="${esc(v.join(","))}"></div>`;
    if (v && typeof v === "object") return `<div class="field" style="grid-column:1/-1"><label for="${id}">${esc(label(k))} (JSON)</label><textarea id="${id}" data-key="${k}" data-type="json">${esc(JSON.stringify(v, null, 1))}</textarea></div>`;
    return `<div class="field"><label for="${id}">${esc(label(k))}</label><input id="${id}" data-key="${k}" data-type="str" value="${esc(v)}"></div>`;
  }).join("");
  return `<form class="cfgform" data-section="${esc(section)}"><div class="form">${f}</div>
    <div class="row" style="margin-top:12px"><span class="spacer"></span><button class="btn primary" type="submit">Save ${esc(section)}</button></div></form>`;
}
function readForm(form) {
  const out = {};
  $$("[data-key]", form).forEach((i) => {
    const t = i.dataset.type, k = i.dataset.key;
    if (t === "bool") out[k] = i.checked;
    else if (t === "num") out[k] = Number(i.value);
    else if (t === "numlist") out[k] = i.value.split(",").map((x) => Number(x.trim())).filter((x) => isFinite(x));
    else if (t === "list") out[k] = i.value.split(",").map((x) => x.trim()).filter(Boolean);
    else if (t === "json") out[k] = JSON.parse(i.value);
    else out[k] = i.value;
  });
  return out;
}
function nest(path, value) { return path.split(".").reverse().reduce((acc, p) => ({ [p]: acc }), value); }
document.addEventListener("submit", async (e) => {
  const form = e.target.closest(".cfgform");
  if (!form) return;
  e.preventDefault();
  try {
    const patch = nest(form.dataset.section, readForm(form));
    await api("/api/config", { method: "PATCH", body: { patch, note: `dashboard: ${form.dataset.section}` } });
    toast(`Saved ${form.dataset.section} (new config version)`, "info");
    S.cfg = null;
  } catch (err) { toast(`Not saved: ${err.message}`, "critical", 8000); }
});
async function getCfg() { if (!S.cfg) S.cfg = (await api("/api/config")).config; return S.cfg; }

// ---------------------------------------------------------------- pages
const ROUTES = [
  ["sec", "Trading"], ["overview", "Overview", "◉"], ["chart", "Chart", "📈"], ["markets", "Live Markets", "≋"], ["trades", "Active Trades", "⇅"],
  ["history", "Trade History", "☰"], ["sec", "Intelligence"], ["strategies", "Strategies", "⚙"], ["team", "AI Team", "◎"],
  ["regime", "Market Regime", "◈"], ["sec", "Risk & Research"], ["risk", "Risk Management", "⛨"], ["backtest", "Backtesting", "⏱"],
  ["paper", "Paper Trading", "▤"], ["performance", "Performance", "↗"], ["sec", "System"], ["settings", "Settings", "⚒"],
  ["api", "API Connection", "⚿"], ["alerts", "Alerts", "!"], ["logs", "System Logs", "≡"],
];
const BOTTOM = [["overview", "Overview", "◉"], ["chart", "Graf", "📈"], ["trades", "Trades", "⇅"], ["risk", "Risk", "⛨"], ["more", "More", "⋯"]];
function buildNav() {
  $("#sidenav").innerHTML = ROUTES.map((r) => (r[0] === "sec" ? `<div class="sec">${r[1]}</div>` : `<a href="#/${r[0]}" data-r="${r[0]}"><span>${r[2]}</span>${r[1]}</a>`)).join("");
  $("#bottomnav").innerHTML = BOTTOM.map((r) => `<a href="${r[0] === "more" ? "#" : "#/" + r[0]}" data-r="${r[0]}"><span class="ic">${r[2]}</span>${r[1]}</a>`).join("");
  $("#bottomnav").addEventListener("click", (e) => { const a = e.target.closest("a"); if (a && a.dataset.r === "more") { e.preventDefault(); $("#sidenav").classList.toggle("open"); } });
  $("#menuBtn").onclick = () => $("#sidenav").classList.toggle("open");
  $("#sidenav").addEventListener("click", (e) => { if (e.target.closest("a")) $("#sidenav").classList.remove("open"); });
}
function markNav() { $$("[data-r]").forEach((a) => a.classList.toggle("active", a.dataset.r === S.route || (S.route === "engine" && a.dataset.r === "strategies"))); }

const kpi = (l, v, s = "") => `<div class="card kpi"><div class="l">${esc(l)}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
function bannerFor(st) {
  const b = Object.keys(st.breakers || {});
  let h = "";
  if (!st.running) h += `<div class="banner red">Runtime is stopped (market data or venue could not start — see Alerts / System Logs). <button class="btn small" data-act="start">Start</button></div>`;
  if (st.venue === "LIVE") h += `<div class="banner red">LIVE TRADING — real funds at risk</div>`;
  if (b.length) h += `<div class="banner red">Circuit breaker active: ${esc(b.join(", "))} — new trades blocked</div>`;
  if (st.paused) h += `<div class="banner amber">Trading paused — open positions are still managed</div>`;
  return h;
}

const PAGES = {};
PAGES.overview = async () => {
  const st = await api("/api/status");
  S.status = st;
  const p = st.portfolio, pn = st.pnl;
  const perf = await api("/api/performance").catch(() => null);
  const pend = st.pending_decisions || [];
  const recent = await api("/api/decisions?limit=8&actionable=1").catch(() => []);
  const regimes = Object.entries(st.regimes || {});
  return `${bannerFor(st)}<h1>Overview <span class="badge">${esc(st.mode)}</span><span class="badge ${st.venue === "LIVE" ? "live" : ""}">${esc(st.venue)}</span>
    <span class="spacer"></span><button class="btn small" data-act="${st.paused ? "resume" : "pause"}">${st.paused ? "▶ Resume" : "❚❚ Pause"}</button></h1>
  <div class="grid kpis">
    ${kpi("Equity", fmt(p.equity), "USDT")}${kpi("Balance", fmt(p.balance))}${kpi("Unrealized PnL", sgn(pn.unrealized))}
    ${kpi("Realized PnL", sgn(pn.realized_total))}${kpi("Daily PnL", sgn(pn.daily))}${kpi("Weekly PnL", sgn(pn.weekly))}
    ${kpi("Monthly PnL", sgn(pn.monthly))}${kpi("Drawdown", pct(p.drawdown_pct), `month ${pct(p.monthly_drawdown_pct)}`)}
    ${kpi("Open positions", p.positions, `${fmt(p.gross_notional / Math.max(p.equity, 1), 2)}x gross`)}
    ${kpi("Open risk", fmt(p.open_risk), pct(p.open_risk / Math.max(p.equity, 1) * 100))}
    ${kpi("Trades", st.trade_count, `today ${p.trades_today}`)}
    ${kpi("Risk status", Object.keys(st.breakers).length ? '<span class="neg">BLOCKED</span>' : '<span class="pos">OK</span>', `${p.consecutive_losses} consecutive losses`)}
  </div>
  ${pend.length ? `<div class="card"><h2>Awaiting your approval</h2>${pend.map((d) => `<div class="card"><div class="row">${dirPill(d.direction)}<b>${esc(d.symbol)}</b><span class="muted">${esc(d.engine)} · conf ${fmt(d.confidence, 0)} · qty ${d.qty}</span><span class="spacer"></span>
      <button class="btn small" data-act="reject" data-id="${esc(d.id)}">Reject</button><button class="btn small primary" data-act="approve" data-id="${esc(d.id)}">Approve</button></div><pre class="explain">${esc(d.explanation)}</pre></div>`).join("")}</div>` : ""}
  <div class="grid cols2">
    <div class="card"><h2>Equity</h2><canvas class="chart" id="eqChart"></canvas>${perf && perf.equity.length > 1 ? "" : '<div class="muted">The curve appears after the first snapshots.</div>'}</div>
    <div class="card"><h2>Market regime</h2><div class="muted" style="margin-bottom:8px">${esc(st.market || "—")}</div>
      <div class="tablewrap"><table><tr><th>Symbol</th><th>Regime</th></tr>${regimes.map(([s, r]) => `<tr><td>${esc(s)}</td><td><span class="pill">${esc(r)}</span></td></tr>`).join("") || `<tr><td colspan=2>${empty("waiting for data")}</td></tr>`}</table></div></div>
  </div>
  <div class="grid cols2">
    <div class="card"><h2>Active strategies</h2>${st.active_engines.map((e) => `<a class="pill" style="margin:2px" href="#/engine/${e}">${esc(e)}</a>`).join("")}
      <div class="muted" style="margin-top:8px">Data: ${esc(st.feed.mode || "—")} (${esc(st.feed.status)}) · errors ${st.feed.errors}</div></div>
    <div class="card"><h2>Latest decisions</h2>${recent.length ? recent.slice(0, 4).map((d) => `<details><summary>${time(d.ts)} · ${esc(d.symbol)} · <b>${esc(d.outcome)}</b> ${d.engine ? "· " + esc(d.engine) : ""}</summary><pre class="explain">${esc(d.explanation)}</pre></details>`).join("") : empty("No actionable decisions yet — the bot waits for a statistical edge.")}</div>
  </div>`;
};
PAGES.overview.after = async () => {
  const perf = await api("/api/performance").catch(() => null);
  if (perf && perf.equity.length > 1) lineChart($("#eqChart"), perf.equity.map((r) => r.ts), perf.equity.map((r) => r.equity));
};

PAGES.markets = async () => {
  const m = await api("/api/markets");
  const rows = Object.entries(m.views).map(([s, v]) => {
    const sig = (n) => { const x = v.signals[n]; if (!x) return "—"; return x.direction === "FLAT" ? `<span class="muted">${esc(x.state || "—")}</span>` : `${dirPill(x.direction)} ${fmt(x.confidence, 0)}`; };
    return `<tr class="click" data-go="#/chart/${esc(s)}"><td><b>${esc(s)}</b></td><td class="num">${px(v.price)}</td><td><span class="pill">${esc(v.regime.regime)}</span></td>
      <td class="num">${isNum(v.spread_bps) ? fmt(v.spread_bps, 1) : "—"}</td><td class="num">${isNum(v.funding) ? (v.funding * 100).toFixed(4) + "%" : "—"}</td>
      <td>${sig("vwap")}</td><td>${sig("trend")}</td><td>${sig("breakout")}</td><td>${sig("order_flow")}</td></tr>`;
  }).join("");
  const uni = (m.universe || []).slice(0, 25).map((u) => `<tr><td>${esc(u.symbol)}</td><td>${u.eligible ? '<span class="pill ok">eligible</span>' : '<span class="pill bad">excluded</span>'}</td>
    <td class="num">${fmt(u.quote_volume / 1e6, 0)}M</td><td class="num">${fmt(u.spread_bps, 2)}</td><td class="muted">${esc((u.reasons || []).join("; "))}</td></tr>`).join("");
  return `<h1>Live Markets</h1><div class="card"><div class="tablewrap"><table><tr><th>Symbol</th><th class="num">Price</th><th>Regime</th><th class="num">Spread bps</th><th class="num">Funding</th><th>VWAP</th><th>Trend</th><th>Breakout</th><th>Order flow</th></tr>
    ${rows || `<tr><td colspan=9>${empty("Waiting for the first closed bar.")}</td></tr>`}</table></div></div>
    <div class="card"><h2>Universe (liquidity, spread, depth, funding filters)</h2><div class="tablewrap"><table><tr><th>Symbol</th><th>Status</th><th class="num">24h vol</th><th class="num">Spread</th><th>Reasons</th></tr>${uni || `<tr><td colspan=5>${empty()}</td></tr>`}</table></div></div>
    ${m.pairs && m.pairs.length ? `<div class="card"><h2>Stat-arb pairs</h2><div class="tablewrap"><table><tr><th>Pair</th><th>Tradeable</th><th class="num">corr</th><th class="num">coint p</th><th class="num">half-life</th><th class="num">z</th></tr>${m.pairs.map((p) => `<tr><td>${esc(p.y)}/${esc(p.x)}</td><td>${p.tradeable ? "yes" : "no"}</td><td class="num">${fmt(p.correlation, 2)}</td><td class="num">${fmt(p.coint_pvalue, 3)}</td><td class="num">${fmt(p.half_life, 1)}</td><td class="num">${fmt(p.z, 2)}</td></tr>`).join("")}</table></div></div>` : ""}`;
};

PAGES.trades = async () => {
  const st = await api("/api/status");
  const ps = st.positions;
  return `${bannerFor(st)}<h1>Active Trades <span class="spacer"></span>${ps.length ? '<button class="btn danger small" data-act="close-all">Close all</button>' : ""}</h1>
  ${ps.length ? `<div class="grid cols3">${ps.map((p) => `<div class="card"><div class="row">${dirPill(p.direction)}<b>${esc(p.symbol)}</b><span class="muted">${esc(p.engine)} · ${esc(p.setup)}</span><span class="spacer"></span>${sgn(p.unrealized)}</div>
      <div class="tablewrap"><table>
      <tr><td class="muted">Qty</td><td class="num">${p.qty} / ${p.initial_qty}</td></tr><tr><td class="muted">Entry</td><td class="num">${px(p.entry_price)}</td></tr>
      <tr><td class="muted">Mark</td><td class="num">${px(p.mark)}</td></tr><tr><td class="muted">Stop</td><td class="num">${px(p.stop)}</td></tr>
      <tr><td class="muted">Targets</td><td class="num">${p.targets.map((t) => px(t.price)).join(" / ")}</td></tr>
      <tr><td class="muted">Leverage</td><td class="num">${fmt(p.leverage, 0)}x</td></tr><tr><td class="muted">Opened</td><td class="num">${time(p.opened_ts)}</td></tr></table></div>
      <div class="row" style="margin-top:8px"><a class="btn small primary" href="#/chart/${esc(p.symbol)}">📈 Graf</a><span class="spacer"></span><button class="btn small danger" data-act="close-pos" data-id="${esc(p.id)}" data-sym="${esc(p.symbol)}">Close</button></div></div>`).join("")}</div>`
    : `<div class="card">${empty("No open positions. The bot only trades when an eligible engine shows edge after costs and the Risk Manager approves.")}</div>`}`;
};

PAGES.history = async () => {
  const t = await api("/api/trades?limit=500");
  return `<h1>Trade History</h1><div class="card"><div class="tablewrap"><table><tr><th>Closed</th><th>Symbol</th><th>Dir</th><th>Engine</th><th>Regime</th><th class="num">Entry</th><th class="num">Exit</th><th class="num">Net</th><th class="num">R</th><th class="num">Fees</th><th>Exit reason</th><th>Mode</th></tr>
  ${t.map((x) => `<tr class="click" data-go="#/chart/${esc(x.symbol)}/${esc(x.id)}"><td>${time(x.closed_ts)}</td><td>${esc(x.symbol)}</td><td>${dirPill(x.direction)}</td><td>${esc(x.engine)}</td><td class="muted">${esc(x.regime)}</td><td class="num">${px(x.entry_price)}</td><td class="num">${px(x.exit_price)}</td><td class="num">${sgn(x.net_pnl)}</td><td class="num">${sgn(x.r_multiple)}</td><td class="num">${fmt(x.fees)}</td><td>${esc(x.exit_reason)}</td><td>${esc(x.mode)}</td></tr>`).join("") || `<tr><td colspan=12>${empty()}</td></tr>`}</table></div></div>`;
};

PAGES.strategies = async () => {
  const es = await api("/api/engines");
  return `<h1>Strategies</h1><p class="muted">Six independent engines. Each has its own logic, configuration, statistics and enable switch. Regime decides eligibility; the Risk Manager has the final veto.</p>
  <div class="grid cols3">${es.map((e) => `<div class="card"><div class="row"><b>${esc(e.title)}</b><span class="spacer"></span>
    <label class="switch" title="enable"><input type="checkbox" data-act="toggle-engine" data-name="${esc(e.name)}" ${e.enabled ? "checked" : ""}><span></span></label></div>
    <p class="muted">${esc(e.description)}</p>
    <div class="row"><span class="pill">signals ${e.stats.signals}</span><span class="pill long">L ${e.stats.long_signals}</span><span class="pill short">S ${e.stats.short_signals}</span><span class="pill">evals ${e.stats.evaluations}</span></div>
    ${e.edges.length ? `<div class="tablewrap"><table><tr><th>Setup</th><th class="num">n</th><th class="num">E[R] shrunk</th></tr>${e.edges.map((x) => `<tr><td>${esc(x.key.split("|").slice(1).join(" "))}</td><td class="num">${x.n}</td><td class="num">${sgn(x.shrunk_r, 3)}</td></tr>`).join("")}</table></div>` : '<div class="muted" style="margin-top:8px">No validated evidence yet.</div>'}
    <div class="row" style="margin-top:8px"><span class="spacer"></span><a class="btn small" href="#/engine/${esc(e.name)}">Details →</a></div></div>`).join("")}</div>`;
};

PAGES.engine = async (name) => {
  const e = await api(`/api/engines/${encodeURIComponent(name)}`);
  const cfg = await getCfg();
  const cur = Object.entries(e.current || {}).map(([s, x]) => x ? `<tr><td>${esc(s)}</td><td>${x.direction ? dirPill(x.direction) : esc(x.regime || "")}</td><td class="num">${fmt(x.confidence, 0)}</td><td>${esc(x.state || (x.flags || []).join(", "))}</td><td class="muted">${esc((x.reasons || []).slice(0, 2).join("; "))}</td></tr>` : "").join("");
  const m = e.performance || {};
  return `<h1><a href="#/strategies">←</a> ${esc(e.title)} <label class="switch"><input type="checkbox" data-act="toggle-engine" data-name="${esc(e.name)}" ${e.enabled ? "checked" : ""}><span></span></label></h1>
  <div class="grid kpis">${kpi("Signals", e.stats.signals)}${kpi("Trades", m.trades || 0)}${kpi("Win rate", pct((m.win_rate || 0) * 100, 1))}${kpi("Expectancy", sgn(m.expectancy_r, 3) + " R")}${kpi("Profit factor", fmt(m.profit_factor))}${kpi("Max DD", pct(m.max_drawdown_pct))}</div>
  <div class="card"><h2>Current view per instrument</h2><div class="tablewrap"><table><tr><th>Symbol</th><th>Signal</th><th class="num">Conf</th><th>State</th><th>Reasons</th></tr>${cur || `<tr><td colspan=5>${empty("waiting for data")}</td></tr>`}</table></div></div>
  ${e.pairs ? `<div class="card"><h2>Pairs tested</h2><pre class="explain">${esc(JSON.stringify(e.pairs.slice(0, 10), null, 1))}</pre></div>` : ""}
  <div class="card"><h2>Parameters</h2>${configForm(`engines.${e.name}`, cfg.engines[e.name], { skip: ["enabled"] })}</div>
  <div class="card"><h2>Methodology</h2><pre class="explain">${esc(e.doc || "")}</pre></div>
  <div class="card"><h2>Recent signals</h2><pre class="explain">${esc(JSON.stringify(e.stats.recent.slice(-10).reverse(), null, 1))}</pre></div>`;
};

PAGES.team = async () => {
  const a = await api("/api/agents");
  const R = a.risk_manager.utilisation, P = a.performance_auditor;
  const card = (title, body) => `<div class="card"><h2>${esc(title)}</h2>${body}</div>`;
  return `<h1>AI Team</h1><p class="muted">Six deterministic, auditable analytical roles with separated responsibilities. The Risk Manager's veto cannot be overridden.</p>
  <div class="grid cols2">
  ${card("1 · Quant Researcher", `${a.quant_researcher.edges.length ? `<div class="tablewrap"><table><tr><th>Setup</th><th class="num">n</th><th class="num">E[R]</th><th>Verdict</th></tr>${a.quant_researcher.edges.map((x) => `<tr><td>${esc(x.key)}</td><td class="num">${x.n}</td><td class="num">${sgn(x.shrunk_r, 3)}</td><td>${esc(x.verdict)}</td></tr>`).join("")}</table></div>` : empty("No evidence yet.")}
     <h2 style="margin-top:12px">Proposed experiments</h2>${a.quant_researcher.proposed_experiments.map((p) => `<div class="muted">• ${esc(p.experiment)} — ${esc(p.why)}</div>`).join("") || empty()}`)}
  ${card("2 · Market Analyst", `<p>${esc(a.market_analyst.summary || "—")}</p>${a.market_analyst.block_all ? '<div class="banner red">Recommends BLOCK ALL TRADING</div>' : ""}`)}
  ${card("3 · Execution Specialist", Object.keys(a.execution_specialist.per_symbol).length ? `<div class="tablewrap"><table><tr><th>Symbol</th><th class="num">fills</th><th class="num">realized bps</th><th class="num">expected bps</th></tr>${Object.entries(a.execution_specialist.per_symbol).map(([s, x]) => `<tr><td>${esc(s)}</td><td class="num">${x.fills}</td><td class="num">${fmt(x.avg_realized_bps)}</td><td class="num">${fmt(x.avg_expected_bps)}</td></tr>`).join("")}</table></div>` : empty("No fills analysed yet."))}
  ${card("4 · Portfolio Manager", `<div class="row"><span class="pill">net ${pct(a.portfolio_manager.net_exposure_pct)}</span><span class="pill">gross ${pct(a.portfolio_manager.gross_exposure_pct)}</span></div>
     ${a.portfolio_manager.top_correlations.length ? `<div class="tablewrap"><table><tr><th>Pair</th><th class="num">corr</th></tr>${a.portfolio_manager.top_correlations.map((c) => `<tr><td>${esc(c.pair)}</td><td class="num">${fmt(c.corr, 2)}</td></tr>`).join("")}</table></div>` : ""}`)}
  ${card("5 · Risk Manager (veto)", `<div class="row"><span class="pill ok">approvals ${a.risk_manager.approvals}</span><span class="pill bad">vetoes ${a.risk_manager.vetoes}</span><span class="pill">size mult ${R.drawdown_multiplier}</span></div>
     ${(a.risk_manager.recent_verdicts || []).slice(-6).reverse().map((v) => `<div class="muted">${v.approved ? "✓" : "✗"} ${esc(v.symbol)} ${esc((v.reasons || []).join("; "))}</div>`).join("")}`)}
  ${card("6 · Performance Auditor", `${Object.entries(P.engines || {}).map(([n, x]) => `<div><b>${esc(n)}</b> <span class="pill ${x.status === "OK" ? "ok" : x.status === "INSUFFICIENT_DATA" ? "" : "bad"}">${esc(x.status)}</span> <span class="muted">${esc(x.findings.join("; "))}</span></div>`).join("") || empty("No trades to audit yet.")}
     ${(P.recommendations || []).map((r) => `<div class="banner amber">Recommend ${esc(r.action)}: ${esc(r.engine)}</div>`).join("")}<div class="muted">${esc(P.note || "")}</div>`)}
  </div>`;
};

PAGES.regime = async () => {
  const r = await api("/api/regime");
  const elig = r.eligibility;
  const engines = ["vwap", "trend", "breakout", "order_flow", "stat_arb"];
  return `<h1>Market Regime</h1><div class="card"><p>${esc(r.analyst.summary || "Waiting for data.")}</p></div>
  <div class="grid cols2">${Object.entries(r.states).map(([s, x]) => `<div class="card"><div class="row"><b>${esc(s)}</b><span class="pill">${esc(x.regime)}</span>${(x.flags || []).map((f) => `<span class="pill amber">${esc(f)}</span>`).join("")}<span class="spacer"></span><span class="muted">risk ×${x.risk_multiplier}</span></div>
    <div class="muted">${esc((x.reasons || []).join("; "))}</div><div class="muted">Permitted: ${esc(x.permitted_engines.join(", ") || "none")}</div>
    <div class="tablewrap"><table>${Object.entries(x.metrics).map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td class="num">${isNum(v) ? fmt(v, 4) : "—"}</td></tr>`).join("")}</table></div></div>`).join("") || `<div class="card">${empty()}</div>`}</div>
  <div class="card"><h2>Eligibility matrix (editable in Settings → engines.regime)</h2><div class="tablewrap"><table><tr><th>Regime</th>${engines.map((e) => `<th>${e}</th>`).join("")}</tr>
  ${Object.entries(elig).map(([reg, es]) => `<tr><td>${esc(reg)}</td>${engines.map((e) => `<td>${es.includes(e) ? '<span class="pos">✓</span>' : '<span class="muted">·</span>'}</td>`).join("")}</tr>`).join("")}</table></div></div>`;
};

PAGES.risk = async () => {
  const r = await api("/api/risk");
  const u = r.utilisation;
  const bar = (name, val, lim, unit = "%") => { const f = lim ? Math.min(1, Math.abs(val) / lim) : 0; return `<div class="util"><span>${esc(name)}</span><span class="num">${fmt(val, 2)}${unit} / ${fmt(lim, 2)}${unit}</span><div class="bar ${f > 0.8 ? "red" : f > 0.5 ? "amber" : ""}"><i style="width:${(f * 100).toFixed(0)}%"></i></div></div>`; };
  const active = Object.values(r.breakers.active);
  return `<h1>Risk Management</h1>
  ${active.map((b) => `<div class="banner red">${esc(b.name)}: ${esc(b.reason)} <button class="btn small" data-act="reset-breaker" data-name="${esc(b.name)}">Reset…</button></div>`).join("")}
  <div class="grid cols2"><div class="card"><h2>Limit utilisation</h2>
    ${bar("Open risk", u.open_risk_pct, u.open_risk_limit_pct)}${bar("Daily loss", u.daily_loss_pct, u.daily_limit_pct)}${bar("Weekly loss", u.weekly_loss_pct, u.weekly_limit_pct)}
    ${bar("Monthly drawdown", u.monthly_dd_pct, u.monthly_limit_pct)}${bar("Gross leverage", u.gross_leverage, u.max_leverage, "x")}${bar("Positions", u.positions, u.max_positions, "")}
    ${bar("Consecutive losses", u.consecutive_losses, u.max_consecutive_losses, "")}<div class="muted">Drawdown size multiplier: ${u.drawdown_multiplier} (never above 1 — no martingale)</div></div>
  <div class="card"><h2>Recent verdicts</h2>${(r.recent_verdicts || []).slice().reverse().map((v) => `<div>${v.approved ? '<span class="pos">APPROVED</span>' : '<span class="neg">VETO</span>'} ${esc(v.symbol)} <span class="muted">${esc((v.reasons || []).join("; "))}</span></div>`).join("") || empty()}</div></div>
  <div class="card"><h2>Risk limits</h2>${configForm("risk", r.limits)}</div>
  <div class="card"><h2>Breaker events</h2><div class="tablewrap"><table><tr><th>Time</th><th>Event</th><th>Breaker</th><th>Reason</th></tr>${r.events.map((e) => `<tr><td>${time(e.ts)}</td><td>${esc(e.event)}</td><td>${esc(e.name)}</td><td class="muted">${esc(e.reason)}</td></tr>`).join("") || `<tr><td colspan=4>${empty()}</td></tr>`}</table></div></div>`;
};

function metricsTable(m) {
  const keys = ["trades", "net_pnl", "gross_pnl", "return_pct", "win_rate", "profit_factor", "expectancy_r", "expectancy_usdt", "avg_win", "avg_loss", "max_drawdown_pct", "max_drawdown_mtm_pct", "sharpe", "sortino", "calmar", "avg_holding_min", "fees", "funding", "slippage"];
  return `<div class="tablewrap"><table>${keys.filter((k) => k in m).map((k) => `<tr><td class="muted">${esc(label(k))}</td><td class="num">${isNum(m[k]) ? fmt(m[k], k === "trades" ? 0 : 3) : esc(m[k] ?? "—")}</td></tr>`).join("")}</table></div>`;
}
function breakdown(title, obj) {
  if (!obj || !Object.keys(obj).length) return "";
  return `<div class="card"><h2>${esc(title)}</h2><div class="tablewrap"><table><tr><th></th><th class="num">trades</th><th class="num">net</th><th class="num">win</th><th class="num">E[R]</th><th class="num">PF</th></tr>
  ${Object.entries(obj).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v.trades}</td><td class="num">${sgn(v.net_pnl)}</td><td class="num">${pct(v.win_rate * 100, 0)}</td><td class="num">${sgn(v.expectancy_r, 3)}</td><td class="num">${fmt(v.profit_factor)}</td></tr>`).join("")}</table></div></div>`;
}

PAGES.backtest = async () => {
  const j = await api("/api/research/jobs");
  const st = S.status || (await api("/api/status"));
  const jobs = [...j.running, ...j.history.filter((h) => !j.running.find((r) => r.id === h.id))];
  return `<h1>Backtesting & Research Lab</h1><p class="muted">Runs in an isolated copy of the configuration on Binance archive data (data.binance.vision), with fees, slippage, spread and funding. Results never change live settings.</p>
  <div class="card"><form id="btForm"><div class="form">
    <div class="field"><label>Vrsta testa</label><select name="kind"><option value="backtest">Backtest (+ Monte Carlo)</option><option value="train_test">Učenje / provjera</option><option value="walk_forward">Walk-forward (najpošteniji)</option><option value="policy_comparison">Usporedba načina odlučivanja</option><option value="sensitivity">Osjetljivost parametara</option></select></div>
    <div class="field"><label>Parovi (zarezom)</label><input name="symbols" value="${esc((st.symbols || ["BTCUSDT", "ETHUSDT"]).slice(0, 3).join(","))}"></div>
    <div class="field"><label>Koliko dana povijesti</label><input name="days" type="number" value="30" inputmode="numeric"></div>
    <div class="field"><label>Timeframe</label><select name="timeframe">${["3m", "1m", "5m", "15m", "30m", "1h", "4h"].map((t) => `<option ${t === (st.timeframe || "3m") ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    <div class="field"><label>Što testirati (za osjetljivost / walk-forward)</label><select name="preset">${Object.entries(PRESETS).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("")}</select></div>
  </div><details style="margin-top:10px"><summary>Napredno</summary><div class="form" style="margin-top:8px">
    <div class="field"><label>Walk-forward: dani učenja, dani provjere</label><input name="wf" value="20,7"></div>
    <div class="field" style="grid-column:1/-1"><label>Vlastita mreža parametara (JSON) — zamjenjuje predložak</label><textarea name="grid" placeholder='{"engines.vwap.entry_band":[1.0,1.5]}'></textarea></div></div></details>
  <div class="row" style="margin-top:10px"><span class="spacer"></span><button class="btn primary" type="submit">Pokreni test</button></div></form></div>
  <div class="card"><h2>Experiments</h2><div class="tablewrap"><table><tr><th>Started</th><th>Kind</th><th>Status</th><th></th></tr>${jobs.map((x) => `<tr class="${x.status === "done" ? "click" : ""}" ${x.status === "done" ? `data-go="#/result/${esc(x.id)}"` : ""}><td>${time(x.ts)}</td><td>${esc(EXP_NAME[x.kind] || x.kind)}</td><td>${esc({ done: "gotovo", running: "radi", queued: "u redu", failed: "greška" }[x.status] || x.status)}</td><td>${x.status === "done" ? `<a href="#/result/${esc(x.id)}">Otvori →</a>` : x.status === "running" || x.status === "queued" ? "⏳ radi…" : esc(x.error || "")}</td></tr>`).join("") || `<tr><td colspan=4>${empty()}</td></tr>`}</table></div></div>`;
};
PAGES.backtest.after = () => {
  const f = $("#btForm");
  if (!f) return;
  f.onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(f).entries());
    const [tr, te] = (d.wf || "20,7").split(",").map(Number);
    try {
      const grid = d.grid && d.grid.trim() ? JSON.parse(d.grid) : PRESETS[d.preset || "none"].grid;
      if (d.kind === "sensitivity" && !Object.keys(grid).length) { toast("Za osjetljivost odaberi što testirati (predložak).", "warning"); return; }
      const r = await post("/api/research/run", { kind: d.kind, symbols: d.symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean), days: Number(d.days), train_days: tr, test_days: te, grid, timeframe: d.timeframe });
      toast(`Test pokrenut. Skidanje podataka može potrajati minutu.`);
      setTimeout(() => route(), 1500);
    } catch (err) { toast(err.message, "critical"); }
  };
};
// ---------------------------------------------------------------- research results in plain language
const EXP_NAME = { backtest: "Backtest", train_test: "Učenje / provjera", walk_forward: "Walk-forward", policy_comparison: "Usporedba načina odlučivanja", sensitivity: "Osjetljivost parametara" };
const PRESETS = {
  none: { label: "— bez parametara —", grid: {} },
  vwap_band: { label: "VWAP: ulazni pojas 1σ / 1,5σ / 2σ", grid: { "engines.vwap.entry_band": [1.0, 1.5, 2.0] } },
  vwap_rr: { label: "VWAP: minimalni omjer zarade/rizika 0,8 / 1,0 / 1,5", grid: { "engines.vwap.min_rr_tp1": [0.8, 1.0, 1.5] } },
  trend_stop: { label: "Trend: širina stopa 1,5 / 1,8 / 2,5 ATR", grid: { "engines.trend.stop_atr": [1.5, 1.8, 2.5] } },
  trend_tp1: { label: "Trend: TP1 na 1,0 / 1,5 / 2,0 R", grid: { "engines.trend.tp1_r": [1.0, 1.5, 2.0] } },
  breakout_comp: { label: "Breakout: kompresija 15 / 20 / 30 percentil", grid: { "engines.breakout.compression_pct": [15, 20, 30] } },
  cost_gate: { label: "Filter troškova 0,15 / 0,25 / 0,40 R", grid: { "orchestrator.max_cost_r": [0.15, 0.25, 0.4] } },
};
const pctS = (x, d = 2) => (isNum(x) ? `${x > 0 ? "+" : ""}${fmt(x, d)}%` : "—");
function verdict(m, ci) {
  const n = m.trades || 0, e = m.expectancy_r;
  if (n < 30) return { cls: "amber", icon: "⚠️", title: "Premalo tradeova za zaključak", text: `Samo ${n} tradeova. Za pouzdan zaključak treba barem 30, idealno 100+. Pokreni duži period ili više parova.` };
  if (ci && ci.lo > 0) return { cls: "ok", icon: "✅", title: "Obećavajuće", text: "Prosječan trade je pozitivan i to je statistički uvjerljivo (95% interval pouzdanosti je iznad nule). Sljedeći korak: paper trading da se potvrdi uživo." };
  if ((ci && ci.hi < 0) || (m.profit_factor < 0.9 && e < 0)) return { cls: "bad", icon: "❌", title: "Gubitno", text: "Ovakve postavke su na ovim podacima gubile novac nakon troškova. Ne koristiti za pravi novac." };
  return { cls: "amber", icon: "➖", title: "Neodlučno", text: "Rezultat nije ni jasno dobar ni jasno loš — razlika od nule može biti slučajnost. Treba više podataka." };
}
function verdictCard(v) { return `<div class="verdict ${v.cls}"><div class="vi">${v.icon}</div><div><b>${esc(v.title)}</b><div>${esc(v.text)}</div></div></div>`; }
function tile(label, value, hint, cls = "") { return `<div class="card kpi ${cls}"><div class="l">${esc(label)}</div><div class="v">${value}</div>${hint ? `<div class="s">${hint}</div>` : ""}</div>`; }
function plainStory(m, days) {
  const n = m.trades || 0;
  if (!n) return "U ovom periodu bot nije otvorio nijedan trade — nijedan signal nije prošao filtere (režim tržišta, troškovi, rizik). To je namjerno: bez prednosti nema ulaza.";
  const wins = Math.round((m.win_rate || 0) * 10);
  const cost = (m.fees || 0) + (m.slippage || 0) + Math.max(0, m.funding || 0);
  const gross = m.gross_pnl || 0;
  let s = `U ${days ? fmt(days, 0) + " dana" : "testiranom periodu"} bot bi napravio <b>${n}</b> tradeova i završio na <b class="${m.net_pnl >= 0 ? "pos" : "neg"}">${pctS(m.return_pct)}</b> (${sgn(m.net_pnl)} USDT na 10.000). `;
  s += `Otprilike <b>${wins} od 10</b> tradeova je bilo dobitno. Prosječan trade: <b class="${m.expectancy_r >= 0 ? "pos" : "neg"}">${m.expectancy_r >= 0 ? "+" : ""}${fmt(m.expectancy_r, 2)}R</b> `;
  s += `(R = iznos koji riskiraš po tradeu; uz 0,5% rizika, 1R ≈ 50 USDT). Najveći pad od vrha bio je <b>${fmt(m.max_drawdown_pct, 1)}%</b>. `;
  if (cost > 0) s += `Troškovi (naknade ${fmt(m.fees, 0)} + slippage ${fmt(m.slippage, 0)} USDT) ${gross > 0 ? `odnijeli su <b>${fmt(Math.min(999, (cost / gross) * 100), 0)}%</b> bruto zarade` : "su dodatno povećali gubitak"}.`;
  return s;
}
function hbars(rows, unit = "R") {  // rows: [{label, value, sub}]
  const max = Math.max(0.0001, ...rows.map((r) => Math.abs(r.value || 0)));
  return `<div class="hbars">${rows.map((r) => `<div class="hb"><span class="hl">${esc(r.label)}</span><div class="ht"><i class="${r.value >= 0 ? "p" : "n"}" style="width:${(Math.abs(r.value || 0) / max * 50).toFixed(1)}%;${r.value >= 0 ? "left:50%" : `right:50%`}"></i></div><span class="hv ${r.value >= 0 ? "pos" : "neg"}">${r.value >= 0 ? "+" : ""}${fmt(r.value, 2)}${unit}</span>${r.sub ? `<span class="hs">${esc(r.sub)}</span>` : ""}</div>`).join("")}</div>`;
}
function engineBars(by) {
  if (!by || !Object.keys(by).length) return "";
  return `<div class="card"><h2>Koji engine je kako radio</h2>${hbars(Object.entries(by).map(([k, v]) => ({ label: k, value: v.expectancy_r, sub: `${v.trades} tradeova · ${Math.round(v.win_rate * 100)}% dobitnih` })))}
    <p class="muted">Prosječan rezultat po tradeu u R. Zeleno desno = zarađuje, crveno lijevo = gubi.</p></div>`;
}
function weeklyBars(trades) {
  if (!trades || trades.length < 3) return "";
  const wk = {};
  trades.forEach((t) => { const d = new Date(t.closed_ts); const k = new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7)).toISOString().slice(0, 10); wk[k] = (wk[k] || 0) + t.net_pnl; });
  const rows = Object.entries(wk).sort().map(([k, v]) => ({ label: k.slice(5).split("-").reverse().join("."), value: v }));
  const pos = rows.filter((r) => r.value > 0).length;
  return `<div class="card"><h2>Tjedan po tjedan</h2>${hbars(rows, " $")}<p class="muted">${pos} od ${rows.length} tjedana u plusu.</p></div>`;
}
function mcCard(mc) {
  if (!mc || mc.error) return "";
  return `<div class="card"><h2>Monte Carlo — što se moglo dogoditi</h2>
    <p>Bot je ${mc.simulations} puta nasumično ispremiješao i ponovno izvukao iste tradeove da vidi raspon mogućih ishoda.</p>
    <div class="grid kpis">${tile("Vjerojatnost gubitka", `${Math.round(mc.prob_loss * 100)}%`, "postotak simulacija koje završe u minusu", mc.prob_loss > 0.5 ? "bad" : "")}
      ${tile("Tipičan rezultat", pctS(mc.bootstrap_return_pct.p50, 1), `90% simulacija između ${pctS(mc.bootstrap_return_pct.p5, 1)} i ${pctS(mc.bootstrap_return_pct.p95, 1)}`)}
      ${tile("Najgori pad (95%)", `${fmt(mc.bootstrap_max_dd_pct.p95, 1)}%`, "u 95% simulacija pad nije bio veći od ovoga")}</div></div>`;
}
function tech(r) { return `<details class="card"><summary>Tehnički detalji (za napredne)</summary><pre class="explain">${esc(JSON.stringify(r, null, 1))}</pre></details>`; }

PAGES.result = async (id) => {
  const j = await api(`/api/research/jobs/${encodeURIComponent(id)}`);
  const r = j.result || {};
  const kind = j.kind;
  const head = `<h1><a href="#/backtest">←</a> ${esc(EXP_NAME[kind] || kind)}</h1><p class="muted">Simulacija na povijesnim podacima s naknadama, slippageom i fundingom. Prošli rezultati ne jamče buduće.</p>`;
  if (j.status === "failed" || r.error) return head + `<div class="card"><b>Eksperiment nije uspio:</b> ${esc(r.error || j.error)}</div>`;
  if (kind === "backtest" && r.metrics) {
    const m = r.metrics;
    S.resultCurve = r.equity_curve;
    const days = r.days || (m.days);
    return head + verdictCard(verdict(m, r.ci95)) + `<div class="card"><p class="big">${plainStory(m, days)}</p></div>
      <div class="grid kpis">${tile("Rezultat", `<span class="${m.net_pnl >= 0 ? "pos" : "neg"}">${pctS(m.return_pct)}</span>`, `${sgn(m.net_pnl)} USDT`)}
      ${tile("Tradeova", m.trades, `${Math.round((m.win_rate || 0) * 100)}% dobitnih`)}${tile("Prosječan trade", `${m.expectancy_r >= 0 ? "+" : ""}${fmt(m.expectancy_r, 2)}R`, r.ci95 ? `95% sigurno između ${fmt(r.ci95.lo, 2)} i ${fmt(r.ci95.hi, 2)}R` : "")}
      ${tile("Profit faktor", fmt(m.profit_factor), "zarada ÷ gubici (iznad 1 = zarađuje)")}${tile("Najveći pad", `${fmt(m.max_drawdown_pct, 1)}%`, "od najvišeg stanja računa")}
      ${tile("Troškovi", `${fmt((m.fees || 0) + (m.slippage || 0), 0)} $`, "naknade + slippage")}</div>
      <div class="card"><h2>Stanje računa kroz vrijeme</h2><canvas class="chart" id="resChart"></canvas></div>
      ${engineBars(m.by_engine)}${weeklyBars(r.trades)}${mcCard(r.monte_carlo)}
      ${m.long && m.short ? `<div class="card"><h2>Long vs short</h2>${hbars([{ label: "LONG", value: m.long.expectancy_r, sub: `${m.long.trades} tradeova` }, { label: "SHORT", value: m.short.expectancy_r, sub: `${m.short.trades} tradeova` }])}</div>` : ""}
      ${tech(r)}`;
  }
  if (kind === "train_test") {
    const a = r.train || {}, b = r.test || {};
    const held = isNum(a.expectancy_r) && isNum(b.expectancy_r) && (b.expectancy_r >= 0) === (a.expectancy_r >= 0);
    const v = (b.trades || 0) < 30 ? verdict(b, null) : b.expectancy_r > 0 && held ? { cls: "ok", icon: "✅", title: "Rezultat se zadržao", text: "I na novim podacima (koje strategija 'nije vidjela') rezultat je ostao pozitivan." } :
      { cls: "bad", icon: "❌", title: "Rezultat se nije zadržao", text: "Na novim podacima strategija nije zarađivala. To znači da dobar rezultat (ako ga je bilo) nije pouzdan." };
    const col = (t, x) => `<div class="card"><h2>${t}</h2><p class="big"><b class="${x.net_pnl >= 0 ? "pos" : "neg"}">${pctS(x.return_pct)}</b> · ${x.trades} tradeova · prosjek ${fmt(x.expectancy_r, 2)}R</p><p class="muted">profit faktor ${fmt(x.profit_factor)} · najveći pad ${fmt(x.max_drawdown_pct, 1)}%</p></div>`;
    return head + verdictCard(v) + `<div class="card"><p>Podaci su podijeljeni na dva dijela: <b>prvi dio</b> (učenje) i <b>drugi dio</b> (provjera na podacima koje strategija nije vidjela). Pravi test je drugi dio.</p></div>
      <div class="grid cols2">${col("1. dio — učenje", a)}${col("2. dio — provjera", b)}</div>${tech(r)}`;
  }
  if (kind === "walk_forward") {
    const m = r.oos_metrics || {};
    const dsr = r.deflated_sharpe ? r.deflated_sharpe.dsr : null;
    const folds = (r.folds || []).map((f, k) => ({ label: `Krug ${k + 1}`, value: f.test.return_pct || 0, sub: `${f.test.trades} tradeova${Object.keys(f.chosen || {}).length ? " · " + Object.entries(f.chosen).map(([a, b]) => a.split(".").pop() + "=" + b).join(", ") : ""}` }));
    return head + verdictCard(verdict(m, r.expectancy_ci95)) + `<div class="card"><p>Walk-forward ponavlja ovo više puta zaredom: <b>nauči</b> na jednom razdoblju, pa <b>trguj</b> na idućem koje nije vidio. Ovo je najpošteniji test jer nalikuje stvarnom trgovanju.</p>
      <p class="big">${plainStory(m)}</p>${isNum(dsr) ? `<p>Vjerojatnost da rezultat nije samo sreća (deflated Sharpe): <b>${Math.round(dsr * 100)}%</b> ${dsr >= 0.95 ? "— uvjerljivo" : "— nije dovoljno uvjerljivo (treba 95%+)"}.</p>` : ""}</div>
      <div class="card"><h2>Rezultat svakog kruga (na neviđenim podacima)</h2>${hbars(folds, "%")}</div>${tech(r)}`;
  }
  if (kind === "policy_comparison") {
    const NAMES = { regime_gated: "Prema režimu tržišta (zadano)", majority_vote: "Većina engineova", confidence_average: "Prosjek pouzdanosti" };
    const rows = Object.entries(r.results || {}).map(([k, v]) => ({ label: NAMES[k] || k, value: v.return_pct || 0, sub: `${v.trades} tradeova · prosjek ${fmt(v.expectancy_r, 2)}R` }));
    return head + `<div class="card"><p>Isti podaci, tri različita načina na koja središnji sustav odlučuje. Pokazuje radi li naš način (prema režimu tržišta) bolje od jednostavnih alternativa.</p></div><div class="card"><h2>Rezultat</h2>${hbars(rows, "%")}</div>${tech(r)}`;
  }
  if (kind === "sensitivity") {
    const vs = r.variants || [];
    if (!vs.length) return head + `<div class="verdict amber"><div class="vi">ℹ️</div><div><b>Nisi odabrao parametre za testiranje</b><div>Pokreni ponovno i u polju „Što testirati“ odaberi predložak (npr. „VWAP: ulazni pojas“).</div></div></div>` + tech(r);
    const rows = vs.map((x) => ({ label: `${x.param.split(".").pop()} = ${x.value}`, value: x.expectancy_r || 0, sub: `${x.trades} tradeova · ${pctS(x.return_pct)}` }));
    const interp = { "broad plateau": "Stabilno — rezultat ne ovisi o točnoj vrijednosti (dobar znak).", "fragile: performance depends on exact parameters": "Krhko — rezultat jako ovisi o točnoj vrijednosti (znak prenaučenosti).", mixed: "Mješovito." }[r.interpretation] || "";
    return head + `<div class="card"><p>Isti test s malo promijenjenim postavkama. Ako rezultat ostaje sličan, strategija je robusna; ako skače, vjerojatno je slučajnost.</p><p><b>${esc(interp)}</b></p></div>
      <div class="card"><h2>Prosječan trade po varijanti</h2>${hbars(rows)}<p class="muted">Osnovna postavka: ${fmt(r.base.expectancy_r, 2)}R, ${r.base.trades} tradeova.</p></div>${tech(r)}`;
  }
  return head + tech(r);
};
PAGES.result.after = () => { const c = S.resultCurve; if (c && c.equity && c.equity.length > 1) lineChart($("#resChart"), c.ts, c.equity); S.resultCurve = null; };

PAGES.paper = async () => {
  const p = await api("/api/paper");
  return `<h1>Paper Trading <span class="spacer"></span>${p.venue === "PAPER" ? '<button class="btn small danger" data-act="paper-reset">Reset account…</button>' : ""}</h1>
  <div class="banner amber">${esc(p.note)}</div>
  <div class="grid kpis">${kpi("Venue", esc(p.venue))}${kpi("Paper wallet", fmt(p.wallet))}${kpi("Fills", p.fills.length)}</div>
  <div class="card"><h2>Simulated fills</h2><div class="tablewrap"><table><tr><th>Time</th><th>Symbol</th><th>Side</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Ref</th><th class="num">Fee</th><th class="num">Realized</th><th>Type</th></tr>
  ${p.fills.map((f) => `<tr><td>${time(f.ts)}</td><td>${esc(f.symbol)}</td><td>${esc(f.side)}</td><td class="num">${f.qty}</td><td class="num">${px(f.price)}</td><td class="num">${px(f.ref_price)}</td><td class="num">${fmt(f.fee, 4)}</td><td class="num">${sgn(f.realized)}</td><td>${esc(f.order_type)}</td></tr>`).join("") || `<tr><td colspan=9>${empty()}</td></tr>`}</table></div></div>`;
};

PAGES.performance = async () => {
  const p = await api("/api/performance");
  const m = p.metrics;
  S.perfCurve = p.equity;
  return `<h1>Performance Analytics</h1>${m.trades ? "" : '<div class="card">' + empty("No closed trades yet. Metrics are computed only from real (paper/testnet/live) trades — never estimated.") + "</div>"}
  <div class="grid cols2"><div class="card"><h2>Summary</h2>${metricsTable(m)}</div><div class="card"><h2>Equity</h2><canvas class="chart" id="perfChart"></canvas></div></div>
  ${breakdown("By engine", m.by_engine)}${breakdown("By regime", m.by_regime)}${breakdown("By instrument", m.by_symbol)}${breakdown("By hour (UTC)", m.by_hour_utc)}${breakdown("Long / short", m.long ? { long: m.long, short: m.short } : null)}${breakdown("By exit", m.by_exit)}`;
};
PAGES.performance.after = () => { const c = S.perfCurve; if (c && c.length > 1) lineChart($("#perfChart"), c.map((r) => r.ts), c.map((r) => r.equity)); };

PAGES.settings = async () => {
  const d = await api("/api/config");
  const c = d.config;
  S.cfg = c;
  const tab = S.param || "system";
  const sections = { system: c.system, orchestrator: c.orchestrator, costs: c.costs, execution: c.execution, universe: c.universe, notifications: c.notifications,
    "engines.regime": c.engines.regime, "engines.vwap": c.engines.vwap, "engines.trend": c.engines.trend, "engines.breakout": c.engines.breakout,
    "engines.order_flow": c.engines.order_flow, "engines.stat_arb": c.engines.stat_arb };
  return `<h1>Settings</h1><div class="tabs">${Object.keys(sections).map((k) => `<button data-go="#/settings/${k}" class="${k === tab ? "active" : ""}">${esc(k)}</button>`).join("")}<button data-go="#/settings/theme" class="${tab === "theme" ? "active" : ""}">appearance</button></div>
  ${tab === "theme" ? `<div class="card"><h2>Appearance</h2><div class="row"><button class="btn" data-act="theme" data-v="dark">Dark</button><button class="btn" data-act="theme" data-v="light">Light</button><button class="btn" data-act="theme" data-v="">System</button></div></div>`
    : `<div class="card">${tab === "system" ? '<div class="banner amber">Changing the execution timeframe keeps chart settings (EMA/ADX/ATR/Bollinger/VWAP bands) and rescales durations (time stops, cooldowns, statistics windows) so they mean the same time. Positions must be closed; the market-data stream restarts.</div>' : ""}${tab === "execution" ? '<div class="banner amber">Venue PAPER/TESTNET only here. LIVE is enabled from API Connection with a typed confirmation and a deployment flag.</div>' : ""}${configForm(tab, sections[tab])}</div>`}
  <div class="card"><h2>Configuration versions</h2><div class="tablewrap"><table><tr><th>#</th><th>Time</th><th>By</th><th>Note</th><th></th></tr>${d.versions.map((v) => `<tr><td>${v.id}</td><td>${time(v.ts)}</td><td>${esc(v.actor)}</td><td class="muted">${esc(v.note)}</td><td><button class="btn small" data-act="rollback" data-id="${v.id}">Restore</button></td></tr>`).join("")}</table></div></div>`;
};

PAGES.api = async () => {
  const k = await api("/api/keys");
  const keyCard = (env) => `<div class="card"><h2>Binance ${env.toUpperCase()}</h2>
    <div class="row"><span class="pill ${k[env].key.set ? "ok" : ""}">${k[env].key.set ? "key " + esc(k[env].key.preview) : "no key"}</span><span class="pill ${k[env].secret_set ? "ok" : ""}">${k[env].secret_set ? "secret stored (encrypted)" : "no secret"}</span></div>
    <form class="keyform" data-env="${env}"><div class="form" style="margin-top:10px"><div class="field"><label>API key</label><input name="key" autocomplete="off" spellcheck="false"></div><div class="field"><label>API secret</label><input name="secret" type="password" autocomplete="off"></div></div>
    <div class="row" style="margin-top:10px"><button class="btn small" type="button" data-act="key-test" data-env="${env}">Test connection</button><button class="btn small" type="button" data-act="key-del" data-env="${env}">Delete</button><span class="spacer"></span><button class="btn primary small" type="submit">Save encrypted</button></div></form></div>`;
  return `<h1>API Connection</h1><div class="card"><p>Create keys with <b>Futures trading + reading only</b>. Never enable withdrawals — keys allowing withdrawals are rejected. Restrict the key to your IP where possible. Secrets are encrypted at rest and never shown again.</p>
    <div class="row"><span class="pill">venue ${esc(k.venue)}</span><span class="pill ${k.live_allowed_by_deployment ? "amber" : ""}">live ${k.live_allowed_by_deployment ? "allowed by deployment" : "disabled by deployment"}</span></div></div>
  <div class="grid cols2">${keyCard("testnet")}${keyCard("live")}</div>
  <div class="card"><h2>Enable LIVE trading</h2><p class="muted">Requires: QT_ALLOW_LIVE=1 on the server, a successful LIVE key check within the last hour (no withdrawal permission), no open positions, and typed confirmation. Paper trading remains the default.</p>
    <button class="btn danger" data-act="enable-live">Enable LIVE…</button></div>
  <div class="card"><h2>Telegram notifications</h2><div class="row"><span class="pill">${k.telegram.set ? "bot token " + esc(k.telegram.preview) : "no bot token"}</span></div>
    <form class="keyform" data-env="telegram"><div class="form" style="margin-top:10px"><div class="field"><label>Bot token</label><input name="token" type="password" autocomplete="off"></div></div><div class="row" style="margin-top:10px"><span class="spacer"></span><button class="btn primary small" type="submit">Save encrypted</button></div></form>
    <p class="muted">Set the chat id and enable Telegram in Settings → notifications.</p></div>`;
};
document.addEventListener("submit", async (e) => {
  const f = e.target.closest(".keyform");
  if (!f) return;
  e.preventDefault();
  const body = { env: f.dataset.env, ...Object.fromEntries(new FormData(f).entries()) };
  try { await post("/api/keys", body); toast("Stored encrypted."); route(); } catch (err) { toast(err.message, "critical"); }
});

PAGES.alerts = async () => {
  const a = await api("/api/alerts?limit=300");
  const cfg = await getCfg();
  return `<h1>Alerts <span class="spacer"></span><button class="btn small" data-act="alerts-read">Mark all read</button></h1>
  <div class="card"><div class="tablewrap"><table><tr><th>Time</th><th>Severity</th><th>Type</th><th>Message</th></tr>${a.map((x) => `<tr><td>${time(x.ts)}</td><td><span class="pill ${x.severity === "critical" ? "bad" : x.severity === "warning" ? "amber" : ""}">${esc(x.severity)}</span></td><td>${esc(x.kind)}</td><td style="white-space:normal">${esc(x.message)}</td></tr>`).join("") || `<tr><td colspan=4>${empty()}</td></tr>`}</table></div></div>
  <div class="card"><h2>Notification settings</h2>${configForm("notifications", cfg.notifications)}</div>`;
};

PAGES.logs = async () => {
  const l = await api("/api/logs");
  const tab = S.param || "system";
  const rows = tab === "audit" ? l.audit.map((x) => `<tr><td>${time(x.ts)}</td><td>${esc(x.actor)}</td><td>${esc(x.action)}</td><td class="muted" style="white-space:normal">${esc(x.detail_json)}</td></tr>`)
    : tab === "orders" ? l.orders.map((x) => `<tr><td>${time(x.ts)}</td><td>${esc(x.client_id)}</td><td>${esc(x.event)}</td><td class="muted" style="white-space:normal">${esc(x.data_json)}</td></tr>`)
    : l.logs.map((x) => `<tr><td>${time(x.ts)}</td><td><span class="pill ${x.level === "ERROR" ? "bad" : x.level === "WARNING" ? "amber" : ""}">${esc(x.level)}</span></td><td>${esc(x.logger)}</td><td class="mono" style="white-space:normal">${esc(x.msg)}</td></tr>`);
  return `<h1>System Logs</h1><div class="tabs">${["system", "orders", "audit"].map((t) => `<button data-go="#/logs/${t}" class="${t === tab ? "active" : ""}">${t}</button>`).join("")}</div>
  <div class="card"><div class="tablewrap"><table>${rows.join("") || `<tr><td>${empty()}</td></tr>`}</table></div></div>`;
};


// ---------------------------------------------------------------- live trading chart
const HR_STATE = {
  INSIDE_BANDS: "cijena unutar VWAP pojaseva — nema odstupanja za trgovati", NORMAL_DEVIATION: "cijena izvan pojasa, čeka se potvrda okreta",
  OVEREXTENDED: "cijena jako daleko od VWAP-a — previše rizično za ulaz", STRONG_TREND_CONTINUATION: "jak trend — bot ne ide protiv trenda",
  POTENTIAL_REVERSAL: "moguć okret prema VWAP-u, provjeravaju se volumen i tok naloga", FALSE_REJECTION: "lažni okret — odbačeno",
  VWAP_RECLAIM: "cijena se vratila iznad VWAP-a", VWAP_BREAKDOWN: "cijena je pala ispod VWAP-a", HIGH_VOLATILITY: "previsoka volatilnost",
  INSUFFICIENT_SESSION: "dan je tek počeo — VWAP još nije pouzdan", NO_TREND: "nema jasnog trenda", TREND_REVERSAL: "trend se okreće",
  TREND_EXHAUSTION: "trend gubi snagu", MOMENTUM_ACCELERATION: "zamah ubrzava", STRONG_BULL: "jak uzlazni trend", STRONG_BEAR: "jak silazni trend",
  WEAK_BULL: "slab uzlazni trend", WEAK_BEAR: "slab silazni trend", COMPRESSION: "tržište se stišće — moguć proboj", NO_SETUP: "nema postavke",
  VALID_BREAKOUT: "valjan proboj", LOW_VOLUME_BREAKOUT: "proboj bez volumena — odbačeno", FALSE_BREAKOUT: "lažni proboj", BREAKOUT_CONTINUATION: "proboj se nastavlja",
  BALANCED: "kupci i prodavači u ravnoteži", AGGRESSIVE_BUYING: "agresivna kupnja", AGGRESSIVE_SELLING: "agresivna prodaja", ABSORPTION: "veliki volumen ne pomiče cijenu",
  WARMUP: "skupljanje podataka", RANGE: "bočno tržište", STRONG_TREND: "jak trend", WEAK_TREND: "slab trend", HIGH_VOLATILITY_REGIME: "visoka volatilnost",
  VOLATILITY_COMPRESSION: "volatilnost se stišće", VOLATILITY_EXPANSION: "volatilnost raste", MARKET_DISLOCATION: "poremećaj tržišta — bez novih ulaza",
  UNSAFE_MARKET: "nesigurno tržište — bez novih ulaza", UNKNOWN: "premalo podataka", LOW_VOLATILITY: "niska volatilnost",
};
const EXIT_LABEL = { take_profit_1: "TP1", take_profit_2: "TP2", take_profit_3: "TP3", take_profit_4: "TP4", stop_loss: "SL", breakeven_stop: "BE",
  trailing_stop: "Trail", time_stop: "Vrijeme", signal_exit: "Signal", regime_exit: "Režim", manual_close: "Ručno", manual_close_all: "Ručno",
  emergency_stop: "STOP", end_of_test: "Kraj" };
const tz = () => new Date().getTimezoneOffset() * 60;
const T = (ms) => Math.floor(ms / 1000) - tz();
const exitLabel = (r) => EXIT_LABEL[r] || (String(r).startsWith("take_profit") ? "TP" : String(r).slice(0, 8));
function snap(times, ms) {  // bar (open time) containing ms
  const t = T(ms); let lo = 0, hi = times.length - 1, best = times[0];
  while (lo <= hi) { const m = (lo + hi) >> 1; if (times[m] <= t) { best = times[m]; lo = m + 1; } else hi = m - 1; }
  return best;
}
function chartColors() {
  const cs = getComputedStyle(document.documentElement), v = (n) => cs.getPropertyValue(n).trim();
  return { bg: v("--panel"), text: v("--muted"), line: v("--line"), green: v("--green"), red: v("--red"), amber: v("--amber"), accent: v("--accent"), violet: v("--violet") };
}
const OVL_DEFAULT = { vwap: true, bands: true, ema: true, volume: true };
function overlays() { try { return { ...OVL_DEFAULT, ...JSON.parse(store.get("qt_ovl") || "{}") }; } catch { return { ...OVL_DEFAULT }; } }

PAGES.chart = async (param) => {
  const [symRaw, tradeId] = String(param || "").split("/");
  const st = S.status || (await api("/api/status"));
  const sym = (symRaw || (st.positions[0] && st.positions[0].symbol) || (st.symbols || [])[0] || "BTCUSDT").toUpperCase();
  S.chartSym = sym; S.chartTrade = tradeId || null;
  const held = new Set((st.positions || []).map((p) => p.symbol));
  const ov = overlays();
  const chip = (k, lbl, col) => `<button class="ovl ${ov[k] ? "on" : ""}" data-act="ovl" data-k="${k}"><i style="background:${col}"></i>${lbl}</button>`;
  return `<h1>Graf <span class="pill">${esc(sym)}</span><span class="spacer"></span><span class="muted" id="chartTf"></span></h1>
  <div class="tabs">${(st.symbols || []).map((s) => `<button data-go="#/chart/${s}" class="${s === sym ? "active" : ""}">${held.has(s) ? "● " : ""}${esc(s.replace("USDT", ""))}</button>`).join("")}</div>
  <div class="card chartcard"><div id="tvchart" class="tvchart"></div>
    <div class="row ovls">${chip("vwap", "VWAP", "var(--amber)")}${chip("bands", "VWAP ±σ", "color-mix(in srgb, var(--amber) 50%, transparent)")}${chip("ema", "EMA 20/50/200", "var(--accent)")}${chip("volume", "Volumen", "var(--muted)")}</div></div>
  <div id="chartInfo"></div>`;
};
PAGES.chart.after = async () => { await drawChart(true); S.chartTimer = setInterval(() => { if (!document.hidden && S.route === "chart") drawChart(false).catch(() => {}); }, 5000); };

async function drawChart(fresh) {
  const sym = S.chartSym;
  const d = await api(`/api/chart/${encodeURIComponent(sym)}?bars=400`);
  if (S.route !== "chart" || S.chartSym !== sym) return;
  const el = $("#tvchart");
  if (!el || !window.LightweightCharts) { if (el) el.innerHTML = '<div class="empty">Grafički modul nije učitan.</div>'; return; }
  const C = chartColors();
  const LC = window.LightweightCharts;
  if (fresh || !S.chart) {
    if (S.chart) { try { S.chart.remove(); } catch {} }
    const chart = LC.createChart(el, {
      autoSize: true, layout: { background: { type: "solid", color: C.bg }, textColor: C.text, fontSize: 11 },
      grid: { vertLines: { color: C.line }, horzLines: { color: C.line } }, rightPriceScale: { borderColor: C.line },
      timeScale: { borderColor: C.line, timeVisible: true, secondsVisible: false, rightOffset: 6 }, crosshair: { mode: 0 },
      localization: { priceFormatter: (x) => px(x) },
    });
    const S2 = { chart };
    S2.candles = chart.addCandlestickSeries({ upColor: C.green, downColor: C.red, wickUpColor: C.green, wickDownColor: C.red, borderVisible: false });
    S2.candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.2 } });
    S2.volume = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    const line = (color, width, style = 0) => chart.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    S2.vwap = line(C.amber, 2);
    S2.bands = Object.keys(d.bands).map((k) => ({ k, up: line(C.amber, 1, Number(k) >= 2 ? 3 : 2), lo: line(C.amber, 1, Number(k) >= 2 ? 3 : 2) }));
    const emaCol = [C.accent, C.violet, C.text];
    S2.ema = Object.keys(d.ema).map((n, i) => ({ n, s: line(emaCol[i % 3], 1) }));
    S2.lines = [];
    S.chart = chart; S.cs = S2;
  }
  const s = S.cs, times = d.ts.map(T);
  const bars = d.ts.map((_, i) => ({ time: times[i], open: d.o[i], high: d.h[i], low: d.l[i], close: d.c[i] })).filter((b) => b.open != null);
  s.candles.setData(bars);
  s.volume.setData(d.ts.map((_, i) => ({ time: times[i], value: d.v[i] || 0, color: (d.c[i] >= d.o[i] ? C.green : C.red) + "55" })));
  const ser = (arr) => arr.map((v, i) => (v == null ? { time: times[i] } : { time: times[i], value: v }));
  s.vwap.setData(ser(d.vwap));
  s.bands.forEach((b) => { b.up.setData(ser(d.bands[b.k].up)); b.lo.setData(ser(d.bands[b.k].lo)); });
  s.ema.forEach((e) => e.s.setData(ser(d.ema[e.n])));
  const ov = overlays();
  s.vwap.applyOptions({ visible: ov.vwap }); s.bands.forEach((b) => { b.up.applyOptions({ visible: ov.bands }); b.lo.applyOptions({ visible: ov.bands }); });
  s.ema.forEach((e) => e.s.applyOptions({ visible: ov.ema })); s.volume.applyOptions({ visible: ov.volume });
  // levels
  s.lines.forEach((l) => { try { s.candles.removePriceLine(l); } catch {} });
  s.lines = [];
  const pl = (price, color, title, style = 0, width = 2) => { if (isNum(price)) s.lines.push(s.candles.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title })); };
  const markers = [];
  const levelsOf = (dir, entry, stop, targets, hits, trail, curStop) => {
    pl(entry, C.accent, `ULAZ ${dir === "LONG" ? "▲" : "▼"}`);
    const tps = trail > 0 ? targets.slice(0, -1) : targets;
    tps.forEach((t, k) => pl(t.price ?? t, k < hits ? C.text : C.green, `TP${k + 1}${t.fraction && t.fraction < 1 ? " " + Math.round(t.fraction * 100) + "%" : ""}${k < hits ? " ✓" : ""}`, k < hits ? 2 : 0));
    const cs = curStop ?? stop;
    const beyond = (cs - entry) * (dir === "LONG" ? 1 : -1);
    pl(cs, C.red, beyond > 0 ? "TRAILING SL" : Math.abs(beyond) < 1e-12 ? "SL break-even" : "STOP LOSS");
    if (isNum(stop) && Math.abs(stop - cs) > 1e-12) pl(stop, C.red, "početni SL", 1, 1);
  };
  for (const p of d.positions) {
    levelsOf(p.direction, p.entry_price, p.initial_stop, p.targets, p.tp_hits, p.trail_atr, p.stop);
    markers.push({ time: snap(times, p.opened_ts), position: p.direction === "LONG" ? "belowBar" : "aboveBar", color: C.accent, shape: p.direction === "LONG" ? "arrowUp" : "arrowDown", text: `ULAZ ${p.engine}` });
  }
  for (const t of d.trades) {
    const long = t.direction === "LONG";
    markers.push({ time: snap(times, t.opened_ts), position: long ? "belowBar" : "aboveBar", color: C.accent, shape: long ? "arrowUp" : "arrowDown", text: `${t.engine}` });
    for (const x of (t.meta && t.meta.exits) || []) {
      const good = String(x.reason).startsWith("take_profit") || (x.price - t.entry_price) * (long ? 1 : -1) > 0;
      markers.push({ time: snap(times, x.ts || t.closed_ts), position: long ? "aboveBar" : "belowBar", color: good ? C.green : C.red, shape: "circle", text: exitLabel(x.reason) });
    }
    if (S.chartTrade === t.id) levelsOf(t.direction, t.entry_price, t.meta && t.meta.stop, (t.meta && t.meta.targets) || [], 0, 0, t.meta && t.meta.stop);
  }
  for (const dc of d.decisions) {
    if (!["APPROVE_LONG", "APPROVE_SHORT"].includes(dc.outcome) || dc.status === "executed") continue;
    markers.push({ time: snap(times, dc.ts), position: dc.direction === "LONG" ? "belowBar" : "aboveBar", color: C.violet, shape: "square", text: dc.status === "pending_user" ? "čeka odobrenje" : "signal" });
  }
  markers.sort((a, b) => a.time - b.time);
  s.candles.setMarkers(markers);
  if (fresh && S.chartTrade) {
    const t = d.trades.find((x) => x.id === S.chartTrade);
    if (t) s.chart.timeScale().setVisibleRange({ from: snap(times, t.opened_ts) - 60 * 60, to: snap(times, t.closed_ts) + 60 * 60 });
  } else if (fresh) s.chart.timeScale().scrollToRealTime();
  $("#chartTf").textContent = `${d.timeframe} · ${new Date().toLocaleTimeString()}`;
  $("#chartInfo").innerHTML = chartInfo(d);
}

function chartInfo(d) {
  const last = d.c[d.c.length - 1];
  const pct = (a, b) => ((a - b) / b) * 100;
  let h = "";
  for (const p of d.positions) {
    const long = p.direction === "LONG", sg = long ? 1 : -1, R = Math.abs(p.entry_price - p.initial_stop);
    const ur = R > 0 ? ((last - p.entry_price) * sg) / R : 0;
    const tps = (p.trail_atr > 0 ? p.targets.slice(0, -1) : p.targets);
    const next = tps.find((_, k) => k >= p.tp_hits);
    const prog = next ? Math.max(0, Math.min(1, (last - p.stop) / (next.price - p.stop))) : 1;
    h += `<div class="card"><h2>Što bot sada radi</h2>
      <p class="big">${long ? "📈 Kupio" : "📉 Prodao (short)"} <b>${esc(p.symbol)}</b> na <b>${px(p.entry_price)}</b> — engine <b>${esc(p.engine)}</b> (${esc(p.setup)}).
      Trenutno ${sgn(p.unrealized)} USDT (<b class="${ur >= 0 ? "pos" : "neg"}">${ur >= 0 ? "+" : ""}${fmt(ur, 2)}R</b>).</p>
      <p class="muted">R = iznos koji bot riskira na ovom tradeu (${fmt(p.initial_risk_usdt)} USDT). +1R znači zaradu jednaku riziku.</p>
      <div class="levels">
        ${tps.map((t, k) => `<div class="lv ${k < p.tp_hits ? "hit" : ""}"><span class="tag tp">TP${k + 1}</span><span class="num">${px(t.price)}</span><span class="muted">${fmt(pct(t.price, last), 2)}% od cijene · zatvara ${t.fraction < 1 ? Math.round(t.fraction * 100) + "%" : "ostatak"}${k < p.tp_hits ? " · ✓ ostvaren" : ""}</span></div>`).join("")}
        ${p.trail_atr > 0 ? `<div class="lv"><span class="tag tp">RUNNER</span><span class="muted">ostatak prati trailing stop (${fmt(p.trail_atr, 1)} × ATR)</span></div>` : ""}
        <div class="lv"><span class="tag entry">ULAZ</span><span class="num">${px(p.entry_price)}</span><span class="muted">${time(p.opened_ts)}</span></div>
        <div class="lv"><span class="tag sl">SL</span><span class="num">${px(p.stop)}</span><span class="muted">${fmt(pct(p.stop, last), 2)}% od cijene${p.stop !== p.initial_stop ? ` · pomaknut (početni ${px(p.initial_stop)})` : ""}</span></div>
      </div>
      ${next ? `<div class="muted" style="margin-top:8px">Put od stop-lossa do sljedećeg cilja (TP${p.tp_hits + 1})</div><div class="bar"><i style="width:${(prog * 100).toFixed(0)}%"></i></div>` : ""}
    </div>`;
  }
  const reg = d.regime;
  const sig = Object.entries(d.signals || {});
  h += `<div class="card"><h2>Što engineovi vide na ${esc(d.symbol)}</h2>
    ${reg ? `<p><span class="pill">${esc(reg.regime)}</span> ${esc(HR_STATE[reg.regime] || "")}</p><p class="muted">Dopušteni engineovi u ovom režimu: ${esc((reg.permitted_engines || []).join(", ") || "nijedan")}</p>` : ""}
    ${sig.map(([n, x]) => `<div class="lv"><b style="min-width:92px">${esc(n)}</b>${x.direction && x.direction !== "FLAT" ? dirPill(x.direction) + ` ${fmt(x.confidence, 0)}/100` : '<span class="pill">čeka</span>'}<span class="muted">${esc(HR_STATE[x.state] || x.state || "")}</span></div>`).join("") || empty("Čeka se prva zatvorena svjećica.")}
    ${d.positions.length ? "" : '<p class="muted" style="margin-top:8px">Nema otvorene pozicije na ovom paru. Bot ulazi samo kad engine pokaže prednost nakon troškova i Risk Manager odobri.</p>'}
  </div>
  <div class="card"><h2>Legenda</h2><div class="legend">
    <span><i style="background:var(--amber)"></i>VWAP (prosječna cijena dana po volumenu)</span><span><i class="dash" style="border-color:var(--amber)"></i>VWAP ±1σ / ±2σ pojasevi</span>
    <span><i style="background:var(--accent)"></i>EMA 20</span><span><i style="background:var(--violet)"></i>EMA 50</span><span><i style="background:var(--muted)"></i>EMA 200</span>
    <span><i style="background:var(--green)"></i>ciljevi TP1, TP2…</span><span><i style="background:var(--red)"></i>stop-loss</span><span>▲▼ ulaz · ● izlaz (zeleno dobit, crveno gubitak) · ■ signal bez ulaza</span>
  </div><p class="muted" style="margin-top:8px">Graf: TradingView Lightweight Charts™ (Apache 2.0).</p></div>`;
  return h;
}

// ---------------------------------------------------------------- actions
const ACT = {
  async start() { toast("Starting runtime…"); await post("/api/control/start"); route(); },
  async pause() { await post("/api/control/pause"); toast("Trading paused. Open positions remain managed."); route(); },
  async resume() { await post("/api/control/resume"); toast("Trading resumed."); route(); },
  async approve(el) { const r = await post(`/api/decisions/${el.dataset.id}/approve`); toast(r.ok ? "Executed." : `Not executed: ${r.reason || ""}`, r.ok ? "info" : "warning"); route(); },
  async reject(el) { await post(`/api/decisions/${el.dataset.id}/reject`); route(); },
  async "close-pos"(el) {
    if (!(await confirmDialog({ title: `Close ${el.dataset.sym}?`, text: "A reduce-only market order will close this position now." }))) return;
    const r = await post(`/api/positions/${el.dataset.id}/close`, { confirm: true }); toast(r.ok ? "Closed." : "Close not confirmed — check reconciliation.", r.ok ? "info" : "critical"); route();
  },
  async "close-all"() {
    const c = await confirmDialog({ title: "Close ALL positions?", text: "All open positions will be closed with reduce-only market orders.", typed: "CLOSE ALL" });
    if (!c) return; const r = await post("/api/positions/close_all", { confirm: "CLOSE ALL" }); toast(`Closed ${r.closed}.`); route();
  },
  async "toggle-engine"(el) { try { await post(`/api/engines/${el.dataset.name}/toggle`, { enabled: el.checked }); toast(`${el.dataset.name} ${el.checked ? "enabled" : "disabled"}`); S.cfg = null; } catch (e) { el.checked = !el.checked; throw e; } },
  async "reset-breaker"(el) {
    if (!(await confirmDialog({ title: `Reset ${el.dataset.name}?`, text: "Only reset after you understand why it tripped.", typed: "RESET" }))) return;
    await post("/api/risk/reset", { name: el.dataset.name, confirm: "RESET" }); toast("Breaker reset."); route();
  },
  async rollback(el) { if (!(await confirmDialog({ title: `Restore version ${el.dataset.id}?`, text: "Creates a new version with those settings (the venue is not changed).", danger: false }))) return; await post("/api/config/rollback", { id: Number(el.dataset.id) }); S.cfg = null; toast("Restored."); route(); },
  async "paper-reset"() {
    const c = await confirmDialog({ title: "Reset paper account?", text: "Resets the simulated wallet. Trade history is kept.", typed: "RESET PAPER", extra: `<div class="field"><label>Starting equity (USDT)</label><input data-k="equity" type="number" value="10000"></div>` });
    if (!c) return; await post("/api/paper/reset", { confirm: "RESET PAPER", equity: Number(c.vals.equity) }); toast("Paper account reset."); route();
  },
  async "key-test"(el) { toast("Testing…"); const r = await post("/api/keys/test", { env: el.dataset.env }); toast(r.ok ? `Connected. Wallet ${fmt(r.wallet)} USDT, one-way mode OK.` : `Problem: ${r.problem}`, r.ok ? "info" : "critical", 9000); },
  async "key-del"(el) { if (!(await confirmDialog({ title: `Delete ${el.dataset.env} keys?`, text: "The encrypted key and secret are removed." }))) return; await api(`/api/keys/${el.dataset.env}`, { method: "DELETE" }); route(); },
  async "enable-live"() {
    const c = await confirmDialog({ title: "Enable LIVE trading", text: "<b>Real funds will be at risk.</b> Leveraged futures can lose more than expected. There is no guarantee of profit.", typed: "ENABLE LIVE TRADING",
      extra: `<div class="field"><label>Mode</label><select data-k="mode"><option>SEMI_AUTOMATIC</option><option>AUTOMATIC</option></select></div>` });
    if (!c) return; await post("/api/live/enable", { confirm: "ENABLE LIVE TRADING", mode: c.vals.mode }); toast("LIVE enabled.", "critical"); route();
  },
  ovl(el) { const ov = overlays(); ov[el.dataset.k] = !ov[el.dataset.k]; store.set("qt_ovl", JSON.stringify(ov), true); el.classList.toggle("on", ov[el.dataset.k]); drawChart(false); },
  async "alerts-read"() { await post("/api/alerts/read"); route(); },
  theme(el) { const v = el.dataset.v; if (v) document.documentElement.dataset.theme = v; else delete document.documentElement.dataset.theme; store.set("qt_theme", v, true); },
};
document.addEventListener("click", async (e) => {
  const go = e.target.closest("[data-go]");
  if (go) { location.hash = go.dataset.go; return; }
  const el = e.target.closest("[data-act]");
  if (!el || el.tagName === "INPUT") return;
  e.preventDefault();
  try { el.disabled = true; await ACT[el.dataset.act](el); } catch (err) { toast(err.message, "critical", 8000); } finally { el.disabled = false; }
});
document.addEventListener("change", async (e) => {
  const el = e.target.closest("input[data-act]");
  if (!el) return;
  try { await ACT[el.dataset.act](el); } catch (err) { toast(err.message, "critical", 8000); }
});

$("#estopBtn").onclick = async () => {
  const c = await confirmDialog({ title: "EMERGENCY STOP", okLabel: "STOP NOW",
    text: "Blocks all new trades immediately and cancels pending approvals. Open positions keep their exchange stops unless you choose to flatten.",
    extra: `<label class="row" style="margin-top:8px"><input type="checkbox" data-k="flatten" style="width:auto;min-height:0"> Also close all positions now (market, reduce-only)</label>` });
  if (!c) return;
  try { const r = await post("/api/control/emergency_stop", { confirm: true, flatten: !!c.vals.flatten }); toast(`Emergency stop active${r.flattened ? `, ${r.flattened} closed` : ""}.`, "critical", 10000); route(); }
  catch (err) { toast(err.message, "critical"); }
};

// ---------------------------------------------------------------- routing
async function route() {
  const h = location.hash.replace(/^#\/?/, "") || "overview";
  const [r, ...rest] = h.split("/");
  S.route = PAGES[r] ? r : "overview";
  S.param = rest.join("/") || null;
  markNav();
  const page = PAGES[S.route];
  if (S.chartTimer && (S.route !== "chart" || view.dataset.route !== h)) { clearInterval(S.chartTimer); S.chartTimer = null; }
  if (S.chart && S.route !== "chart") { try { S.chart.remove(); } catch {} S.chart = null; }
  if (S.route === "chart" && view.dataset.route === h) return;  // the chart refreshes itself
  const scroll = window.scrollY;
  const same = view.dataset.route === h;
  if (!same) view.innerHTML = '<div class="loading">Loading…</div>';
  try {
    const html = await page(S.param);
    if ((location.hash.replace(/^#\/?/, "") || "overview") !== h) return;
    view.innerHTML = html;
    view.dataset.route = h;
    if (page.after) await page.after(S.param);
    if (same) window.scrollTo(0, scroll);
  } catch (err) {
    if (S.token) view.innerHTML = `<div class="card"><b>Could not load:</b> ${esc(err.message)}</div>`;
  }
}
window.addEventListener("hashchange", () => { view.dataset.route = ""; route(); });
const AUTO = new Set(["overview", "trades", "markets", "regime"]);  // the chart page refreshes itself
function autoRefresh() {
  clearInterval(S.timer);
  S.timer = setInterval(() => {
    if (document.hidden || !AUTO.has(S.route) || !$("#modal").classList.contains("hidden")) return;
    if (document.activeElement && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    route();
  }, 8000);
}

// ---------------------------------------------------------------- websocket
function connectWS(delay = 1000) {
  if (!S.token) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(S.token)}`);
  S.ws = ws;
  ws.onopen = () => { S.wsOk = true; $("#connDot").className = "dot on"; delay = 1000; };
  ws.onclose = () => { S.wsOk = false; $("#connDot").className = "dot off"; setTimeout(() => connectWS(Math.min(delay * 2, 30000)), delay); };
  ws.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "status") { S.status = m.data; updateBadges(m.data); }
    if (m.type === "alert") toast(`${m.data.kind}: ${m.data.message}`, m.data.severity, m.data.severity === "critical" ? 12000 : 6000);
  };
}
function updateBadges(st) {
  const b = $("#modeBadge");
  b.textContent = `${st.mode === "SEMI_AUTOMATIC" ? "SEMI" : st.mode === "AUTOMATIC" ? "AUTO" : "PAPER"} · ${st.venue}`;
  b.className = `badge ${st.venue === "LIVE" ? "live" : Object.keys(st.breakers || {}).length ? "bad" : st.paused ? "amber" : "ok"}`;
}

// ---------------------------------------------------------------- auth
async function renderAuth() {
  $("#sidenav").style.display = "none"; $("#bottomnav").style.display = "none";
  const st = await api("/api/auth/state").catch(() => ({ has_user: true }));
  view.innerHTML = `<div class="auth card"><h1>${st.has_user ? "Sign in" : "Create administrator"}</h1>
    ${st.has_user ? "" : '<p class="muted">First start: choose the administrator account. Password ≥ 10 characters.</p>'}
    <form id="authForm"><div class="field"><label>Username</label><input name="username" autocomplete="username" required></div>
    <div class="field" style="margin-top:10px"><label>Password</label><input name="password" type="password" autocomplete="${st.has_user ? "current-password" : "new-password"}" required minlength="10"></div>
    <label class="row" style="margin-top:10px"><input type="checkbox" name="remember" style="width:auto;min-height:0"> Keep me signed in on this device</label>
    <div class="row" style="margin-top:14px"><span class="spacer"></span><button class="btn primary" type="submit">${st.has_user ? "Sign in" : "Create"}</button></div></form></div>`;
  $("#authForm").onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target).entries());
    try {
      if (!st.has_user) await post("/api/auth/setup", { username: d.username, password: d.password });
      const r = await post("/api/auth/login", { username: d.username, password: d.password });
      S.token = r.token; store.set("qt_token", r.token, !!d.remember); boot();
    } catch (err) { toast(err.message, "critical"); }
  };
}

function boot() {
  $("#sidenav").style.display = ""; $("#bottomnav").style.display = "";
  connectWS(); route(); autoRefresh();
  api("/api/status").then(updateBadges).catch(() => {});
}

(function init() {
  const th = store.get("qt_theme"); if (th) document.documentElement.dataset.theme = th;
  buildNav();
  const m = location.hash.match(/token=([A-Za-z0-9_\-]+)/);
  if (m) { S.token = m[1]; store.set("qt_token", m[1], false); history.replaceState(null, "", "#/overview"); }
  else S.token = store.get("qt_token");
  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("/sw.js").catch(() => {});
  if (!S.token) renderAuth(); else boot();
})();
})();
