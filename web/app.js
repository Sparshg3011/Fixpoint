/* Fixpoint UI — a pure renderer over the API. No frameworks, no build step,
   and no hardcoded data: every model, number, and row comes from endpoints
   that discover artifacts on disk. Replay and live views share one renderer,
   which is what makes the demo honest — it is a real recorded run played
   back, never a script. */

"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (path) => {
  const r = await fetch(`/api${path}`);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
};
const pct = (x) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const usd = (x) => (x == null ? "—" : x === 0 ? "$0.00" : `$${x.toFixed(2)}`);
const secs = (s) => (s == null ? "—" : s >= 3600 ? `${(s / 3600).toFixed(1)}h`
  : s >= 60 ? `${Math.round(s / 60)}m` : `${Math.round(s)}s`);
const shortModel = (m) => m.replace(/^fixpoint-singleshot-/, "");

/* ---------- the cast: presentation for the diary's stage vocabulary ------- */
const GLYPHS = {
  retrieval:  '<circle cx="17" cy="17" r="6.5"/><path d="M22 22l5 5"/>',
  sandbox:    '<rect x="9" y="11" width="16" height="12" rx="2"/><path d="M9 15h16"/>',
  reproducer: '<path d="M14 9h6M15 9v6l-5 8a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-8V9"/>',
  developer:  '<path d="M13 12l-5 5 5 5M21 12l5 5-5 5"/>',
  tester:     '<path d="M17 8l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9v-6z"/><path d="M13.5 17l2.5 2.5 4.5-4.5"/>',
  loop:       '<path d="M9 20v-6M14 22V12M19 20v-4M24 22V10"/>',
};
const CAST = {
  retrieval:  { name: "Scout",      sub: "find the files" },
  reproducer: { name: "Reproducer", sub: "capture the bug" },
  developer:  { name: "Developer",  sub: "write the patch" },
  tester:     { name: "Tester",     sub: "prove it" },
  loop:       { name: "Conductor",  sub: "decide & retry" },
  sandbox:    { name: "Sandbox",    sub: "isolated env" },
};
const avatarSVG = (stage) =>
  `<svg viewBox="0 0 34 34" aria-hidden="true">
     <circle cx="17" cy="17" r="15.5" class="agent-node" stroke-width="1.5"/>
     <g class="agent-glyph">${GLYPHS[stage] || GLYPHS.loop}</g>
   </svg>`;

/* ---------- diff & output rendering --------------------------------------- */
function renderDiff(diff) {
  if (!diff || !diff.trim()) return `<div class="empty-pane">no patch produced</div>`;
  const cls = (l) =>
    l.startsWith("+++") || l.startsWith("---") || l.startsWith("diff --git") ? "file"
    : l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
  return `<div class="code"><pre>${diff.split("\n")
    .map((l) => `<span class="ln ${cls(l)}">${esc(l) || " "}</span>`).join("")}</pre></div>`;
}
const renderOutput = (text) => !text || !text.trim()
  ? `<div class="empty-pane">no output yet</div>`
  : `<div class="code"><pre>${text.split("\n")
      .map((l) => `<span class="ln">${esc(l) || " "}</span>`).join("")}</pre></div>`;

/* ---------- results view --------------------------------------------------- */
async function showResults() {
  const { models } = await api("/results");
  if (!models.length) {
    view.innerHTML = `<div class="empty"><h3>No benchmark results yet</h3>
      <p class="mono">python scripts/run_singleshot.py --n 25</p></div>`;
    return;
  }
  const head = models.find((m) => m.resolve_rate != null) || models[0];
  const chip = $("#headline-chip");
  chip.hidden = false;
  chip.textContent = `${pct(head.resolve_rate)} of ${head.n} · ${usd(head.cost_usd)}`;

  const totalCost = models.reduce((a, m) => a + (m.cost_usd || 0), 0);
  const totalGraded = models.reduce((a, m) => a + (m.graded || 0), 0);

  const funnel = (m) => {
    const f = (x) => (m.n ? Math.round((x / m.n) * 100) : 0);
    return `<div class="funnel" title="localized ${m.localized} · applied ${m.applied} · resolved ${m.resolved} of ${m.n}">
      <span class="f-loc" style="width:${f(m.localized)}%"></span>
      <span class="f-app" style="width:${f(m.applied)}%"></span>
      <span class="f-res" style="width:${f(m.resolved)}%"></span></div>`;
  };

  view.innerHTML = `
    <h1>Benchmark results</h1>
    <p class="muted" style="margin:0 0 22px">Single-shot resolve rates on SWE-bench-Lite,
      graded by the unmodified official Docker harness. Every row is read live from
      artifacts on disk — nothing on this page is hardcoded.</p>
    <div class="hero">
      <div class="stat stat-hero">
        <span class="k">best resolve rate — ${esc(shortModel(head.model))}</span>
        <span class="v">${pct(head.resolve_rate)}</span>
        <span class="s">${head.resolved}/${head.n} instances · official harness verdicts</span>
      </div>
      <div class="stat"><span class="k">models benchmarked</span>
        <span class="v">${models.length}</span><span class="s">same scaffold, same instances</span></div>
      <div class="stat"><span class="k">instances graded</span>
        <span class="v">${totalGraded}</span><span class="s">in isolated Docker sandboxes</span></div>
      <div class="stat"><span class="k">total API cost</span>
        <span class="v">${usd(totalCost)}</span>
        <span class="s">${models.filter((m) => !m.cost_usd).length} of ${models.length} models ran free</span></div>
    </div>

    <div class="section">
      <div class="section-head"><h2>Models</h2>
        <span class="chip chip-dim">click a row for per-instance detail</span></div>
      <div class="panel"><table>
        <thead><tr><th>model</th><th class="num">scope</th><th>pipeline funnel</th>
          <th class="num">apply</th><th class="num">resolved</th><th class="num">cost</th><th class="num">gen time</th></tr></thead>
        <tbody id="model-rows">
          ${models.map((m) => `
            <tr data-dir="${esc(m.dir)}">
              <td class="mono">${esc(shortModel(m.model))}</td>
              <td class="num">n=${m.n}</td>
              <td>${funnel(m)}</td>
              <td class="num">${pct(m.apply_rate)}</td>
              <td class="num"><span class="chip ${m.resolve_rate ? "chip-green" : "chip-dim"}">${pct(m.resolve_rate)}</span></td>
              <td class="num">${usd(m.cost_usd)}</td>
              <td class="num">${secs(m.wall_s)}</td>
            </tr>`).join("")}
        </tbody>
      </table></div>
      <div class="legend">
        <span><i style="background:rgba(88,166,255,.4)"></i>gold file retrieved</span>
        <span><i style="background:rgba(210,153,34,.55)"></i>patch applied</span>
        <span><i style="background:var(--green)"></i>resolved</span>
      </div>
    </div>
    <div class="section" id="instances-section" hidden>
      <div class="section-head"><h2>Instances — <span id="inst-model" class="mono"></span></h2></div>
      <div class="toolbar" id="inst-toolbar"></div>
      <div class="panel"><table>
        <thead><tr><th>instance</th><th>verdict</th><th class="num">gold rank</th><th>failure</th></tr></thead>
        <tbody id="inst-rows"></tbody></table></div>
    </div>
    <aside class="drawer" id="drawer" aria-label="Instance detail"></aside>`;

  $("#model-rows").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-dir]");
    if (!tr) return;
    $("#model-rows").querySelectorAll("tr").forEach((r) => r.classList.remove("selected"));
    tr.classList.add("selected");
    loadInstances(tr.dataset.dir);
  });
  const first = $("#model-rows tr");
  if (first) { first.classList.add("selected"); loadInstances(first.dataset.dir); }
}

let instState = { dir: null, rows: [], filter: "all", q: "" };
async function loadInstances(dir) {
  const data = await api(`/results/${encodeURIComponent(dir)}/instances`);
  instState = { dir, rows: data.instances, filter: "all", q: "" };
  $("#instances-section").hidden = false;
  $("#inst-model").textContent = shortModel(data.model);
  const counts = data.instances.reduce((a, r) => ((a[r.verdict] = (a[r.verdict] || 0) + 1), a), {});
  $("#inst-toolbar").innerHTML =
    ["all", "resolved", "unresolved", "empty", "ungraded"]
      .filter((f) => f === "all" || counts[f])
      .map((f) => `<button class="fchip ${f === "all" ? "on" : ""}" data-f="${f}">
         ${f}${f === "all" ? ` · ${data.instances.length}` : ` · ${counts[f]}`}</button>`).join("") +
    `<input class="search" id="inst-q" placeholder="filter instances…" aria-label="Filter instances">`;
  $("#inst-toolbar").addEventListener("click", (e) => {
    const b = e.target.closest(".fchip"); if (!b) return;
    $("#inst-toolbar").querySelectorAll(".fchip").forEach((x) => x.classList.remove("on"));
    b.classList.add("on"); instState.filter = b.dataset.f; paintInstances();
  });
  $("#inst-q").addEventListener("input", (e) => { instState.q = e.target.value; paintInstances(); });
  paintInstances();
}

function paintInstances() {
  const chipFor = { resolved: "chip-green", unresolved: "chip-red", empty: "chip-dim", ungraded: "chip-amber" };
  const rows = instState.rows.filter((r) =>
    (instState.filter === "all" || r.verdict === instState.filter) &&
    (!instState.q || r.instance_id.includes(instState.q)));
  $("#inst-rows").innerHTML = rows.map((r) => `
    <tr data-iid="${esc(r.instance_id)}">
      <td class="mono">${esc(r.instance_id)}</td>
      <td><span class="chip ${chipFor[r.verdict]}">${r.verdict}</span>
          ${r.guided ? '<span class="chip chip-accent" title="model-guided retrieval fired">guided</span>' : ""}</td>
      <td class="num">${r.gold_rank ?? "miss"}</td>
      <td class="muted" style="font-size:12px">${esc(r.error || "")}</td>
    </tr>`).join("") ||
    `<tr><td colspan="4" class="muted">no instances match</td></tr>`;
  $("#inst-rows").onclick = (e) => {
    const tr = e.target.closest("tr[data-iid]");
    if (tr) openDrawer(instState.dir, tr.dataset.iid);
  };
}

async function openDrawer(dir, iid) {
  const d = await api(`/results/${encodeURIComponent(dir)}/instances/${encodeURIComponent(iid)}`);
  const el = $("#drawer");
  el.innerHTML = `
    <div class="drawer-head">
      <span class="chip ${d.resolved ? "chip-green" : d.graded ? "chip-red" : "chip-dim"}">
        ${d.resolved ? "resolved" : d.graded ? "unresolved" : "not graded"}</span>
      <span class="mono">${esc(d.instance_id)}</span>
      <button class="drawer-close" aria-label="Close">×</button>
    </div>
    <div class="drawer-body">
      <dl class="kv">
        <dt>gold file rank</dt><dd>${d.gold_rank ?? "not in top-5 (BM25 miss)"}</dd>
        <dt>files shown</dt><dd>${d.top_files.map(esc).join("<br>")}</dd>
        <dt>gold files</dt><dd>${d.gold_files.map(esc).join("<br>") || "—"}</dd>
        <dt>tokens in / out</dt><dd>${d.tokens.in ?? "—"} / ${d.tokens.out ?? "—"}</dd>
        <dt>cost</dt><dd>${usd(d.cost_usd)}</dd>
        ${d.error ? `<dt>failure</dt><dd style="color:var(--red)">${esc(d.error)}</dd>` : ""}
      </dl>
      <h2 style="margin-bottom:8px">Generated patch</h2>
      ${renderDiff(d.diff)}
    </div>`;
  el.classList.add("open");
  $(".drawer-close", el).onclick = () => el.classList.remove("open");
  document.addEventListener("keydown", function escClose(e) {
    if (e.key === "Escape") { el.classList.remove("open"); document.removeEventListener("keydown", escClose); }
  });
}

/* ---------- runs view ------------------------------------------------------ */
async function showRuns() {
  const { runs } = await api("/runs");
  if (!runs.length) {
    view.innerHTML = `<div class="empty">
      <h3>No runs recorded yet</h3>
      <p>Run diaries appear here the moment the replan loop writes one — live runs stream in real time.</p>
      <p class="mono">python scripts/run_loop.py --n 2 --workers 1</p></div>`;
    return;
  }
  view.innerHTML = `<h1>Agent runs</h1>
    <p class="muted" style="margin:0 0 22px">Each card is a recorded event stream from a real run —
      replayed at its original pacing through the same renderer that shows live runs.</p>
    <div class="runlist">${runs.map((r) => `
      <div class="runcard" data-rid="${esc(r.run_id)}">
        <span class="dot ${r.live ? "dot-live" : r.green ? "dot-green" : "dot-red"}"></span>
        <span class="mono">${esc(r.instance_id)}</span>
        ${r.live ? '<span class="chip chip-accent">LIVE</span>'
                 : `<span class="chip ${r.green ? "chip-green" : "chip-red"}">${r.green ? "loop green" : "loop red"}</span>`}
        <span class="meta"><span>${r.events} events</span><span>${secs(r.duration_s)}</span>
          <span>${new Date(r.started * 1000).toLocaleString()}</span></span>
      </div>`).join("")}</div>`;
  $(".runlist").addEventListener("click", (e) => {
    const c = e.target.closest(".runcard");
    if (c) location.hash = `#/runs/${encodeURIComponent(c.dataset.rid)}`;
  });
}

/* ---------- run replay/live view ------------------------------------------ */
let player = null;
async function showRun(runId) {
  const { runs } = await api("/runs");
  const meta = runs.find((r) => r.run_id === runId);
  const railStages = ["retrieval", "reproducer", "developer", "tester", "loop"];
  view.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px">
      <a href="#/runs" class="muted">← runs</a>
      <h1 style="margin:0" class="mono">${esc(meta ? meta.instance_id : runId)}</h1>
      <span id="run-state" class="chip chip-dim"></span>
    </div>
    <div class="runview">
      <div class="rail panel" id="rail">
        ${railStages.map((s, i) => `
          ${i ? '<div class="rail-link"></div>' : ""}
          <div class="agent" data-stage="${s}">${avatarSVG(s)}
            <div><div class="an">${CAST[s].name}</div><div class="as">${CAST[s].sub}</div></div>
          </div>`).join("")}
      </div>
      <div class="panel"><div class="pane-head">Event feed</div><div class="feed" id="feed"></div></div>
      <div class="panes">
        <div class="panel pane"><div class="pane-head">Patch (latest attempt)</div><div id="pane-diff">
          <div class="empty-pane">waiting for the developer…</div></div></div>
        <div class="panel pane"><div class="pane-head">Sandbox output</div><div id="pane-out">
          <div class="empty-pane">waiting for the tester…</div></div></div>
      </div>
    </div>
    <div class="replaybar" id="replaybar" hidden>
      <button class="playbtn" id="play" aria-label="Play or pause">⏸</button>
      <div class="speed" id="speed">${[1, 8, 32].map((s) =>
        `<button data-s="${s}" class="${s === 8 ? "on" : ""}">×${s}</button>`).join("")}</div>
      <div class="track"><i id="trackfill"></i></div>
      <div class="rtime" id="rtime"></div>
    </div>`;

  if (player) player.stop();
  const live = meta && meta.live;
  $("#run-state").textContent = live ? "live" : "replay";
  $("#run-state").className = `chip ${live ? "chip-accent" : "chip-dim"}`;
  if (live) {
    const es = new EventSource(`/api/runs/${encodeURIComponent(runId)}/stream`);
    es.onmessage = (m) => applyEvent(JSON.parse(m.data));
    es.onerror = () => es.close();
    player = { stop: () => es.close() };
  } else {
    const { events } = await api(`/runs/${encodeURIComponent(runId)}`);
    player = new Replayer(events);
    player.start();
  }
}

function applyEvent(e) {
  const agent = document.querySelector(`.agent[data-stage="${e.stage}"]`);
  if (agent) {
    agent.classList.remove("working", "done", "failed");
    agent.classList.add(e.event === "started" || e.event === "progress" ? "working"
      : e.event === "succeeded" ? "done" : "failed");
  }
  document.querySelectorAll(".agent.working").forEach((a) => {
    if (a !== agent) a.classList.remove("working");
  });
  const feed = $("#feed");
  const cls = e.event === "succeeded" ? "ok" : e.event === "failed" ? "bad" : "go";
  const msg = Object.entries(e.detail || {})
    .filter(([k]) => !["diff", "output", "script"].includes(k))
    .map(([k, v]) => `${k}=${String(v).slice(0, 80)}`).join("  ");
  const item = document.createElement("div");
  item.className = `evt ${cls}`;
  item.innerHTML = `<span class="t">${e.attempt ? "#" + e.attempt : ""}</span>
    <span class="badge">${esc(CAST[e.stage]?.name || e.stage)}</span>
    <span class="msg">${esc(e.event)}${msg ? " · " + esc(msg) : ""}</span>`;
  feed.appendChild(item);
  feed.scrollTop = feed.scrollHeight;
  if (e.detail?.diff) $("#pane-diff").innerHTML = renderDiff(e.detail.diff);
  if (e.detail?.output) $("#pane-out").innerHTML = renderOutput(e.detail.output);
}

class Replayer {
  /* Plays events on their original clock (gaps capped at 6s so Docker waits
     stay watchable), with speed control. Same applyEvent as live mode. */
  constructor(events) {
    this.events = events; this.i = 0; this.speed = 8; this.playing = true;
    this.t0 = events[0]?.ts || 0;
    this.total = events.length ? events[events.length - 1].ts - this.t0 : 0;
    this.clock = 0; this.timer = null;
    $("#replaybar").hidden = false;
    $("#play").onclick = () => { this.playing = !this.playing;
      $("#play").textContent = this.playing ? "⏸" : "▶"; this.tick(); };
    $("#speed").onclick = (e) => { const b = e.target.closest("button"); if (!b) return;
      $("#speed").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on"); this.speed = +b.dataset.s; };
  }
  start() { this.tick(); }
  stop() { clearTimeout(this.timer); }
  tick() {
    if (!this.playing || this.i >= this.events.length) return;
    const e = this.events[this.i];
    const gap = this.i === 0 ? 0 : Math.min(e.ts - this.events[this.i - 1].ts, 6);
    this.timer = setTimeout(() => {
      applyEvent(e); this.clock = e.ts - this.t0; this.i += 1;
      $("#trackfill").style.width = `${this.total ? (this.clock / this.total) * 100 : 100}%`;
      $("#rtime").textContent = `${secs(this.clock)} / ${secs(this.total)} real`;
      this.tick();
    }, (gap * 1000) / this.speed);
  }
}

/* ---------- router --------------------------------------------------------- */
async function route() {
  const hash = location.hash || "#/results";
  document.querySelectorAll(".tabs a").forEach((a) =>
    a.classList.toggle("active", hash.startsWith(`#/${a.dataset.tab}`)));
  if (player) { player.stop(); player = null; }
  try {
    const runMatch = hash.match(/^#\/runs\/(.+)$/);
    if (runMatch) await showRun(decodeURIComponent(runMatch[1]));
    else if (hash.startsWith("#/runs")) await showRuns();
    else await showResults();
  } catch (err) {
    view.innerHTML = `<div class="err">failed to load: ${esc(err.message)}</div>`;
  }
  view.focus({ preventScroll: true });
}
addEventListener("hashchange", route);
route();
