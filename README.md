# NBA 9-Cat Optimizer

Projections, z-score valuation, start/sit, waiver, trade and Monte Carlo matchup tools for
12-team, 9-category head-to-head fantasy basketball.

> Work in progress — built in phases. A full write-up arrives in Phase 6.

## Quick start

```bash
pip install -r requirements.txt
python -m src.data.pipeline          # download + cache 2026-27 game logs and schedule
python -m pytest
```

## Layout

```
src/config.py        all tunable numbers (weights, pool size, sim count, API settings)
src/data/            nba_api fetch with retry + parquet cache, cleaning into tidy tables
src/projections/     (phase 2)
src/valuation/       (phase 3)
src/simulation/      (phase 5)
app/                 (phase 6) Streamlit app
tests/               pytest
```
