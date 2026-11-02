"""Build the static portfolio site (deployed on Vercel) into ``public/``.

    python -m app.static_site

Sections render only when their data exists (rankings need cached game logs, the
backtest section needs ``reports/backtest_<season>.csv``), so the site never shows
placeholder numbers. Charts are Plotly figures from :mod:`app.charts`, rendered with
plotly.js from a CDN, with separate light and dark layouts switched by the viewer's
colour-scheme preference.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

import pandas as pd

from app import charts
from src import config

REPO_URL = "https://github.com/NiKeT972/nba-9cat-optimizer"
PLOTLY_JS = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"


def _fig_json(fig) -> dict:
    """Light and dark variants of a figure as plain JSON for plotly.js."""
    light = json.loads(charts.style(fig, height=fig.layout.height or 380, dark=False).to_json())
    dark = json.loads(charts.style(fig, height=fig.layout.height or 380, dark=True).to_json())
    return {"light": light, "dark": dark}


def _rankings_section() -> tuple[str, dict | None]:
    try:
        from src.data.pipeline import load_game_logs
        from src.projections.project import project_players
        from src.valuation.zscores import value_players

        logs = load_game_logs(config.SEASON)
        try:
            prior = load_game_logs(config.PRIOR_SEASON)
        except Exception:  # noqa: BLE001
            prior = None
        if logs.empty:
            return "", None
        as_of = logs["date"].max() + pd.Timedelta(days=1)
        table, stats = value_players(project_players(logs, as_of=as_of, prior_logs=prior))
    except Exception as exc:  # noqa: BLE001
        print(f"Rankings section skipped: {exc}")
        return "", None

    top = table[table["active"]].head(config.SITE_TOP_N)
    cats = config.CATEGORIES
    head = "".join(f"<th>{config.CAT_LABELS[c]}</th>" for c in cats)
    rows = []
    for rank, (_, r) in enumerate(top.iterrows(), start=1):
        cells = "".join(
            f'<td class="z" style="{charts.z_cell_color(r[f"z_{c}"])}">{r[f"z_{c}"]:+.2f}</td>' for c in cats)
        rows.append(
            f"<tr><td class='num'>{rank}</td><td class='name'>{html.escape(str(r['name']))}</td>"
            f"<td>{html.escape(str(r['team']))}</td><td class='num'>{r['min']:.1f}</td>"
            f'<td class="z total" style="{charts.z_cell_color(r["z_total"] / 3)}">{r["z_total"]:+.2f}</td>{cells}</tr>')
    meta = {
        "players": int(len(table)),
        "through": logs["date"].max().strftime("%b %d, %Y"),
        "fg": stats.pool_pct["FG_PCT"],
        "ft": stats.pool_pct["FT_PCT"],
    }
    section = f"""
<section id="rankings">
  <h2>Top {config.SITE_TOP_N} by per-game value</h2>
  <p class="lede">{config.SEASON} projections, z-scored against the top {config.ZSCORE_POOL_SIZE} players.
  Data through {meta['through']}. Pool FG% {meta['fg']:.3f}, FT% {meta['ft']:.3f}.</p>
  <div class="table-wrap"><table>
    <thead><tr><th>#</th><th>Player</th><th>Team</th><th>MPG</th><th>Value</th>{head}</tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
  <p class="note">Blue = better than the typical rostered player, red = worse; deeper tint = further from average.</p>
</section>"""
    return section, meta


def _backtest_section() -> tuple[str, dict | None]:
    path = config.REPORTS_DIR / f"backtest_{config.BACKTEST_SEASON}.csv"
    if not path.exists():
        return "", None
    summary = pd.read_csv(path, index_col=0)
    fig = charts.backtest_improvement_bars(summary)
    rows = []
    for cat, r in summary.rename(index=config.CAT_LABELS).iterrows():
        pct = cat.endswith("%")
        f = (lambda v: f"{v * 100:.2f} pp") if pct else (lambda v: f"{v:.3f}")
        rows.append(f"<tr><td>{cat}</td><td class='num'>{f(r['blend'])}</td><td class='num'>{f(r['season_avg'])}</td>"
                    f"<td class='num'>{f(r['last7_avg'])}</td><td class='num'>{r['blend_vs_season_avg_%']:+.1f}%</td></tr>")
    section = f"""
<section id="backtest">
  <h2>How accurate are the projections?</h2>
  <p class="lede">Walk-forward backtest on the {config.BACKTEST_SEASON} season: every week, project each player from
  games <em>before</em> that date, then score against what he averaged the following week.</p>
  <div class="chart" id="chart-backtest"></div>
  <div class="table-wrap"><table>
    <thead><tr><th>Category</th><th>MAE: model</th><th>MAE: season avg</th><th>MAE: last 7 days</th><th>Model vs season avg</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
  <p class="note">Players projected for at least {config.BACKTEST_MIN_MINUTES:.0f} minutes. FG%/FT% errors are
  attempt-weighted, in percentage points.</p>
</section>"""
    return section, {"chart-backtest": _fig_json(fig)}


METHOD = f"""
<section id="method">
  <h2>How it works</h2>
  <ol class="steps">
    <li><h3>Project</h3><p>Per-minute rates from the last 7 days, last 15 days and the season, blended
      {" / ".join(f"{v:.0%}" for v in config.PROJ_WEIGHTS.values())} and multiplied by projected minutes.
      Small samples are shrunk toward last season (or league average for rookies), so a hot first week
      doesn't make a 40-point scorer.</p></li>
    <li><h3>Value</h3><p>Each category becomes a z-score against the top {config.ZSCORE_POOL_SIZE} players
      ({config.NUM_TEAMS} teams × {config.ROSTER_SPOTS}), re-selected until the pool is stable. Turnovers are
      flipped. FG% and FT% use <code>(player% − league%) × attempts</code>, so volume counts. Punt mode drops
      chosen categories.</p></li>
    <li><h3>Decide</h3><p>Start/sit multiplies value by games you can start this week. Waivers weight each
      category by how much <em>your</em> team needs it. Trades compare team category totals before and after.</p></li>
    <li><h3>Simulate</h3><p>{config.SIM_COUNT:,} Monte Carlo weeks of the head-to-head: a shared "role shock" per
      player-game times Poisson counting stats, and shots drawn as attempts then makes. Output: win probability
      per category, expected score and the swing categories to target.</p></li>
  </ol>
</section>"""

CSS = """
:root{color-scheme:light;--bg:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#6f6d68;
--line:#e1e0d9;--accent:#2a78d6;--accent-ink:#ffffff;--code:#f0efec}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0d0d0d;--surface:#1a1a19;
--ink:#f0efec;--ink2:#c3c2b7;--muted:#a3a29b;--line:#2c2c2a;--accent:#3987e5;--accent-ink:#ffffff;--code:#262624}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--muted:#a3a29b;--line:#2c2c2a;--accent:#3987e5;--accent-ink:#ffffff;--code:#262624}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:56px 16px 80px}
header h1{font-size:clamp(32px,5vw,48px);line-height:1.1;margin:0 0 12px;letter-spacing:-0.02em}
header p{color:var(--ink2);font-size:18px;max-width:680px;margin:0 0 24px}
.links{display:flex;gap:12px;flex-wrap:wrap}
.btn{display:inline-block;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;border:1px solid var(--line);color:var(--ink)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:40px 0 0}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px}
.kpi b{display:block;font-size:28px;font-weight:600}
.kpi span{color:var(--muted);font-size:14px}
section{margin-top:64px}
h2{font-size:26px;margin:0 0 8px;letter-spacing:-0.01em}
h3{font-size:17px;margin:0 0 6px}
.lede{color:var(--ink2);margin:0 0 20px;max-width:760px}
.note{color:var(--muted);font-size:14px;margin-top:10px}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--surface)}
td.num,td.z{text-align:right}
td.total{font-weight:600}
tbody tr:last-child td{border-bottom:none}
.chart{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:8px;margin-bottom:16px;min-height:300px}
.steps{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;counter-reset:s}
.steps li{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px;counter-increment:s}
.steps li h3::before{content:counter(s) " · ";color:var(--accent)}
.steps p{color:var(--ink2);margin:0;font-size:15px}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:0.9em}
footer{margin-top:72px;color:var(--muted);font-size:14px;border-top:1px solid var(--line);padding-top:20px}
footer a{color:inherit}
"""

JS = """
const FIGS = __FIGS__;
const dark = () => document.documentElement.dataset.theme === 'dark' ||
  (document.documentElement.dataset.theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
function draw(){
  if (!window.Plotly) return;
  for (const [id, f] of Object.entries(FIGS)) {
    const el = document.getElementById(id); if (!el) continue;
    const fig = dark() ? f.dark : f.light;
    Plotly.react(el, fig.data, fig.layout, {displayModeBar:false, responsive:true});
  }
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
window.addEventListener('load', draw);
"""


def build() -> str:
    """Write ``public/index.html`` and return its path. Never downloads: reads the cache only."""
    config.OFFLINE = True
    rankings_html, meta = _rankings_section()
    backtest_html, figs = _backtest_section()
    app_link = (f'<a class="btn primary" href="{html.escape(config.STREAMLIT_APP_URL)}">Open the app</a>'
                if config.STREAMLIT_APP_URL else "")
    kpis = ""
    if meta:
        kpis = f"""<div class="kpis">
  <div class="kpi"><b>{meta['players']}</b><span>players projected</span></div>
  <div class="kpi"><b>{config.ZSCORE_POOL_SIZE}</b><span>player valuation pool</span></div>
  <div class="kpi"><b>{config.SIM_COUNT:,}</b><span>simulated weeks per matchup</span></div>
  <div class="kpi"><b>{meta['through']}</b><span>data through</span></div>
</div>"""
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NBA 9-Cat Optimizer</title>
<meta name="description" content="Projections, z-score valuation and Monte Carlo matchup simulation for 9-category head-to-head fantasy basketball.">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏀</text></svg>">
<style>{CSS}</style>
{f'<script src="{PLOTLY_JS}" defer></script>' if figs else ''}
</head>
<body>
<main>
<header>
  <h1>NBA 9-Cat Optimizer</h1>
  <p>A data-science toolkit for 12-team, 9-category head-to-head fantasy basketball: player projections,
  z-score valuation, start/sit, waiver and trade tools, and a Monte Carlo simulation of each weekly matchup.</p>
  <div class="links">{app_link}<a class="btn" href="{REPO_URL}">Source on GitHub</a></div>
  {kpis}
</header>
{rankings_html}
{backtest_html}
{METHOD}
<footer>Built {built} from <a href="{REPO_URL}">{REPO_URL.removeprefix('https://')}</a> ·
Python, pandas, NumPy, scikit-learn, Plotly, Streamlit · Data: stats.nba.com via nba_api.</footer>
</main>
{f'<script>{JS.replace("__FIGS__", json.dumps(figs))}</script>' if figs else ''}
</body>
</html>"""
    config.SITE_DIR.mkdir(parents=True, exist_ok=True)
    out = config.SITE_DIR / "index.html"
    out.write_text(page)
    return str(out)


if __name__ == "__main__":
    print("Wrote", build())
