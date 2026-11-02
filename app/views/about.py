"""Method & Backtest: how the numbers are made and how accurate the projections are."""

import pandas as pd
import streamlit as st

from app.charts import backtest_improvement_bars
from src import config

st.title("Method & Backtest")

st.markdown(
    f"""
### 1 · Projections
Per-minute rates from three windows — last 7 days, last 15 days, season — blended with weights
{", ".join(f"**{k}** {v:.0%}" for k, v in config.PROJ_WEIGHTS.items())}, then × projected minutes.
Small samples are shrunk: the season rate toward last season (or league average for rookies) as if
the prior were worth {config.SHRINK_SEASON_PRIOR_MINUTES:.0f} minutes, and each short window toward the season rate.

### 2 · Z-scores
`z = (x − mean) / std` over the top {config.ZSCORE_POOL_SIZE} players ({config.NUM_TEAMS} teams ×
{config.ROSTER_SPOTS}), re-selecting the pool until it is stable. Turnovers are negated.
FG% and FT% use **impact** = `(player% − league%) × attempts`, so a 55% shooter on 20 attempts
beats a 70% shooter on 2.

### 3 · Decisions
* **Start/sit** – per-game value × games you can start this week ({config.ACTIVE_LINEUP_SLOTS} slots/day).
* **Waivers** – each category weighted by need, `w = 2 / (1 + e^(team_z / {config.WAIVER_NEED_SCALE:g}))`.
* **Trades** – team totals before/after, percentages from summed makes/attempts.

### 4 · Matchup simulation
{config.SIM_COUNT:,} simulated weeks. Each player-game draws a Gamma "role shock" (fitted to recent
minutes variability) that scales a Poisson draw for every counting stat; shots are attempts → makes
(binomial), and `PTS = 2·FGM + 3PM + FTM`.
"""
)

st.markdown(f"### 5 · Backtest ({config.BACKTEST_SEASON})")
path = config.REPORTS_DIR / f"backtest_{config.BACKTEST_SEASON}.csv"
if not path.exists():
    st.info(f"No backtest report yet. Run `python -m src.projections.backtest --season {config.BACKTEST_SEASON}`.")
else:
    summary = pd.read_csv(path, index_col=0)
    st.plotly_chart(backtest_improvement_bars(summary), use_container_width=True, theme="streamlit")
    md = path.with_suffix(".md")
    if md.exists():
        st.markdown(md.read_text())
