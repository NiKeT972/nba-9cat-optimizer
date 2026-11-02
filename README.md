# NBA 9-Cat Optimizer

Projections, z-score valuation, start/sit, waiver, trade and Monte Carlo matchup tools for
12-team, 9-category head-to-head fantasy basketball.

> Work in progress — built in phases. A full write-up arrives in Phase 6.

## Quick start

```bash
pip install -r requirements.txt
python -m src.data.pipeline          # download + cache 2026-27 game logs and schedule
python -m src.data.pipeline --season 2025-26   # last season, used as the projection prior
python -m src.projections.project    # top-25 projections as of today
python -m src.valuation.zscores --punt FT_PCT TOV   # z-score rankings, optional punts
python -m pytest
```

## Layout

```
src/config.py        all tunable numbers (weights, pool size, sim count, API settings)
src/data/            nba_api fetch with retry + parquet cache, tidy tables, roster JSON
src/projections/     shrunk, weighted blend of last-7 / last-15 / season per-minute rates × projected minutes
src/valuation/       z-scores (top-156 pool, volume-weighted FG%/FT%, punts), start/sit, waivers, trades
src/simulation/      Monte Carlo H2H week: Gamma-Poisson counting stats, attempts->makes shooting
app/                 (phase 6) Streamlit app
tests/               pytest
```
