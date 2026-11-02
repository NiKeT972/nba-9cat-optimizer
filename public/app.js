/* NBA 9-Cat Optimizer: web UI. All maths lives in core.js (NineCat). */
(function () {
  "use strict";
  const NC = window.NineCat;
  const STORE = "ninecat:v1";
  const ROSTERS = [
    { key: "my", title: "My team", file: "My Team" },
    { key: "opp", title: "This week's opponent", file: "Opponent" },
    { key: "other", title: "Everyone else rostered in my league", file: "Other rostered players" },
  ];

  let D, cfg, CATS, L, players, pmap;
  const state = {
    rosters: { my: [], opp: [], other: [] }, punt: [], includeToday: true, tab: "rankings",
    rank: { q: "", team: "", fa: false, active: true, view: "z", limit: 100, sort: null },
    waiver: { mode: "season", active: true }, trade: { give: [], get: [], partner: [] },
    sim: { n: null, inactive: true, wtd: true, result: null, key: null },
  };

  // ---------------------------------------------------------------- utilities
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style") el.setAttribute("style", v);
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "html") el.innerHTML = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid.nodeType ? kid : String(kid));
    return el;
  }
  const f1 = (v) => (v == null || isNaN(v) ? "–" : v.toFixed(1));
  const f3 = (v) => (v == null || isNaN(v) ? "–" : v.toFixed(3));
  const sg = (v, d = 2) => (v == null || isNaN(v) ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d));
  const pct = (v) => `${Math.round(v * 100)}%`;
  const isPct = (c) => c === "FG_PCT" || c === "FT_PCT";
  function zStyle(v, cap = 2.5) {
    if (!v || isNaN(v)) return null;
    const a = (Math.min(Math.abs(v) / cap, 1) * 0.35).toFixed(3);
    return `background: rgba(var(${v > 0 ? "--good" : "--bad"}), ${a})`;
  }
  const todayET = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
  const today = () => { const t = todayET(); return D.as_of && t < D.as_of ? D.as_of : t; };
  const niceDate = (d) => new Date(d + "T12:00:00Z").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });

  function save() {
    try { localStorage.setItem(STORE, JSON.stringify({ rosters: state.rosters, punt: state.punt, includeToday: state.includeToday, tab: state.tab })); } catch (e) { /* private mode */ }
  }
  function load() {
    try {
      const s = JSON.parse(localStorage.getItem(STORE) || "{}");
      if (s.rosters) for (const k of ["my", "opp", "other"]) state.rosters[k] = (s.rosters[k] || []).filter((id) => pmap.has(id));
      if (Array.isArray(s.punt)) state.punt = s.punt.filter((c) => CATS.includes(c));
      if (typeof s.includeToday === "boolean") state.includeToday = s.includeToday;
      if (s.tab) state.tab = s.tab;
    } catch (e) { /* ignore */ }
    const hash = location.hash.slice(1);
    if (hash) state.tab = hash;
  }

  let valuedCache = { key: null, rows: null };
  function valued() {
    const key = state.punt.join(",");
    if (valuedCache.key !== key) {
      const rows = NC.valuePlayers(players, cfg, state.punt);
      valuedCache = { key, rows, map: NC.byId(rows) };
    }
    return valuedCache;
  }

  // ---------------------------------------------------------------- charts (Plotly)
  const charts = [];
  const isDark = () => document.documentElement.dataset.theme === "dark" ||
    (document.documentElement.dataset.theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  const COLORS = () => isDark()
    ? { me: "#3987e5", opp: "#d95926", good: "#3987e5", bad: "#e66767", neutral: "#898781", ink: "#c3c2b7", muted: "#a3a29b", grid: "#2c2c2a", axis: "#383835" }
    : { me: "#2a78d6", opp: "#eb6834", good: "#2a78d6", bad: "#e34948", neutral: "#898781", ink: "#52514e", muted: "#6f6d68", grid: "#e1e0d9", axis: "#c3c2b7" };
  function baseLayout(title, height) {
    const c = COLORS();
    const ax = { gridcolor: c.grid, linecolor: c.axis, zerolinecolor: c.axis, tickfont: { color: c.muted } };
    return { title: { text: title, x: 0, font: { size: 15, color: c.ink } }, height, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 13, color: c.ink }, margin: { l: 8, r: 8, t: 40, b: 8 },
      xaxis: { ...ax, automargin: true }, yaxis: { ...ax, automargin: true }, showlegend: false, bargap: 0.35 };
  }
  function plot(el, fn) {
    const draw = () => { if (!window.Plotly || !el.isConnected) return; const { data, layout } = fn(); Plotly.react(el, data, layout, { displayModeBar: false, responsive: true }); };
    charts.push(draw);
    if (window.Plotly) draw(); else window.addEventListener("load", draw, { once: true });
    return el;
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => charts.forEach((d) => d()));

  /** Bars per category, coloured by sign; labels sit across the zero line from their bar. */
  function divergingBars(labels, values, title, { digits = 2, suffix = "" } = {}) {
    return plot(h("div", { class: "chart" }), () => {
      const c = COLORS(); const lab = [...labels].reverse(); const val = [...values].reverse();
      const span = Math.max(...val.map(Math.abs), 1e-9) * 1.1;
      const layout = baseLayout(title, Math.max(260, 32 * lab.length + 80));
      layout.xaxis.range = [-span, span]; layout.yaxis.showgrid = false;
      layout.shapes = [{ type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper", line: { color: c.neutral, width: 1 } }];
      return { layout, data: [
        { type: "bar", orientation: "h", y: lab, x: val, marker: { color: val.map((v) => (v >= 0 ? c.good : c.bad)), cornerradius: 4 },
          hovertemplate: `%{y}: %{x:+.${digits}f}${suffix}<extra></extra>` },
        { type: "scatter", mode: "text", y: lab, x: val.map((v) => (v >= 0 ? -0.02 : 0.02) * span), hoverinfo: "skip",
          text: val.map((v) => sg(v, digits) + suffix), textposition: val.map((v) => (v >= 0 ? "middle left" : "middle right")), textfont: { color: c.ink } },
      ] };
    });
  }

  function winBars(rows) {
    return plot(h("div", { class: "chart" }), () => {
      const c = COLORS(); const r = [...rows].reverse();
      const p = r.map((x) => x.pWin + cfg.tie_value * x.pTie);
      const layout = baseLayout("Chance to win each category", 380);
      layout.xaxis = { ...layout.xaxis, range: [0, 1], tickformat: ".0%", dtick: 0.25 }; layout.yaxis.showgrid = false;
      layout.shapes = [
        { type: "rect", x0: cfg.close_band[0], x1: cfg.close_band[1], y0: 0, y1: 1, yref: "paper", fillcolor: "rgba(137,135,129,0.12)", line: { width: 0 }, layer: "below" },
        { type: "line", x0: 0.5, x1: 0.5, y0: 0, y1: 1, yref: "paper", line: { color: c.neutral, width: 1 } }];
      return { layout, data: [
        { type: "bar", orientation: "h", y: r.map((x) => L[x.cat]), x: p.map((v) => v - 0.5), base: 0.5,
          marker: { color: p.map((v) => (v >= 0.5 ? c.me : c.opp)), cornerradius: 4 },
          customdata: r.map((x) => [x.my, x.opp]), hovertemplate: "%{y}: me %{customdata[0]:.3~f} vs opp %{customdata[1]:.3~f}<extra></extra>" },
        { type: "scatter", mode: "text", y: r.map((x) => L[x.cat]), x: p.map((v) => (v >= 0.5 ? 0.485 : 0.515)), hoverinfo: "skip",
          text: p.map(pct), textposition: p.map((v) => (v >= 0.5 ? "middle left" : "middle right")), textfont: { color: c.ink } },
      ] };
    });
  }

  function radar(series, title) {
    return plot(h("div", { class: "chart" }), () => {
      const c = COLORS(); const theta = [...CATS.map((k) => L[k]), L[CATS[0]]];
      const all = series.flatMap((s) => CATS.map((k) => s.values[k]));
      const lo = Math.min(0, ...all), hi = Math.max(0, ...all), pad = Math.max(1, 0.1 * (hi - lo));
      const layout = baseLayout(title, 440);
      layout.showlegend = true; layout.legend = { orientation: "h", x: 0.5, xanchor: "center", y: -0.08 };
      layout.margin = { l: 30, r: 30, t: 48, b: 48 };
      layout.polar = { bgcolor: "rgba(0,0,0,0)", radialaxis: { range: [lo - pad, hi + pad], angle: 90, nticks: 4, tickfont: { size: 10, color: c.muted }, gridcolor: c.grid, linecolor: c.axis },
        angularaxis: { gridcolor: c.grid, linecolor: c.axis, tickfont: { color: c.ink } } };
      const data = series.map((s) => {
        const col = s.who === "me" ? c.me : c.opp; const r = [...CATS.map((k) => s.values[k]), s.values[CATS[0]]];
        return { type: "scatterpolar", r, theta, name: s.name, mode: "lines+markers", line: { color: col, width: 2 }, marker: { size: 8, color: col },
          fill: "toself", fillcolor: col + "1f", hovertemplate: `%{theta}: %{r:+.2f}<extra>${s.name}</extra>` };
      });
      if (lo < 0 && hi > 0) data.push({ type: "scatterpolar", r: theta.map(() => 0), theta, name: "League-average team", mode: "lines", line: { color: c.neutral, width: 1, dash: "dot" }, hoverinfo: "skip" });
      return { data, layout };
    });
  }

  function distBars(dist) {
    return plot(h("div", { class: "chart" }), () => {
      const c = COLORS(); const half = CATS.length / 2;
      const layout = baseLayout("Final score distribution", 300);
      layout.xaxis = { ...layout.xaxis, type: "category", title: { text: "Categories I win", font: { size: 12 } } };
      layout.yaxis = { ...layout.yaxis, tickformat: ".0%" };
      return { layout, data: [{ type: "bar", x: dist.map(([k]) => String(k)), y: dist.map(([, p]) => p),
        marker: { color: dist.map(([k]) => (k > half ? c.me : k < half ? c.opp : c.neutral)), cornerradius: 4 },
        hovertemplate: "%{x} categories: %{y:.1%}<extra></extra>" }] };
    });
  }

  // ---------------------------------------------------------------- tables
  function table(columns, rows, { rowClass, sortState } = {}) {
    const thead = h("tr", {}, columns.map((col) => h("th", {
      class: col.sort ? "sortable" : null, title: col.title || null,
      onclick: col.sort && sortState ? () => { sortState.set(col.key); } : null,
    }, col.label)));
    const body = rows.map((r) => h("tr", { class: rowClass ? rowClass(r) : null }, columns.map((col) => {
      const v = col.get(r);
      return h("td", { class: col.cls || null, style: col.style ? col.style(r) : null }, v);
    })));
    return h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, thead), h("tbody", {}, body)));
  }
  const zCols = (getZ) => CATS.map((c) => ({ label: L[c], cls: "num", get: (r) => sg(getZ(r)[c]), style: (r) => zStyle(getZ(r)[c]), sort: true, key: "z:" + c }));

  // ---------------------------------------------------------------- player picker
  function picker(listKey, { pool, placeholder, onAdd }) {
    const wrap = h("div", { class: "picker" });
    const input = h("input", { class: "input", type: "search", placeholder: placeholder || "Search a player…", "data-picker": listKey, autocomplete: "off" });
    const box = h("div", { class: "suggest", hidden: true });
    let hits = [], hl = 0;
    const show = () => {
      const q = NC.norm(input.value);
      if (!q) { box.hidden = true; return; }
      const cand = pool().filter((p) => NC.norm(p.name).includes(q));
      cand.sort((a, b) => (NC.norm(b.name).startsWith(q) - NC.norm(a.name).startsWith(q)) || a.rank - b.rank);
      hits = cand.slice(0, 8); hl = 0;
      box.replaceChildren(...hits.map((p, i) => h("button", { type: "button", class: i === hl ? "hl" : null, onmousedown: (e) => { e.preventDefault(); pick(p); } },
        h("span", {}, p.name), h("span", { class: "dim" }, `${p.team} · #${p.rank}`))));
      box.hidden = !hits.length;
    };
    const pick = (p) => { input.value = ""; box.hidden = true; onAdd(p.id); };
    input.addEventListener("input", show);
    input.addEventListener("blur", () => setTimeout(() => (box.hidden = true), 100));
    input.addEventListener("keydown", (e) => {
      if (box.hidden) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); hl = (hl + (e.key === "ArrowDown" ? 1 : -1) + hits.length) % hits.length; [...box.children].forEach((b, i) => b.classList.toggle("hl", i === hl)); }
      if (e.key === "Enter" && hits[hl]) { e.preventDefault(); pick(hits[hl]); }
      if (e.key === "Escape") box.hidden = true;
    });
    wrap.append(input, box);
    return wrap;
  }
  function refocus(key) { const el = document.querySelector(`[data-picker="${key}"]`); if (el) el.focus(); }

  // ---------------------------------------------------------------- rendering shell
  function render() {
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === state.tab)));
    document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== "tab-" + state.tab));
    charts.length = 0;
    const el = document.getElementById("tab-" + state.tab);
    const fn = { rankings: renderRankings, team: renderTeam, waivers: renderWaivers, trade: renderTrade, matchup: renderMatchup, about: renderAbout }[state.tab];
    el.replaceChildren(...[].concat(fn ? fn() : renderRankings()).filter((x) => x !== null && x !== undefined && x !== false));
    charts.forEach((d) => d()); // charts can only draw once their container is in the page
  }
  function setTab(t) { state.tab = t; history.replaceState(null, "", "#" + t); save(); render(); window.scrollTo({ top: 0 }); }
  function changed() { save(); render(); }
  const needData = () => h("div", { class: "empty" }, "No player data in this build yet. Run ", h("code", {}, "python -m src.data.pipeline"),
    " then ", h("code", {}, "python -m app.static_site"), " and push.");
  const needRoster = () => h("div", { class: "empty" }, "Add your players on the ", h("a", { href: "#team", onclick: (e) => { e.preventDefault(); setTab("team"); } }, "My Team"), " tab first.");

  // ---------------------------------------------------------------- Rankings
  function renderRankings() {
    if (!players.length) return [h("h2", {}, "Player rankings"), needData()];
    const { rows } = valued(); const S = state.rank;
    const mine = new Set(state.rosters.my); const taken = new Set([...state.rosters.my, ...state.rosters.opp, ...state.rosters.other]);
    const teams = [...new Set(players.map((p) => p.team))].sort();
    const holder = h("div");
    const draw = () => {
      const q = NC.norm(S.q);
      let list = rows.filter((r) => (!q || NC.norm(r.name).includes(q)) && (!S.team || r.team === S.team) && (!S.fa || !taken.has(r.id)) && (!S.active || r.active));
      if (S.sort) {
        const [kind, k] = S.sort.split(":");
        list = [...list].sort((a, b) => (kind === "z" ? b.z[k] - a.z[k] : (kind === "s" ? statVal(b, k) - statVal(a, k) : 0)));
      }
      const shown = list.slice(0, S.limit);
      const sortState = { set: (key) => { S.sort = S.sort === key ? null : key; draw(); } };
      const base = [
        { label: "#", cls: "num dim", get: (r) => r.rank },
        { label: "Player", get: (r) => [r.name, mine.has(r.id) ? h("span", { class: "tag" }, "mine") : null] },
        { label: "Team", get: (r) => r.team },
        { label: "GP", cls: "num", get: (r) => r.games },
        { label: "MPG", cls: "num", get: (r) => f1(r.min) },
        { label: "Value", cls: "num strong", get: (r) => sg(r.total), style: (r) => zStyle(r.total / 3), sort: true, key: "v:" },
      ];
      const stats = [["PTS", "pts"], ["REB", "reb"], ["AST", "ast"], ["STL", "stl"], ["BLK", "blk"], ["3PM", "fg3m"], ["FG%", "fg_pct"], ["FT%", "ft_pct"], ["TO", "tov"]];
      const cols = S.view === "z" ? base.concat(zCols((r) => r.z))
        : base.concat(stats.map(([lab, k]) => ({ label: lab, cls: "num", get: (r) => (k.endsWith("pct") ? f3(statVal(r, k)) : f1(statVal(r, k))), sort: true, key: "s:" + k })));
      holder.replaceChildren(
        table(cols, shown, { rowClass: (r) => (mine.has(r.id) ? "mine" : null), sortState }),
        h("p", { class: "note" }, `${list.length} players${list.length > shown.length ? `, showing ${shown.length}` : ""}. Click a category header to sort by it. Punted categories still show but don't count toward Value.`),
        list.length > shown.length ? h("button", { class: "btn", onclick: () => { S.limit = 10000; draw(); } }, "Show all") : null);
    };
    const controls = h("div", { class: "controls" },
      h("input", { class: "input", type: "search", placeholder: "Search player…", value: S.q, oninput: (e) => { S.q = e.target.value; draw(); } }),
      h("select", { class: "input", onchange: (e) => { S.team = e.target.value; draw(); } }, h("option", { value: "" }, "All teams"), teams.map((t) => h("option", { value: t, selected: t === S.team }, t))),
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: S.fa, onchange: (e) => { S.fa = e.target.checked; draw(); } }), h("span", {}, "Free agents only")),
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: S.active, onchange: (e) => { S.active = e.target.checked; draw(); } }), h("span", {}, "Active only")),
      seg([["z", "Z-scores"], ["stats", "Per-game stats"]], S.view, (v) => { S.view = v; draw(); }));
    draw();
    return [h("h2", {}, "Player rankings"),
      h("p", { class: "lede" }, `Per-game value: z-scores against the top ${cfg.pool_size} players, projected from recent and season form.`),
      controls, holder];
  }
  function statVal(r, k) { if (k === "fg_pct") return r.fga > 0 ? r.fgm / r.fga : NaN; if (k === "ft_pct") return r.fta > 0 ? r.ftm / r.fta : NaN; return r[k]; }
  function seg(options, value, onPick) {
    return h("div", { class: "seg" }, options.map(([v, label]) => h("button", { type: "button", "aria-pressed": String(v === value), onclick: (e) => {
      e.currentTarget.parentNode.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false")); e.currentTarget.setAttribute("aria-pressed", "true"); onPick(v); } }, label)));
  }

  // ---------------------------------------------------------------- My Team
  function rosterEditor({ key, title }) {
    const { map } = valued(); const ids = state.rosters[key];
    const add = (id) => { for (const k of Object.keys(state.rosters)) state.rosters[k] = state.rosters[k].filter((x) => x !== id); state.rosters[key].push(id); changed(); refocus(key); };
    const ta = h("textarea", { class: "input", placeholder: "Paste names, one per line (e.g. copied from Yahoo/ESPN). Positions and injury tags are ignored." });
    return h("div", { class: "card roster" },
      h("div", { class: "roster-head" }, h("h3", {}, title), h("span", { class: "dim" }, `${ids.length} players`)),
      picker(key, { pool: () => valued().rows.filter((p) => !ids.includes(p.id)), onAdd: add,
        placeholder: key === "other" ? "Add players owned by the other teams…" : "Search a player to add…" }),
      h("div", { class: "picked" }, ids.length ? ids.map((id) => h("span", { class: "pchip" }, map.get(id)?.name || id,
        h("button", { type: "button", "aria-label": "Remove", onclick: () => { state.rosters[key] = ids.filter((x) => x !== id); changed(); } }, "×")))
        : h("span", { class: "dim" }, "No players yet.")),
      h("details", { class: "paste" }, h("summary", {}, "Paste a list of names"), ta,
        h("div", { class: "controls" },
          h("button", { class: "btn primary small", type: "button", onclick: () => {
            const { found, missing } = NC.matchNames(ta.value.split(/\n|,|;/), valued().rows);
            for (const id of found) if (!state.rosters[key].includes(id)) { for (const k of Object.keys(state.rosters)) state.rosters[k] = state.rosters[k].filter((x) => x !== id); state.rosters[key].push(id); }
            save(); render();
            if (missing.length) alert(`Added ${found.length}. Not found: ${missing.join(", ")}`);
          } }, "Add names"),
          ids.length ? h("button", { class: "btn small", type: "button", onclick: () => { if (confirm(`Clear ${title}?`)) { state.rosters[key] = []; changed(); } } }, "Clear list") : null)));
  }

  function renderTeam() {
    if (!players.length) return [h("h2", {}, "My team"), needData()];
    const out = [h("h2", {}, "My team"),
      h("p", { class: "lede" }, "Rosters are saved in this browser. Anyone not listed below is treated as a free agent."),
      h("div", { class: "grid2" }, rosterEditor(ROSTERS[0]), rosterEditor(ROSTERS[1])),
      h("div", { style: "margin-top:16px" }, rosterEditor(ROSTERS[2])),
      h("div", { class: "controls" },
        h("button", { class: "btn small", type: "button", onclick: exportRosters }, "Download rosters (JSON)"),
        h("label", { class: "btn small" }, "Load rosters (JSON)", h("input", { type: "file", accept: "application/json,.json", hidden: true, onchange: importRosters })))];
    const my = state.rosters.my;
    if (!my.length) return out;
    const { rows, map } = valued();

    if (D.schedule) {
      const t = today(); const w = NC.weekWindow(t, state.includeToday, cfg);
      const ss = NC.startSit(rows, my, D.schedule, t, state.includeToday, cfg);
      const left = ss.reduce((s, r) => s + r.games, 0), started = ss.reduce((s, r) => s + r.started, 0);
      out.push(h("h2", {}, `Start / sit · ${niceDate(w.start)} – ${niceDate(w.end)}`),
        h("div", { class: "metrics" }, metric("Games left", left), metric("Games you can start", started), metric("Lost to bench", left - started)),
        table([
          { label: "Player", get: (r) => [r.name, r.active ? null : h("span", { class: "tag" }, "inactive")] },
          { label: "Team", get: (r) => r.team },
          { label: "Value / game", cls: "num", get: (r) => sg(r.z), style: (r) => zStyle(r.z / 3) },
          { label: "Games left", cls: "num", get: (r) => r.games },
          { label: "Starts", cls: "num", get: (r) => r.started },
          { label: "Weekly value", cls: "num strong", get: (r) => sg(r.weeklyStarted), style: (r) => zStyle(r.weeklyStarted / 6) },
        ], ss),
        h("p", { class: "note" }, `Weekly value = value per game × games you can actually start (${cfg.slots} slots a day; positions are not modelled).`));
      const lu = NC.dailyLineups(rows, my, D.schedule, t, state.includeToday, cfg);
      const days = [...new Set(lu.map((g) => g.d))];
      if (days.length) {
        const cell = new Map(lu.map((g) => [g.id + "|" + g.d, g]));
        out.push(h("details", {}, h("summary", { class: "dim", style: "cursor:pointer;margin-top:12px" }, "Daily lineup"),
          table([{ label: "Player", get: (r) => r.name }, ...days.map((d) => ({ label: niceDate(d), get: (r) => { const g = cell.get(r.id + "|" + d); return g ? (g.start ? "Start" : "Bench") : ""; },
            style: (r) => { const g = cell.get(r.id + "|" + d); return g && !g.start ? "color: rgb(var(--bad))" : null; } }))], ss)));
      }
    } else out.push(h("div", { class: "warn" }, "No schedule in this build, so games-left features are off."));

    const tz = NC.teamCategoryZ(map, my); const w = NC.needWeights(tz, cfg, state.punt);
    const weakest = CATS.filter((c) => !state.punt.includes(c)).sort((a, b) => tz[a] - tz[b]).slice(0, 3);
    out.push(h("h2", {}, "Category profile"),
      h("div", { class: "grid2" },
        h("div", { class: "card" }, divergingBars(CATS.map((c) => L[c]), CATS.map((c) => tz[c]), "Team strength (sum of per-game z)")),
        h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, "Weakest categories"),
          h("ul", {}, weakest.map((c) => h("li", {}, h("b", {}, L[c]), `: ${sg(tz[c])} z, waiver weight ×${w[c].toFixed(2)}`))),
          h("p", { class: "note" }, "A league-average 13-man roster sums to about 0 in every category."))),
      h("h3", {}, "Roster"),
      table([{ label: "Player", get: (r) => r.name }, { label: "Team", get: (r) => r.team },
        { label: "Value", cls: "num strong", get: (r) => sg(r.total), style: (r) => zStyle(r.total / 3) }, ...zCols((r) => r.z)],
        my.map((id) => map.get(id)).filter(Boolean).sort((a, b) => b.total - a.total)));
    return out;
  }
  const metric = (label, value) => h("div", { class: "metric" }, h("span", {}, label), h("b", {}, value));

  function exportRosters() {
    const { map } = valued();
    const data = Object.fromEntries(ROSTERS.map((r) => [r.file, state.rosters[r.key].map((id) => ({ player_id: id, name: map.get(id)?.name || "" }))]));
    const a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })), download: "my_rosters.json" });
    document.body.append(a); a.click(); a.remove();
  }
  function importRosters(e) {
    const file = e.target.files[0]; if (!file) return;
    file.text().then((txt) => {
      try {
        const data = JSON.parse(txt);
        for (const r of ROSTERS) state.rosters[r.key] = (data[r.file] || []).map((p) => Number(p.player_id)).filter((id) => pmap.has(id));
        changed();
      } catch (err) { alert("That file isn't a rosters JSON."); }
    });
  }

  // ---------------------------------------------------------------- Waivers
  function renderWaivers() {
    if (!players.length) return [h("h2", {}, "Waivers"), needData()];
    if (!state.rosters.my.length) return [h("h2", {}, "Waivers"), needRoster()];
    const S = state.waiver; const { rows } = valued();
    let gamesLeft = null;
    if (S.mode === "week" && D.schedule) {
      const w = NC.weekWindow(today(), state.includeToday, cfg);
      gamesLeft = NC.gamesRemaining(rows, rows.map((r) => r.id), D.schedule, w.start, w.end);
    }
    const rostered = [...state.rosters.opp, ...state.rosters.other];
    const res = NC.rankWaivers(rows, state.rosters.my, rostered, cfg, { punt: state.punt, gamesLeft, onlyActive: S.active });
    const top = res.rows.slice(0, cfg.waiver_top_n * 2);
    const cols = [{ label: "Player", get: (r) => r.name }, { label: "Team", get: (r) => r.team }];
    if (gamesLeft) cols.push({ label: "Games", cls: "num", get: (r) => r.games }, { label: "Weekly fit", cls: "num strong", get: (r) => sg(r.weeklyFit), style: (r) => zStyle(r.weeklyFit / 8) });
    cols.push({ label: "Fit", cls: "num" + (gamesLeft ? "" : " strong"), get: (r) => sg(r.fit), style: (r) => zStyle(r.fit / 3) },
      { label: "Net gain", cls: "num", get: (r) => sg(r.netGain), style: (r) => zStyle(r.netGain / 3) },
      { label: "Suggested drop", get: (r) => r.drop }, { label: "Helps most", get: (r) => r.helps.map((c) => L[c]).join(", ") },
      { label: "Value", cls: "num", get: (r) => sg(r.total) }, ...zCols((r) => r.z));
    return [h("h2", {}, "Waivers"),
      h("p", { class: "lede" }, "Free agents ranked by how much they help your weakest categories, not just raw value."),
      !state.rosters.other.length ? h("div", { class: "warn" }, "You haven't added the rest of your league's rosters (My Team tab), so everyone except your team and your opponent counts as a free agent.") : null,
      h("div", { class: "controls" },
        seg([["season", "Season-long fit"], ["week", "This week (streaming)"]], S.mode, (v) => { S.mode = v; render(); }),
        h("label", { class: "switch" }, h("input", { type: "checkbox", checked: S.active, onchange: (e) => { S.active = e.target.checked; render(); } }), h("span", {}, "Active only"))),
      h("p", {}, h("b", {}, "Category weights for your team: "), CATS.map((c) => `${L[c]} ×${res.weights[c].toFixed(2)}`).join(" · ")),
      table(cols, top),
      h("p", { class: "note" }, "Fit = Σ (category weight × player z). Weights run from ×2 for your weakest categories to ×0 for ones you dominate or punt. Net gain = fit − fit of your weakest-fit player (the suggested drop).")];
  }

  // ---------------------------------------------------------------- Trade
  function renderTrade() {
    if (!players.length) return [h("h2", {}, "Trade analyzer"), needData()];
    const my = state.rosters.my; if (!my.length) return [h("h2", {}, "Trade analyzer"), needRoster()];
    const { rows, map } = valued(); const T = state.trade;
    T.give = T.give.filter((i) => my.includes(i)); T.get = T.get.filter((i) => !my.includes(i));
    const chipList = (ids, key) => h("div", { class: "picked" }, ids.length ? ids.map((id) => h("span", { class: "pchip" }, map.get(id)?.name,
      h("button", { type: "button", "aria-label": "Remove", onclick: () => { T[key] = T[key].filter((x) => x !== id); render(); } }, "×"))) : h("span", { class: "dim" }, "None yet."));
    const out = [h("h2", {}, "Trade analyzer"),
      h("p", { class: "lede" }, "Your per-game category totals before and after a trade."),
      h("div", { class: "grid2" },
        h("div", { class: "card roster" }, h("h3", { style: "margin:0" }, "I give"),
          h("div", { class: "chips" }, my.map((id) => h("button", { type: "button", class: "chip", "aria-pressed": String(T.give.includes(id)),
            onclick: () => { T.give = T.give.includes(id) ? T.give.filter((x) => x !== id) : [...T.give, id]; render(); } }, map.get(id)?.name)))),
        h("div", { class: "card roster" }, h("h3", { style: "margin:0" }, "I get"),
          picker("get", { pool: () => rows.filter((p) => !my.includes(p.id) && !T.get.includes(p.id)), onAdd: (id) => { T.get.push(id); render(); refocus("get"); } }),
          chipList(T.get, "get")))];
    if (!T.give.length || !T.get.length) { out.push(h("p", { class: "empty" }, "Pick at least one player on each side.")); return out; }
    const partner = state.rosters.opp.some((i) => T.get.includes(i)) ? state.rosters.opp : null;
    const res = NC.analyzeTrade(rows, my, T.give, T.get, cfg, { punt: state.punt, theirIds: partner });
    const verdict = res.total > 0 && res.needWeighted > 0 ? "Accept" : res.total < 0 && res.needWeighted < 0 ? "Decline" : "Mixed";
    out.push(h("div", { class: "metrics" }, metric("Total value change", sg(res.total) + " z"), metric("Need-weighted change", sg(res.needWeighted)),
      metric("Verdict", verdict), res.theirTotal != null ? metric("Opponent's change", sg(res.theirTotal) + " z") : null),
      res.dropped.length ? h("p", { class: "note" }, "To make room you'd drop: " + res.dropped.map((i) => map.get(i).name).join(", ")) : null,
      h("div", { class: "grid2" },
        h("div", { class: "card" }, divergingBars(res.cats.map((r) => L[r.cat]), res.cats.map((r) => r.zDelta), "Category change (z)")),
        h("div", {}, table([
          { label: "Category", get: (r) => L[r.cat] },
          { label: "Before", cls: "num", get: (r) => (isPct(r.cat) ? f3(r.before) : f1(r.before)) },
          { label: "After", cls: "num", get: (r) => (isPct(r.cat) ? f3(r.after) : f1(r.after)) },
          { label: "Change", cls: "num", get: (r) => (isPct(r.cat) ? sg(r.delta, 3) : sg(r.delta, 1)) },
          { label: "z change", cls: "num strong", get: (r) => sg(r.zDelta), style: (r) => zStyle(r.zDelta) },
        ], res.cats),
        h("p", { class: "note" }, `Improves: ${res.improved.map((c) => L[c]).join(", ") || "—"} · Hurts: ${res.hurt.map((c) => L[c]).join(", ") || "—"}`))));
    return out;
  }

  // ---------------------------------------------------------------- Matchup
  function renderMatchup() {
    if (!players.length) return [h("h2", {}, "Matchup simulator"), needData()];
    if (!D.schedule) return [h("h2", {}, "Matchup simulator"), h("div", { class: "warn" }, "No schedule in this build.")];
    const { my, opp } = state.rosters;
    if (!my.length || !opp.length) return [h("h2", {}, "Matchup simulator"), h("div", { class: "empty" }, "Add your team and this week's opponent on the ",
      h("a", { href: "#team", onclick: (e) => { e.preventDefault(); setTab("team"); } }, "My Team"), " tab.")];
    const S = state.sim; S.n = S.n || cfg.sim_count;
    const t = today(); const w = NC.weekWindow(t, state.includeToday, cfg);
    const key = JSON.stringify([my, opp, S.n, S.inactive, S.wtd, state.includeToday, state.punt, t]);
    const results = h("div");
    const run = () => {
      results.replaceChildren(h("p", { class: "dim" }, `Simulating ${S.n.toLocaleString()} weeks…`));
      setTimeout(() => {
        S.result = NC.simulateMatchup(valued().rows, my, opp, D.schedule, t, cfg, { nSims: S.n, includeToday: state.includeToday,
          excludeInactive: S.inactive, useWeekToDate: S.wtd, recent: D.recent || [] });
        S.key = key; showResult();
      }, 30);
    };
    const showResult = () => {
      const r = S.result; const { rows } = valued();
      const ssMe = NC.startSit(rows, my, D.schedule, t, state.includeToday, cfg), ssOpp = NC.startSit(rows, opp, D.schedule, t, state.includeToday, cfg);
      const wk = (ss) => Object.fromEntries(CATS.map((c) => [c, ss.reduce((s, x) => s + x.wk[c], 0)]));
      results.replaceChildren(
        h("div", { class: "metrics" }, metric("Expected score", `${r.expected[0].toFixed(1)} – ${r.expected[1].toFixed(1)}`), metric("Win probability", pct(r.pWin)),
          metric("My games left", r.myGames), metric("Their games left", r.oppGames)),
        r.targets.length ? h("div", { class: "callout" }, h("b", {}, "Swing categories worth targeting: "), r.targets.map((c) => `${L[c]} (${pct(r.cats.find((x) => x.cat === c).pWin)})`).join(", "))
          : h("div", { class: "callout" }, "No close categories: every category is at least 75/25 one way."),
        h("div", { class: "grid2" }, h("div", { class: "card" }, winBars(r.cats)),
          h("div", { class: "card" }, radar([{ name: "Me", who: "me", values: wk(ssMe) }, { name: "Opponent", who: "opp", values: wk(ssOpp) }], "Projected weekly strength (z × starts)"))),
        h("div", { class: "grid2", style: "margin-top:16px" }, h("div", { class: "card" }, distBars(r.dist)),
          table([{ label: "Category", get: (x) => L[x.cat] }, { label: "Me", cls: "num", get: (x) => (isPct(x.cat) ? f3(x.my) : f1(x.my)) },
            { label: "Opponent", cls: "num", get: (x) => (isPct(x.cat) ? f3(x.opp) : f1(x.opp)) }, { label: "Win", cls: "num strong", get: (x) => pct(x.pWin) },
            { label: "Tie", cls: "num", get: (x) => pct(x.pTie) }, { label: "Median margin", cls: "num", get: (x) => (isPct(x.cat) ? sg(x.median, 3) : sg(x.median, 0)) },
            { label: "", get: (x) => (x.close ? "◆ target" : "") }], r.cats)),
        h("p", { class: "note" }, `${r.nSims.toLocaleString()} simulated weeks. Each player-game: a shared Gamma "role shock" × Poisson counting stats; shots drawn as attempts → makes. TO margin is flipped so positive is always good for you.`));
      charts.forEach((d) => d());
    };
    const out = [h("h2", {}, "Matchup simulator"),
      h("p", { class: "lede" }, `Me (${my.length} players) vs. opponent (${opp.length}) · ${niceDate(w.start)} – ${niceDate(w.end)}`),
      h("div", { class: "controls" },
        h("select", { class: "input", onchange: (e) => { S.n = Number(e.target.value); } }, [1000, 5000, 10000, 25000].map((n) => h("option", { value: n, selected: n === S.n }, `${n.toLocaleString()} sims`))),
        h("label", { class: "switch" }, h("input", { type: "checkbox", checked: S.inactive, onchange: (e) => (S.inactive = e.target.checked) }), h("span", {}, "Skip inactive players")),
        h("label", { class: "switch" }, h("input", { type: "checkbox", checked: S.wtd, onchange: (e) => (S.wtd = e.target.checked) }), h("span", {}, "Include stats already banked this week")),
        h("button", { class: "btn primary", type: "button", onclick: run }, "Run simulation")),
      results];
    if (S.result && S.key === key) setTimeout(showResult, 0); else run();
    return out;
  }

  // ---------------------------------------------------------------- About
  function renderAbout() {
    const wts = Object.values(cfg.proj_weights).map((v) => `${Math.round(v * 100)}%`).join(" / ");
    const out = [h("h2", {}, "How it works"),
      h("ol", { class: "steps" },
        h("li", {}, h("h3", {}, "Project"), h("p", {}, `Per-minute rates from the last 7 days, last 15 days and the season, blended ${wts} and multiplied by projected minutes. Small samples are shrunk toward last season (or league average for rookies) as if the prior were worth ${cfg.shrink_minutes} minutes.`)),
        h("li", {}, h("h3", {}, "Value"), h("p", { html: `Each category becomes a z-score against the top ${cfg.pool_size} players (${cfg.num_teams} × ${cfg.roster_spots}), re-selected until stable. Turnovers are flipped. FG% and FT% use <code>(player% − league%) × attempts</code>, so volume counts.` })),
        h("li", {}, h("h3", {}, "Decide"), h("p", {}, `Start/sit = value × games you can start (${cfg.slots} slots a day). Waivers weight each category by your team's need: w = 2 / (1 + e^(team z / ${cfg.need_scale})). Trades compare team totals before and after.`)),
        h("li", {}, h("h3", {}, "Simulate"), h("p", {}, `${cfg.sim_count.toLocaleString()} Monte Carlo weeks: a shared Gamma role shock per player-game × Poisson counting stats, shots as attempts → makes, PTS = 2·FGM + 3PM + FTM.`)))];
    if (D.backtest) {
      const b = D.backtest.rows;
      out.push(h("h2", {}, `How accurate are the projections? (${D.backtest.season})`),
        h("p", { class: "lede" }, `Walk-forward backtest: every week of last season, project each player from games before that date, then compare with what he averaged the following week (players projected for ${cfg.backtest_min_minutes}+ minutes).`),
        h("div", { class: "grid2" },
          h("div", { class: "card" }, divergingBars(b.map((r) => L[r.cat]), b.map((r) => r["blend_vs_season_avg_%"]), "Error reduction vs. season average", { digits: 1, suffix: "%" })),
          table([{ label: "Category", get: (r) => L[r.cat] },
            { label: "Model error", cls: "num strong", get: (r) => (isPct(r.cat) ? `${(r.blend * 100).toFixed(2)} pp` : r.blend.toFixed(3)) },
            { label: "Season avg", cls: "num", get: (r) => (isPct(r.cat) ? `${(r.season_avg * 100).toFixed(2)} pp` : r.season_avg.toFixed(3)) },
            { label: "Last 7 days", cls: "num", get: (r) => (isPct(r.cat) ? `${(r.last7_avg * 100).toFixed(2)} pp` : r.last7_avg.toFixed(3)) },
            { label: "Improvement", cls: "num", get: (r) => sg(r["blend_vs_season_avg_%"], 1) + "%" }], b)),
        h("p", { class: "note" }, "Error = mean absolute error per game; FG%/FT% errors are attempt-weighted, in percentage points. Positive improvement = lower error than the naive season average."));
    }
    out.push(h("p", { class: "note", style: "margin-top:24px" }, "The Python pipeline (nba_api → projections → backtest) builds this site's data; the browser re-runs the valuation, decisions and simulation, checked against the Python implementation by automated tests. ",
      h("a", { href: D.repo }, "Source on GitHub"), "."));
    return out;
  }

  // ---------------------------------------------------------------- boot
  function boot(data) {
    D = data; cfg = D.cfg; CATS = cfg.categories; L = cfg.labels;
    players = D.players || []; pmap = NC.byId(players);
    load();
    document.getElementById("data-status").textContent = players.length
      ? `${D.season} · ${players.length} players · data through ${niceDate(D.through)}` : `${D.season} · no player data yet`;
    document.getElementById("punt-chips").replaceChildren(...CATS.map((c) => h("button", { type: "button", class: "chip", "aria-pressed": String(state.punt.includes(c)),
      onclick: (e) => { state.punt = state.punt.includes(c) ? state.punt.filter((x) => x !== c) : [...state.punt, c]; e.currentTarget.setAttribute("aria-pressed", String(state.punt.includes(c))); changed(); } }, L[c])));
    const inc = document.getElementById("include-today"); inc.checked = state.includeToday;
    inc.addEventListener("change", () => { state.includeToday = inc.checked; changed(); });
    document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
    document.getElementById("footer").replaceChildren(`Built ${new Date(D.built).toUTCString().slice(5, 22)} UTC · Data: stats.nba.com via nba_api · `,
      h("a", { href: D.repo }, "GitHub"));
    render();
  }

  fetch("data/app.json", { cache: "no-cache" }).then((r) => r.json()).then(boot).catch((err) => {
    document.getElementById("main").replaceChildren(h("div", { class: "warn" }, "Couldn't load data/app.json: " + err.message));
  });
})();
