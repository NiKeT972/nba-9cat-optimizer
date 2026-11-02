/*
 * NBA 9-Cat Optimizer: browser engine.
 *
 * A line-for-line port of the Python reference implementation in src/valuation and
 * src/simulation, so the deployed web app needs no server. tests/test_web_parity.py runs
 * this file under Node and checks it against the Python functions on the same inputs.
 *
 * All tunable numbers come from `cfg`, which is exported from src/config.py at build time.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.NineCat = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const CATS = ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "FG_PCT", "FT_PCT", "TOV"];
  const COUNT = { PTS: "pts", REB: "reb", AST: "ast", STL: "stl", BLK: "blk", FG3M: "fg3m", TOV: "tov" };
  const PCT = { FG_PCT: ["fgm", "fga"], FT_PCT: ["ftm", "fta"] };
  const NEG = new Set(["TOV"]);

  // ------------------------------------------------------------------ helpers
  const sum = (a) => a.reduce((s, v) => s + v, 0);
  const keepCats = (punt) => CATS.filter((c) => !punt.includes(c));
  const byId = (rows) => new Map(rows.map((r) => [r.id, r]));

  // ------------------------------------------------------------------ z-scores
  function poolPct(players) {
    const out = {};
    for (const [cat, [m, a]] of Object.entries(PCT)) {
      const att = sum(players.map((p) => p[a]));
      out[cat] = att > 0 ? sum(players.map((p) => p[m])) / att : 0;
    }
    return out;
  }

  /** Counting stats as-is; FG%/FT% as volume-weighted impact = makes - pool% x attempts. */
  function catValues(p, pct) {
    const v = {};
    for (const c of CATS) v[c] = c in COUNT ? p[COUNT[c]] : p[PCT[c][0]] - pct[c] * p[PCT[c][1]];
    return v;
  }

  function zOf(vals, mean, std) {
    const z = {};
    for (const c of CATS) {
      z[c] = std[c] > 0 ? (vals[c] - mean[c]) / std[c] : 0;
      if (NEG.has(c)) z[c] = -z[c];
    }
    return z;
  }

  /** Iteratively find the self-consistent top-N pool (stops on convergence or a 2-cycle). */
  function fitPool(players, cfg, punt = []) {
    const keep = keepCats(punt);
    const n = Math.min(cfg.pool_size, players.length);
    let pool = players.map((_, i) => i);
    const seen = new Set();
    let mean, std, pct, it;
    for (it = 1; it <= cfg.max_iter; it++) {
      const poolPlayers = pool.map((i) => players[i]);
      pct = poolPct(poolPlayers);
      const vals = poolPlayers.map((p) => catValues(p, pct));
      mean = {}; std = {};
      for (const c of CATS) {
        const xs = vals.map((v) => v[c]);
        const m = sum(xs) / xs.length;
        mean[c] = m;
        std[c] = Math.sqrt(sum(xs.map((x) => (x - m) ** 2)) / xs.length); // population std
      }
      const totals = players.map((p, i) => {
        const z = zOf(catValues(p, pct), mean, std);
        return { i, t: sum(keep.map((c) => z[c])) };
      });
      // nlargest(keep="first"): sort by value desc, ties by original position
      totals.sort((a, b) => b.t - a.t || a.i - b.i);
      const next = totals.slice(0, n).map((x) => x.i);
      const key = [...next].sort((a, b) => a - b).join(",");
      const cur = [...pool].sort((a, b) => a - b).join(",");
      if (key === cur || seen.has(key)) break;
      seen.add(cur);
      pool = next;
    }
    return { mean, std, pct, poolIdx: new Set(pool), iterations: it };
  }

  /** Rank players. Returns rows {..player, z:{cat}, total, rank, inPool} sorted by total desc. */
  function valuePlayers(players, cfg, punt = []) {
    const keep = keepCats(punt);
    const fit = fitPool(players, cfg, punt);
    const rows = players.map((p, i) => {
      const z = zOf(catValues(p, fit.pct), fit.mean, fit.std);
      return { ...p, z, total: sum(keep.map((c) => z[c])), inPool: fit.poolIdx.has(i), _i: i };
    });
    rows.sort((a, b) => b.total - a.total || a._i - b._i);
    rows.forEach((r, k) => (r.rank = k + 1));
    rows.fit = fit;
    return rows;
  }

  // ------------------------------------------------------------------ team helpers
  function teamTotals(map, ids, games) {
    const s = { pts: 0, reb: 0, ast: 0, stl: 0, blk: 0, fg3m: 0, tov: 0, fgm: 0, fga: 0, ftm: 0, fta: 0 };
    for (const id of ids) {
      const p = map.get(id); if (!p) continue;
      const g = games ? (games.get(id) || 0) : 1;
      for (const k of Object.keys(s)) s[k] += p[k] * g;
    }
    const out = {};
    for (const c of CATS) out[c] = c in COUNT ? s[COUNT[c]] : (s[PCT[c][1]] > 0 ? s[PCT[c][0]] / s[PCT[c][1]] : NaN);
    return out;
  }

  function teamCategoryZ(map, ids) {
    const out = Object.fromEntries(CATS.map((c) => [c, 0]));
    for (const id of ids) { const p = map.get(id); if (p) for (const c of CATS) out[c] += p.z[c]; }
    return out;
  }

  /** w = 2 / (1 + exp(team_z / scale)); punted categories get 0. */
  function needWeights(teamZ, cfg, punt = []) {
    const w = {};
    for (const c of CATS) w[c] = punt.includes(c) ? 0 : 2 / (1 + Math.exp(teamZ[c] / cfg.need_scale));
    return w;
  }

  // ------------------------------------------------------------------ dates & schedule
  const toUTC = (d) => { const [y, m, dd] = d.split("-").map(Number); return Date.UTC(y, m - 1, dd); };
  const fromUTC = (t) => new Date(t).toISOString().slice(0, 10);
  const addDays = (d, n) => fromUTC(toUTC(d) + n * 86400000);
  const pyWeekday = (d) => (new Date(toUTC(d)).getUTCDay() + 6) % 7; // Monday = 0

  function weekWindow(today, includeToday, cfg) {
    const into = (pyWeekday(today) - cfg.week_start_weekday + 7) % 7;
    return { start: includeToday ? today : addDays(today, 1), end: addDays(today, 6 - into), weekStart: addDays(today, -into) };
  }

  /** One entry per (player, scheduled game) in [start, end]. */
  function playerGames(rows, ids, schedule, start, end) {
    const byTeam = new Map();
    for (const g of schedule) {
      if (g.d < start || g.d > end) continue;
      for (const [team, opp] of [[g.h, g.a], [g.a, g.h]]) {
        if (!byTeam.has(team)) byTeam.set(team, []);
        byTeam.get(team).push({ d: g.d, opp });
      }
    }
    const map = byId(rows); const out = [];
    for (const id of ids) {
      const p = map.get(id); if (!p) continue;
      for (const g of byTeam.get(p.team) || []) out.push({ id, d: g.d, opp: g.opp, z: p.total });
    }
    return out;
  }

  function gamesRemaining(rows, ids, schedule, start, end) {
    const m = new Map(ids.map((i) => [i, 0]));
    for (const g of playerGames(rows, ids, schedule, start, end)) m.set(g.id, m.get(g.id) + 1);
    return m;
  }

  /** Daily lineup: top `slots` per-game values start each day (positions ignored). */
  function dailyLineups(rows, ids, schedule, today, includeToday, cfg) {
    const { start, end } = weekWindow(today, includeToday, cfg);
    const pg = playerGames(rows, ids, schedule, start, end);
    const days = new Map();
    pg.forEach((g, k) => { g._k = k; if (!days.has(g.d)) days.set(g.d, []); days.get(g.d).push(g); });
    for (const list of days.values()) {
      list.sort((a, b) => b.z - a.z || a._k - b._k);
      list.forEach((g, r) => (g.start = r < cfg.slots));
    }
    return pg.sort((a, b) => (a.d < b.d ? -1 : a.d > b.d ? 1 : b.z - a.z));
  }

  function startSit(rows, ids, schedule, today, includeToday, cfg) {
    const map = byId(rows);
    const lineup = dailyLineups(rows, ids, schedule, today, includeToday, cfg);
    const out = [];
    for (const id of ids) {
      const p = map.get(id); if (!p) continue;
      const mine = lineup.filter((g) => g.id === id);
      const started = mine.filter((g) => g.start).length;
      const wk = {}; for (const c of CATS) wk[c] = p.z[c] * started;
      out.push({ id, name: p.name, team: p.team, active: p.active, z: p.total, games: mine.length, started,
        benched: mine.length - started, weekly: p.total * mine.length, weeklyStarted: p.total * started, wk });
    }
    return out.sort((a, b) => b.weeklyStarted - a.weeklyStarted);
  }

  // ------------------------------------------------------------------ waivers
  function rankWaivers(rows, myIds, rostered, cfg, { punt = [], gamesLeft = null, onlyActive = true } = {}) {
    const map = byId(rows);
    const mine = myIds.filter((i) => map.has(i));
    const taken = new Set([...rostered, ...mine]);
    const w = needWeights(teamCategoryZ(map, mine), cfg, punt);
    const fitOf = (p) => sum(CATS.map((c) => w[c] * p.z[c]));
    let worst = null;
    for (const id of mine) { const f = fitOf(map.get(id)); if (!worst || f < worst.f) worst = { id, f }; }
    const out = [];
    for (const p of rows) {
      if (taken.has(p.id) || (onlyActive && !p.active)) continue;
      const contrib = CATS.map((c) => [c, w[c] * p.z[c]]).sort((a, b) => b[1] - a[1]);
      const r = { id: p.id, name: p.name, team: p.team, z: p.z, total: p.total, fit: fitOf(p),
        helps: contrib.slice(0, 3).filter(([, v]) => v > 0).map(([c]) => c) };
      if (worst) { r.drop = map.get(worst.id).name; r.dropId = worst.id; r.netGain = r.fit - worst.f; }
      if (gamesLeft) { r.games = gamesLeft.get(p.id) || 0; r.weeklyFit = r.fit * r.games; }
      out.push(r);
    }
    const key = gamesLeft ? "weeklyFit" : "fit";
    return { rows: out.sort((a, b) => b[key] - a[key]), weights: w };
  }

  // ------------------------------------------------------------------ trades
  function analyzeTrade(rows, myIds, give, get, cfg, { punt = [], theirIds = null, autoDrop = true } = {}) {
    const map = byId(rows);
    const notMine = give.filter((i) => !myIds.includes(i));
    if (notMine.length) throw new Error("Can't trade players not on my roster");
    let after = myIds.filter((i) => !give.includes(i)).concat(get.filter((i) => !myIds.includes(i)));
    let dropped = [];
    const excess = after.length - myIds.length;
    if (autoDrop && excess > 0) {
      dropped = after.filter((i) => !get.includes(i)).map((i) => map.get(i))
        .sort((a, b) => a.total - b.total).slice(0, excess).map((p) => p.id);
      after = after.filter((i) => !dropped.includes(i));
    }
    const side = (before, aft, pnt) => {
      const tb = teamTotals(map, before), ta = teamTotals(map, aft);
      const zb = teamCategoryZ(map, before), za = teamCategoryZ(map, aft);
      const cats = CATS.map((c) => ({ cat: c, before: tb[c], after: ta[c], delta: ta[c] - tb[c],
        zBefore: zb[c], zAfter: za[c], zDelta: za[c] - zb[c] }));
      const total = sum(cats.filter((r) => !pnt.includes(r.cat)).map((r) => r.zDelta));
      return { cats, total, zb };
    };
    const me = side(myIds, after, punt);
    const w = needWeights(me.zb, cfg, punt);
    const live = me.cats.filter((r) => !punt.includes(r.cat));
    const res = { cats: me.cats, total: me.total, needWeighted: sum(me.cats.map((r) => r.zDelta * w[r.cat])),
      improved: live.filter((r) => r.zDelta > 0).map((r) => r.cat), hurt: live.filter((r) => r.zDelta < 0).map((r) => r.cat),
      rosterChange: after.length - myIds.length, dropped };
    if (theirIds && theirIds.length) {
      const theirAfter = theirIds.filter((i) => !get.includes(i)).concat(give.filter((i) => !theirIds.includes(i)));
      res.theirTotal = side(theirIds, theirAfter, []).total;
    }
    return res;
  }

  // ------------------------------------------------------------------ simulation
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function makeSampler(rand) {
    let spare = null;
    const normal = () => {
      if (spare !== null) { const s = spare; spare = null; return s; }
      let u = 0, v = 0; while (u === 0) u = rand(); v = rand();
      const r = Math.sqrt(-2 * Math.log(u)); spare = r * Math.sin(2 * Math.PI * v); return r * Math.cos(2 * Math.PI * v);
    };
    // Marsaglia & Tsang (shape >= 1, which holds because CV <= 0.6 -> shape >= 2.8)
    const gamma = (k) => {
      const d = k - 1 / 3, c = 1 / Math.sqrt(9 * d);
      for (;;) {
        let x, v; do { x = normal(); v = 1 + c * x; } while (v <= 0);
        v = v * v * v; const u = rand();
        if (u < 1 - 0.0331 * x ** 4 || Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
      }
    };
    const poisson = (lam) => {
      if (lam <= 0) return 0;
      if (lam > 30) return Math.max(0, Math.round(lam + Math.sqrt(lam) * normal()));
      const L = Math.exp(-lam); let k = 0, p = 1;
      do { k++; p *= rand(); } while (p > L);
      return k - 1;
    };
    const binomial = (n, p) => { let k = 0; for (let i = 0; i < n; i++) if (rand() < p) k++; return k; };
    return { gamma, poisson, binomial };
  }

  function simulateTeam(games, map, nSims, cfg, S) {
    const keys = ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV", "FGM", "FGA", "FTM", "FTA"];
    const out = Object.fromEntries(keys.map((k) => [k, new Float64Array(nSims)]));
    const g = games.map(({ id }) => {
      const p = map.get(id);
      const cv = Math.max(p.cv ?? cfg.cv_default, 1e-6);
      return { p, k: 1 / (cv * cv),
        fgp: p.fga > 0 ? Math.min(Math.max(p.fgm / p.fga, 0), 1) : 0,
        ftp: p.fta > 0 ? Math.min(Math.max(p.ftm / p.fta, 0), 1) : 0,
        share3: p.fgm > 0 ? Math.min(Math.max(p.fg3m / p.fgm, 0), 1) : 0 };
    });
    for (let s = 0; s < nSims; s++) {
      for (const x of g) {
        const shock = S.gamma(x.k) / x.k;
        const p = x.p;
        out.REB[s] += S.poisson(shock * p.reb); out.AST[s] += S.poisson(shock * p.ast);
        out.STL[s] += S.poisson(shock * p.stl); out.BLK[s] += S.poisson(shock * p.blk);
        out.TOV[s] += S.poisson(shock * p.tov);
        const fga = S.poisson(shock * p.fga), fgm = S.binomial(fga, x.fgp), m3 = S.binomial(fgm, x.share3);
        const fta = S.poisson(shock * p.fta), ftm = S.binomial(fta, x.ftp);
        out.FGA[s] += fga; out.FGM[s] += fgm; out.FG3M[s] += m3; out.FTA[s] += fta; out.FTM[s] += ftm;
        out.PTS[s] += 2 * fgm + m3 + ftm;
      }
    }
    return out;
  }

  function weekToDate(recent, ids, weekStart, simStart) {
    const out = { PTS: 0, REB: 0, AST: 0, STL: 0, BLK: 0, FG3M: 0, TOV: 0, FGM: 0, FGA: 0, FTM: 0, FTA: 0 };
    if (simStart <= weekStart) return out;
    const set = new Set(ids);
    for (const r of recent) {
      if (!set.has(r.id) || r.d < weekStart || r.d >= simStart) continue;
      for (const k of Object.keys(out)) out[k] += r[k.toLowerCase()];
    }
    return out;
  }

  function simulateMatchup(rows, myIds, oppIds, schedule, today, cfg, opts = {}) {
    const { nSims = cfg.sim_count, seed = cfg.sim_seed ?? 42, includeToday = true, excludeInactive = true,
      useWeekToDate = true, recent = [] } = opts;
    const map = byId(rows);
    const S = makeSampler(mulberry32(seed));
    const { start, weekStart } = weekWindow(today, includeToday, cfg);
    const side = (ids) => {
      let use = ids.filter((i) => map.has(i));
      if (excludeInactive) use = use.filter((i) => map.get(i).active);
      const games = dailyLineups(rows, use, schedule, today, includeToday, cfg).filter((g) => g.start);
      const t = simulateTeam(games, map, nSims, cfg, S);
      if (useWeekToDate) { const b = weekToDate(recent, ids, weekStart, start); for (const k in t) for (let s = 0; s < nSims; s++) t[k][s] += b[k]; }
      const v = {};
      for (const c of CATS) {
        if (c in COUNT) v[c] = t[c];
        else { const [m, a] = c === "FG_PCT" ? ["FGM", "FGA"] : ["FTM", "FTA"]; v[c] = t[m].map((x, s) => (t[a][s] > 0 ? x / t[a][s] : 0)); }
      }
      return { v, games: games.length };
    };
    const me = side(myIds), opp = side(oppIds);
    const points = new Float64Array(nSims);
    const cats = CATS.map((c) => {
      let win = 0, tie = 0, sm = 0, so = 0; const diffs = new Float64Array(nSims);
      for (let s = 0; s < nSims; s++) {
        let d = me.v[c][s] - opp.v[c][s]; if (NEG.has(c)) d = -d;
        diffs[s] = d; sm += me.v[c][s]; so += opp.v[c][s];
        if (d > 0) { win++; points[s] += 1; } else if (d === 0) { tie++; points[s] += cfg.tie_value; }
      }
      diffs.sort();
      const median = nSims % 2 ? diffs[(nSims - 1) / 2] : (diffs[nSims / 2 - 1] + diffs[nSims / 2]) / 2;
      const pWin = win / nSims, pTie = tie / nSims;
      return { cat: c, my: sm / nSims, opp: so / nSims, pWin, pTie, pLoss: 1 - pWin - pTie, median,
        close: pWin >= cfg.close_band[0] && pWin <= cfg.close_band[1] };
    });
    const half = CATS.length / 2;
    let win = 0, tie = 0; const dist = new Map();
    for (const x of points) { if (x > half) win++; else if (x === half) tie++; dist.set(x, (dist.get(x) || 0) + 1); }
    const expMy = sum(cats.map((r) => r.pWin + cfg.tie_value * r.pTie));
    return {
      cats, expected: [expMy, CATS.length - expMy], pWin: win / nSims, pTie: tie / nSims,
      dist: [...dist.entries()].sort((a, b) => a[0] - b[0]).map(([k, n]) => [k, n / nSims]),
      targets: cats.filter((r) => r.close).sort((a, b) => Math.abs(a.pWin - 0.5) - Math.abs(b.pWin - 0.5)).map((r) => r.cat),
      myGames: me.games, oppGames: opp.games, nSims,
    };
  }

  // ------------------------------------------------------------------ name matching (paste a roster)
  const norm = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
  function matchNames(lines, rows) {
    const index = new Map(rows.map((p) => [norm(p.name), p.id]));
    const found = [], missing = [];
    for (const raw of lines) {
      const q = norm(raw.replace(/\(.*?\)|\b(PG|SG|SF|PF|C|G|F|UTIL|IL|BN|DTD|O|INJ|GTD)\b/g, " "));
      if (!q) continue;
      let id = index.get(q);
      if (id === undefined) {
        const hits = rows.filter((p) => norm(p.name).includes(q) || q.includes(norm(p.name)));
        if (hits.length === 1) id = hits[0].id;
      }
      if (id === undefined) missing.push(raw.trim()); else if (!found.includes(id)) found.push(id);
    }
    return { found, missing };
  }

  return { CATS, COUNT, PCT, poolPct, catValues, fitPool, valuePlayers, teamTotals, teamCategoryZ, needWeights,
    weekWindow, addDays, gamesRemaining, dailyLineups, startSit, rankWaivers, analyzeTrade, simulateMatchup,
    matchNames, norm, byId };
});
