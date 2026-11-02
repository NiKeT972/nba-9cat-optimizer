/* NBA 9-Cat Optimizer: web UI. All maths lives in core.js (NineCat). */
(function () {
  "use strict";
  const NC = window.NineCat;
  const STORE = "ninecat:v1";
  const POWER_STORE = "ninecat:power";
  const LEGACY_FILE = { my: "My Team", opp: "Opponent", other: "Other rostered players" };

  let D, cfg, CATS, L, players, pmap;
  const state = {
    league: { teams: [], me: null, opp: null, pool: [], waivers: [] },
    rosters: { my: [], opp: [], other: [], waivers: [] }, // derived from league by syncRosters()
    punt: [], includeToday: true, tab: "rankings", leagueView: "game",
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

  // ---------------------------------------------------------------- league model
  // The league is the source of truth: named teams, plus players on waivers and an optional
  // pool of rostered players not assigned to a team. state.rosters is derived from it.
  const newId = () => "t" + Math.random().toString(36).slice(2, 9);
  const team = (id) => state.league.teams.find((t) => t.id === id) || null;
  function ensureMyTeam() {
    const lg = state.league;
    if (!team(lg.me)) {
      const t = { id: newId(), name: "My team", players: [] };
      lg.teams.unshift(t); lg.me = t.id;
    }
    if (lg.opp === lg.me || (lg.opp && !team(lg.opp))) lg.opp = null;
  }
  function syncRosters() {
    ensureMyTeam();
    const lg = state.league, me = team(lg.me), op = team(lg.opp);
    state.rosters = {
      my: me.players, opp: op ? op.players : [],
      other: lg.teams.filter((t) => t !== me && t !== op).flatMap((t) => t.players).concat(lg.pool),
      waivers: lg.waivers,
    };
  }
  /** Move a player: target = team id, "pool", "waivers", or null (remove everywhere). */
  function assign(id, target) {
    const lg = state.league;
    for (const t of lg.teams) t.players = t.players.filter((x) => x !== id);
    lg.pool = lg.pool.filter((x) => x !== id); lg.waivers = lg.waivers.filter((x) => x !== id);
    if (target === "pool") lg.pool.push(id);
    else if (target === "waivers") lg.waivers.push(id);
    else if (target) team(target)?.players.push(id);
  }
  const listOf = (target) => (target === "pool" ? state.league.pool : target === "waivers" ? state.league.waivers : team(target)?.players || []);
  function ownerMap() {
    const m = new Map();
    for (const t of state.league.teams) for (const id of t.players) m.set(id, t);
    return m;
  }
  function cleanLeague(lg) {
    const ok = (ids) => (ids || []).map(Number).filter((id) => pmap.has(id));
    return { teams: (lg.teams || []).map((t) => ({ id: t.id || newId(), name: String(t.name || "Team"), players: ok(t.players) })),
      me: lg.me || null, opp: lg.opp || null, pool: ok(lg.pool), waivers: ok(lg.waivers) };
  }
  function fromLegacy(r) {
    const me = { id: newId(), name: "My team", players: (r.my || []).filter((id) => pmap.has(id)) };
    const teams = [me]; let opp = null;
    if ((r.opp || []).length) { const o = { id: newId(), name: "Opponent", players: r.opp.filter((id) => pmap.has(id)) }; teams.push(o); opp = o.id; }
    return { teams, me: me.id, opp, pool: (r.other || []).filter((id) => pmap.has(id)), waivers: [] };
  }

  function save() {
    try { localStorage.setItem(STORE, JSON.stringify({ league: state.league, punt: state.punt, includeToday: state.includeToday, tab: state.tab })); } catch (e) { /* private mode */ }
  }
  function load() {
    let s = {};
    try {
      s = JSON.parse(localStorage.getItem(STORE) || "{}");
      if (s.league) state.league = cleanLeague(s.league);
      else if (s.rosters) state.league = fromLegacy(s.rosters);
      if (Array.isArray(s.punt)) state.punt = s.punt.filter((c) => CATS.includes(c));
      if (typeof s.includeToday === "boolean") state.includeToday = s.includeToday;
      if (s.tab) state.tab = s.tab;
    } catch (e) { /* ignore */ }
    syncRosters();
    const hash = location.hash.slice(1);
    if (hash) state.tab = hash;
    else if (!s.tab) state.tab = state.rosters.my.length ? "advisor" : "rankings";
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
    const fn = { advisor: renderAdvisor, rankings: renderRankings, team: renderTeam, league: renderLeague, waivers: renderWaivers, trade: renderTrade, matchup: renderMatchup, about: renderAbout }[state.tab];
    el.replaceChildren(...[].concat(fn ? fn() : renderRankings()).filter((x) => x !== null && x !== undefined && x !== false));
    charts.forEach((d) => d()); // charts can only draw once their container is in the page
  }
  function setTab(t) { state.tab = t; history.replaceState(null, "", "#" + t); save(); render(); window.scrollTo({ top: 0 }); }
  function changed() { syncRosters(); save(); render(); }
  const needData = () => h("div", { class: "empty" }, "No player data in this build yet. Run ", h("code", {}, "python -m src.data.pipeline"),
    " then ", h("code", {}, "python -m app.static_site"), " and push.");
  const needRoster = () => h("div", { class: "empty" }, "Add your players on the ", h("a", { href: "#team", onclick: (e) => { e.preventDefault(); setTab("team"); } }, "My Team"), " tab first.");


  // ---------------------------------------------------------------- Advisor
  const advCache = { key: null, data: null };
  const catList = (cats) => cats.map((c) => L[c]).join(", ");
  const pill = (text, kind) => h("span", { class: "pill " + kind }, text);

  function renderAdvisor() {
    if (!players.length) return [h("h2", {}, "Advisor"), needData()];
    if (!state.rosters.my.length) return [h("h2", {}, "Advisor"), h("div", { class: "empty" },
      "Add your team (and this week's opponent) on the ", h("a", { href: "#team", onclick: (e) => { e.preventDefault(); setTab("team"); } }, "My Team"),
      " tab and the advisor will tell you who to start, who to pick up, which trades to offer and which categories to chase.")];
    const t = today();
    const key = JSON.stringify([state.rosters, state.punt, state.includeToday, t]);
    const holder = h("div", { class: "advice" });
    const show = () => holder.replaceChildren(...adviceCards(advCache.data).filter(Boolean));
    if (advCache.key === key) show();
    else {
      holder.replaceChildren(h("p", { class: "dim" }, "Analysing your roster, the waiver wire and this week's matchup…"));
      setTimeout(() => {
        advCache.data = NC.advise(valued().rows, state.rosters, D.schedule || [], t, cfg,
          { punt: state.punt, includeToday: state.includeToday, recent: D.recent || [] });
        advCache.key = key; show();
      }, 30);
    }
    return [h("h2", {}, "Advisor"),
      h("p", { class: "lede" }, "Plain-language roster decisions for this week, ranked by impact. Every suggestion is backed by the projections and a re-run of your matchup simulation."),
      !state.rosters.other.length ? h("div", { class: "warn" }, "Tip: add the other teams on the ", h("a", { href: "#league", onclick: (e) => { e.preventDefault(); setTab("league"); } }, "League"),
        " tab, so the advisor only suggests players who are actually available.") : null,
      holder];
  }

  function adviceCards(a) {
    const { map } = valued(); const name = (id) => map.get(id)?.name || "?";
    const cards = [];

    // Matchup
    if (a.matchup) {
      const m = a.matchup; const [me, them] = m.expected; const winning = me > them;
      const pc = (c) => pct(m.cats.find((x) => x.cat === c).pWin);
      cards.push(h("div", { class: "acard" }, h("h3", {}, "This week's matchup"),
        h("div", { class: "headline" }, `You're projected to ${winning ? "win" : "lose"} ${me.toFixed(1)}–${them.toFixed(1)} · ${pct(m.pWin)} chance to win the week`),
        h("ul", {},
          m.targets.length ? h("li", {}, h("b", {}, "Chase: "), m.targets.map((c) => `${L[c]} (${pc(c)})`).join(", "),
            ". These are close, so stream and start for them.") : h("li", {}, "No category is close: the week is mostly decided by your current roster."),
          m.safe.length ? h("li", {}, h("b", {}, "Locked in: "), catList(m.safe), ". No need to add more here.") : null,
          m.lost.length ? h("li", {}, h("b", {}, "Out of reach: "), catList(m.lost), ". Don't spend moves chasing these this week.") : null,
          h("li", {}, `Games left: you ${m.myGames}, them ${m.oppGames}.`,
            m.myGames < m.oppGames ? " You're behind on volume, so a streamer with extra games helps." : ""))));
    } else if (!state.rosters.opp.length) {
      cards.push(h("div", { class: "acard" }, h("h3", {}, "This week's matchup"),
        h("p", {}, "Pick this week's opponent on My Team (or the League tab) to get matchup advice and to test every move against them.")));
    }

    // Waiver moves
    const moves = a.moves;
    cards.push(h("div", { class: "acard" }, h("h3", {}, "Waiver moves"),
      moves.length ? moves.map((mv) => h("div", { class: "rec" },
        h("div", {},
          h("div", { class: "what" }, mv.claim ? "Put in a claim for " : "Add ", h("b", {}, mv.addName),
            mv.claim ? h("span", { class: "tag" }, "waivers") : h("span", { class: "tag" }, "FA"), ", drop ", h("b", {}, mv.dropName)),
          h("div", { class: "why" },
            mv.helps.length ? ["Helps ", catList(mv.helps), " · "] : null,
            "season value ", pill(sg(mv.gain), mv.gain >= 0 ? "good" : "bad"),
            mv.dExpected !== undefined ? [" · this week ", Math.abs(mv.dExpected) < 0.05 ? pill("±0.0 cats", "neutral")
              : pill(`${sg(mv.dExpected, 1)} cats`, mv.dExpected > 0 ? "good" : "bad"),
              ` win chance ${pct(a.matchup.pWin)} → ${pct(a.matchup.pWin + mv.dWin)}`] : null,
            mv.games !== null ? ` · ${mv.games} games this week vs ${mv.dropGames}` : null,
            mv.claim ? ` · claim clears ${niceDate(mv.claimDate)}, so earlier games don't count` : null)),
        h("button", { class: "btn primary small", type: "button", onclick: () => {
          if (!confirm(`${mv.claim ? "Claim" : "Add"} ${mv.addName} and drop ${mv.dropName}? (${mv.dropName} goes on waivers.)`)) return;
          assign(mv.add, state.league.me); assign(mv.drop, "waivers");
          changed();
        } }, mv.claim ? "Mark as claimed" : "Make this move")))
        : h("p", { class: "dim" }, "No free agent clearly beats your weakest player right now. Hold your roster.")));

    // Lineup
    if (a.lineup) {
      const lu = a.lineup; const items = [];
      if (lu.lostStarts) items.push(h("li", {}, `You'll lose ${lu.lostStarts} start${lu.lostStarts > 1 ? "s" : ""} to the bench on crowded days: `,
        lu.benched.map((b) => `${b.name} (${b.days.map((d) => niceDate(d).split(",")[0]).join(", ")})`).join("; "),
        ". If one of them is a weak hold, swapping him for a player on a lighter schedule gains games."));
      for (const p of lu.idle) items.push(h("li", {}, h("b", {}, p.name), ` hasn't played in ${p.days} days. Likely injured: move him to an IL slot or consider dropping him.`));
      for (const p of lu.noGames) items.push(h("li", {}, h("b", {}, p.name), " has no games left this week."));
      cards.push(h("div", { class: "acard" }, h("h3", {}, "Lineup"),
        items.length ? h("ul", {}, items) : h("p", {}, "Your lineup is clean: every player can start all his games this week.")));
    }

    // Trades
    cards.push(h("div", { class: "acard" }, h("h3", {}, "Trade ideas"),
      a.trades.length ? a.trades.map((t) => h("div", { class: "rec" },
        h("div", {},
          h("div", { class: "what" }, "Offer ", h("b", {}, t.giveName), " for ", h("b", {}, t.getName),
            h("span", { class: "dim" }, t.fromOpponent ? "  (your opponent)" : "  (another team)")),
          h("div", { class: "why" },
            t.improves.length ? ["Improves ", catList(t.improves)] : "Better fit for your needs",
            t.costs.length ? [" · costs ", catList(t.costs)] : null,
            " · your need-weighted gain ", pill(sg(t.gain), "good"),
            " · for them ", pill(`${sg(t.theirChange)} value`, t.theirChange >= 0 ? "good" : "neutral"))),
        h("button", { class: "btn small", type: "button", onclick: () => { state.trade.give = [t.give]; state.trade.get = [t.get]; setTab("trade"); } }, "Open in Trade")))
        : h("p", { class: "dim" }, state.rosters.other.length || state.rosters.opp.length
          ? "No fair 1-for-1 trade clearly improves your weak categories right now."
          : "Add the other teams on the League tab to get trade ideas.")));

    // Strategy
    const weakest = CATS.filter((c) => !state.punt.includes(c)).sort((x, y) => a.teamZ[x] - a.teamZ[y]).slice(0, 2);
    cards.push(h("div", { class: "acard" }, h("h3", {}, "Strategy"),
      h("ul", {},
        a.strategy.map((s) => h("li", {}, h("b", {}, `${L[s.cat]} is far behind`), ` (your team is ${sg(s.teamZ)} z there). Decide: either fix it (the moves above target it), or punt it so every suggestion builds the other eight categories instead. `,
          h("button", { class: "btn small", type: "button", onclick: () => { state.punt = [...state.punt, s.cat]; syncPuntChips(); changed(); } }, `Punt ${L[s.cat]}`))),
        h("li", {}, "Your biggest needs right now: ", h("b", {}, catList(weakest)), ". Waiver and trade suggestions weight these most."),
        state.punt.length ? h("li", {}, `Punting: ${catList(state.punt)}. These are ignored in every suggestion.`) : null)));
    return cards;
  }
  function syncPuntChips() {
    document.querySelectorAll("#punt-chips .chip").forEach((b, i) => b.setAttribute("aria-pressed", String(state.punt.includes(CATS[i]))));
  }

  // ---------------------------------------------------------------- Rankings
  function renderRankings() {
    if (!players.length) return [h("h2", {}, "Player rankings"), needData()];
    const { rows } = valued(); const S = state.rank;
    const mine = new Set(state.rosters.my); const taken = new Set([...state.rosters.my, ...state.rosters.opp, ...state.rosters.other]);
    const owners = ownerMap(); const onW = new Set(state.league.waivers);
    const status = (id) => (owners.has(id) ? owners.get(id).name : onW.has(id) ? "Waivers" : taken.has(id) ? "Rostered" : "FA");
    const avail = { all: () => true, available: (id) => !taken.has(id), fa: (id) => !taken.has(id) && !onW.has(id), waivers: (id) => onW.has(id) };
    const teams = [...new Set(players.map((p) => p.team))].sort();
    const holder = h("div");
    const draw = () => {
      const q = NC.norm(S.q);
      let list = rows.filter((r) => (!q || NC.norm(r.name).includes(q)) && (!S.team || r.team === S.team) && avail[S.avail || "all"](r.id) && (!S.active || r.active));
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
        { label: "Status", get: (r) => { const st = status(r.id); return st === "FA" || st === "Waivers" ? h("span", { class: "tag" }, st) : h("span", { class: "dim" }, st); } },
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
      h("select", { class: "input", onchange: (e) => { S.avail = e.target.value; draw(); } },
        [["all", "All players"], ["available", "Available (FA + waivers)"], ["fa", "Free agents only"], ["waivers", "On waivers only"]]
          .map(([v, lab]) => h("option", { value: v, selected: v === (S.avail || "all") }, lab))),
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
  function listEditor({ target, title, head = null, placeholder, empty = "No players yet." }) {
    const { map } = valued(); const ids = listOf(target); const key = "pick-" + target;
    const add = (id) => { assign(id, target); changed(); refocus(key); };
    const ta = h("textarea", { class: "input", placeholder: "Paste names, one per line (e.g. copied from Yahoo/ESPN). Positions and injury tags are ignored." });
    return h("div", { class: "card roster" },
      head || h("div", { class: "roster-head" }, h("h3", {}, title), h("span", { class: "dim" }, `${ids.length} players`)),
      picker(key, { pool: () => valued().rows.filter((p) => !ids.includes(p.id)), onAdd: add, placeholder: placeholder || "Search a player to add…" }),
      h("div", { class: "picked" }, ids.length ? ids.map((id) => h("span", { class: "pchip" }, map.get(id)?.name || id,
        h("button", { type: "button", "aria-label": "Remove", onclick: () => { assign(id, null); changed(); } }, "×")))
        : h("span", { class: "dim" }, empty)),
      h("details", { class: "paste" }, h("summary", {}, "Paste a list of names"), ta,
        h("div", { class: "controls" },
          h("button", { class: "btn primary small", type: "button", onclick: () => {
            const { found, missing } = NC.matchNames(ta.value.split(/\n|,|;/), valued().rows);
            for (const id of found) if (!listOf(target).includes(id)) assign(id, target);
            changed();
            if (missing.length) alert(`Added ${found.length}. Not found: ${missing.join(", ")}`);
          } }, "Add names"),
          ids.length ? h("button", { class: "btn small", type: "button", onclick: () => { if (confirm(`Clear ${title || "this list"}?`)) { [...ids].forEach((id) => assign(id, null)); changed(); } } }, "Clear list") : null)));
  }
  function opponentPicker() {
    const lg = state.league; const choices = lg.teams.filter((t) => t.id !== lg.me);
    return h("select", { class: "input", onchange: (e) => {
      const v = e.target.value;
      if (v === "__new") { const t = { id: newId(), name: `Team ${lg.teams.length + 1}`, players: [] }; lg.teams.push(t); lg.opp = t.id; }
      else lg.opp = v || null;
      changed();
    } }, h("option", { value: "" }, "— choose this week's opponent —"),
      choices.map((t) => h("option", { value: t.id, selected: t.id === lg.opp }, `${t.name} (${t.players.length})`)),
      h("option", { value: "__new" }, "+ New team"));
  }

  function renderTeam() {
    if (!players.length) return [h("h2", {}, "My team"), needData()];
    const out = [h("h2", {}, "My team"),
      h("p", { class: "lede" }, "Rosters are saved in this browser. Add every team in your league on the ",
        h("a", { href: "#league", onclick: (e) => { e.preventDefault(); setTab("league"); } }, "League"), " tab; anyone not on a team is a free agent (or on waivers, if you list them)."),
      h("div", { class: "grid2" },
        listEditor({ target: state.league.me, title: team(state.league.me).name }),
        h("div", { class: "card roster" }, h("div", { class: "roster-head" }, h("h3", {}, "This week's opponent")), opponentPicker(),
          state.league.opp ? listEditor({ target: state.league.opp, title: team(state.league.opp).name }) : h("p", { class: "dim" }, "Pick a team from your league, or create one."))),
      state.league.pool.length ? h("div", { style: "margin-top:16px" }, listEditor({ target: "pool", title: "Other rostered players (not assigned to a team)" })) : null,
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
    const { map } = valued(); const lg = state.league;
    const named = (ids) => ids.map((id) => ({ player_id: id, name: map.get(id)?.name || "" }));
    const data = {
      format: "ninecat-league-v1",
      league: { teams: lg.teams.map((t) => ({ id: t.id, name: t.name, players: named(t.players) })), me: lg.me, opp: lg.opp,
        pool: named(lg.pool), waivers: named(lg.waivers) },
      // the same rosters in the Python/Streamlit app's format
      [LEGACY_FILE.my]: named(state.rosters.my), [LEGACY_FILE.opp]: named(state.rosters.opp), [LEGACY_FILE.other]: named(state.rosters.other),
    };
    const a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })), download: "my_league.json" });
    document.body.append(a); a.click(); a.remove();
  }
  function importRosters(e) {
    const file = e.target.files[0]; if (!file) return;
    file.text().then((txt) => {
      try {
        const data = JSON.parse(txt);
        const ids = (list) => (list || []).map((p) => Number(typeof p === "object" ? p.player_id : p));
        if (data.league) {
          const lg = data.league;
          state.league = cleanLeague({ ...lg, teams: lg.teams.map((t) => ({ ...t, players: ids(t.players) })), pool: ids(lg.pool), waivers: ids(lg.waivers) });
        } else {
          state.league = fromLegacy({ my: ids(data[LEGACY_FILE.my]), opp: ids(data[LEGACY_FILE.opp]), other: ids(data[LEGACY_FILE.other]) });
        }
        changed();
      } catch (err) { alert("That file isn't a rosters/league JSON."); }
    });
  }


  // ---------------------------------------------------------------- League
  function powerHistory(weekStart, table) {
    // Keep each week's latest ranks so the table can show movement since last week.
    let hist = {};
    try { hist = JSON.parse(localStorage.getItem(POWER_STORE) || "{}"); } catch (e) { /* ignore */ }
    const prevWeek = Object.keys(hist).filter((w) => w < weekStart).sort().pop();
    hist[weekStart] = Object.fromEntries(table.map((t) => [t.id, t.rank]));
    try { localStorage.setItem(POWER_STORE, JSON.stringify(hist)); } catch (e) { /* ignore */ }
    return prevWeek ? hist[prevWeek] : null;
  }
  const rankStyle = (rank, n) => zStyle(((n + 1) / 2 - rank) / ((n - 1) / 2 || 1) * 2.5);
  const record = (r) => (r ? `${r.w}-${r.l}-${r.t}` : "–");

  function teamCard(t) {
    const lg = state.league; const isMe = t.id === lg.me, isOpp = t.id === lg.opp;
    const head = h("div", { class: "roster" },
      h("div", { class: "roster-head" },
        h("input", { class: "team-name", value: t.name, "aria-label": "Team name", onchange: (e) => { t.name = e.target.value.trim() || t.name; changed(); } }),
        h("span", { class: "dim" }, `${t.players.length}`)),
      h("div", { class: "team-actions" },
        isMe ? h("span", { class: "tag me-tag" }, "You") : h("button", { class: "btn small", type: "button", onclick: () => { lg.me = t.id; if (lg.opp === t.id) lg.opp = null; changed(); } }, "This is me"),
        isMe ? null : isOpp ? h("span", { class: "tag" }, "Opponent this week")
          : h("button", { class: "btn small", type: "button", onclick: () => { lg.opp = t.id; changed(); } }, "Opponent this week"),
        isMe ? null : h("button", { class: "btn small", type: "button", onclick: () => {
          if (!confirm(`Delete ${t.name}? Its players become free agents.`)) return;
          lg.teams = lg.teams.filter((x) => x !== t); changed(); } }, "Delete")));
    return listEditor({ target: t.id, title: t.name, head });
  }

  function renderLeague() {
    if (!players.length) return [h("h2", {}, "League"), needData()];
    const lg = state.league; const { rows } = valued(); const t = today();
    const addTeam = (n = 1) => { for (let k = 0; k < n; k++) lg.teams.push({ id: newId(), name: `Team ${lg.teams.length + 1}`, players: [] }); changed(); };
    const out = [h("h2", {}, "League"),
      h("p", { class: "lede" }, "Add every team in your league. The app then rates each team in all nine categories, builds weekly power rankings, and knows exactly who is available.")];

    const filled = lg.teams.filter((x) => x.players.length);
    if (filled.length >= 2) {
      const table = NC.leagueTable(rows, lg.teams, D.schedule || [], t, cfg, { includeToday: state.includeToday });
      const n = table.length; const hasWeek = table.some((x) => x.weekRecord);
      const prev = powerHistory(NC.weekWindow(t, state.includeToday, cfg).weekStart, table);
      const tag = (x) => (x.id === lg.me ? h("span", { class: "tag me-tag" }, "you") : x.id === lg.opp ? h("span", { class: "tag" }, "opponent") : null);
      const moveCell = (x) => {
        if (!prev || prev[x.id] === undefined) return h("span", { class: "dim" }, "–");
        const d = prev[x.id] - x.rank;
        return d > 0 ? h("span", { class: "move up" }, `▲${d}`) : d < 0 ? h("span", { class: "move down" }, `▼${-d}`) : h("span", { class: "dim" }, "=");
      };
      out.push(h("h3", {}, "Power rankings"),
        table_(["Rank", "±", "Team", "All-play (season)", "Win %", hasWeek ? "This week" : null, hasWeek ? "Starts left" : null, "Total z", ...CATS.map((c) => L[c])].filter(Boolean), table.map((x) => [
          { v: x.rank, cls: "num strong" }, { v: moveCell(x) }, { v: [x.name, tag(x)] },
          { v: record(x.perGameRecord), cls: "num" }, { v: pct(x.perGameRecord.pct), cls: "num strong", style: rankStyle(x.rank, n) },
          hasWeek ? { v: `#${x.weekRank} · ${pct(x.weekRecord.pct)}`, cls: "num", style: rankStyle(x.weekRank, n) } : null,
          hasWeek ? { v: x.started, cls: "num" } : null,
          { v: sg(x.total), cls: "num" },
          ...CATS.map((c) => ({ v: x.catRank[c], cls: "num", style: rankStyle(x.catRank[c], n) })),
        ].filter(Boolean)), (x) => (x.id === lg.me ? "mine" : null)),
        h("p", { class: "note" }, `All-play: each team's projected per-game line compared with every other team in all 9 categories (wins-losses-ties). "This week" uses each roster's projected totals for the games it can still start this week. Category columns show each team's rank (1 = best of ${n}). ▲▼ = movement since last week's rankings on this device.`));

      const V = state.leagueView;
      const stat = (x, c) => { const line = V === "week" && x.week ? x.week : x.perGame; return isPct(c) ? f3(line[c]) : V === "week" ? f1(line[c]) : f1(line[c]); };
      out.push(h("div", { class: "controls", style: "margin-top:24px" }, h("h3", { style: "margin:0" }, "Projected team stats"),
          seg([["game", "Per game"], ["week", "This week"]], V, (v) => { state.leagueView = v; render(); })),
        table_(["Team", ...CATS.map((c) => L[c])], table.map((x) => [{ v: [x.name, tag(x)] },
          ...CATS.map((c) => ({ v: stat(x, c), cls: "num", style: rankStyle(x.catRank[c], n) }))]), (x) => null),
        h("p", { class: "note" }, "Per game: sum of each player's projected per-game stats (FG%/FT% from total makes ÷ attempts). This week: projected totals over the games each team can start. Shading follows the season category rank."));
    } else {
      out.push(h("div", { class: "empty" }, "Add at least two teams with players to see power rankings and category ratings."));
    }

    out.push(h("div", { class: "controls", style: "margin-top:28px" }, h("h3", { style: "margin:0" }, `Teams (${lg.teams.length})`),
      h("button", { class: "btn primary small", type: "button", onclick: () => addTeam(1) }, "+ Add team"),
      lg.teams.length < cfg.num_teams ? h("button", { class: "btn small", type: "button", onclick: () => addTeam(cfg.num_teams - lg.teams.length) },
        `Create the rest of a ${cfg.num_teams}-team league`) : null,
      h("button", { class: "btn small", type: "button", onclick: exportRosters }, "Download league (JSON)"),
      h("label", { class: "btn small" }, "Load league (JSON)", h("input", { type: "file", accept: "application/json,.json", hidden: true, onchange: importRosters }))),
      h("div", { class: "team-grid" }, lg.teams.map(teamCard),
        listEditor({ target: "waivers", title: "On waivers", placeholder: "Add a player who is on waivers…",
          empty: "Nobody marked. Players on no team count as free agents." }),
        lg.pool.length ? listEditor({ target: "pool", title: "Rostered, team unknown" }) : null));
    return out;
  }
  /** Small table helper: header labels + rows of {v, cls, style} cells. */
  function table_(head, rowsData, rowClass) {
    return h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, head.map((x) => h("th", {}, x)))),
      h("tbody", {}, rowsData.map((cells, i) => h("tr", {}, cells.map((c) => h("td", { class: c.cls || null, style: c.style || null }, c.v)))))));
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
    const onW = new Set(state.league.waivers);
    const rostered = [...state.rosters.opp, ...state.rosters.other, ...(S.includeW === false ? state.league.waivers : [])];
    const res = NC.rankWaivers(rows, state.rosters.my, rostered, cfg, { punt: state.punt, gamesLeft, onlyActive: S.active });
    const top = res.rows.slice(0, cfg.waiver_top_n * 2);
    const cols = [{ label: "Player", get: (r) => [r.name, h("span", { class: "tag" }, onW.has(r.id) ? "waivers" : "FA")] }, { label: "Team", get: (r) => r.team }];
    if (gamesLeft) cols.push({ label: "Games", cls: "num", get: (r) => r.games }, { label: "Weekly fit", cls: "num strong", get: (r) => sg(r.weeklyFit), style: (r) => zStyle(r.weeklyFit / 8) });
    cols.push({ label: "Fit", cls: "num" + (gamesLeft ? "" : " strong"), get: (r) => sg(r.fit), style: (r) => zStyle(r.fit / 3) },
      { label: "Net gain", cls: "num", get: (r) => sg(r.netGain), style: (r) => zStyle(r.netGain / 3) },
      { label: "Suggested drop", get: (r) => r.drop }, { label: "Helps most", get: (r) => r.helps.map((c) => L[c]).join(", ") },
      { label: "Value", cls: "num", get: (r) => sg(r.total) }, ...zCols((r) => r.z));
    return [h("h2", {}, "Waivers"),
      h("p", { class: "lede" }, "Free agents ranked by how much they help your weakest categories, not just raw value."),
      !state.rosters.other.length ? h("div", { class: "warn" }, "You haven't added the other teams (League tab), so everyone except your team and your opponent counts as available.") : null,
      h("div", { class: "controls" },
        seg([["season", "Season-long fit"], ["week", "This week (streaming)"]], S.mode, (v) => { S.mode = v; render(); }),
        h("label", { class: "switch" }, h("input", { type: "checkbox", checked: S.active, onchange: (e) => { S.active = e.target.checked; render(); } }), h("span", {}, "Active only")),
        h("label", { class: "switch" }, h("input", { type: "checkbox", checked: S.includeW !== false, onchange: (e) => { S.includeW = e.target.checked; render(); } }), h("span", {}, "Include players on waivers"))),
      h("details", { style: "margin:8px 0 12px" }, h("summary", { class: "dim", style: "cursor:pointer" }, `Players on waivers in my league (${state.league.waivers.length})`),
        listEditor({ target: "waivers", title: "On waivers", placeholder: "Add a player who is on waivers…", empty: "Nobody marked as on waivers. Everyone unrostered counts as a free agent." })),
      h("p", {}, h("b", {}, "Category weights for your team: "), CATS.map((c) => `${L[c]} ×${res.weights[c].toFixed(2)}`).join(" · ")),
      table(cols, top),
      h("p", { class: "note" }, "Fit = Σ (category weight × player z). Weights run from ×2 for your weakest categories to ×0 for ones you dominate or punt. Net gain = fit − fit of your lowest-value player (the suggested drop).")];
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
    load(); save(); // persist any migration of older saved rosters right away
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
