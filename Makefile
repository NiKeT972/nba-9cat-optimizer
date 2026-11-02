PY ?= python
PRIOR := $(shell $(PY) -c "from src import config; print(config.PRIOR_SEASON)" 2>/dev/null)
BACKTEST := $(shell $(PY) -c "from src import config; print(config.BACKTEST_SEASON)" 2>/dev/null)
BEFORE_BACKTEST := $(shell $(PY) -c "from src import config; print(config.previous_season(config.BACKTEST_SEASON))" 2>/dev/null)

.PHONY: install test data backtest site app refresh

install:            ## install dependencies
	$(PY) -m pip install -r requirements.txt

test:               ## run the test suite
	$(PY) -m pytest -q

data:               ## download current + previous season (cached)
	$(PY) -m src.data.pipeline
	$(PY) -m src.data.pipeline --season $(PRIOR)

backtest:           ## walk-forward backtest of last season -> reports/
	$(PY) -m src.data.pipeline --season $(BACKTEST)
	-$(PY) -m src.data.pipeline --season $(BEFORE_BACKTEST)
	$(PY) -m src.projections.backtest --season $(BACKTEST)

site:               ## build the static Vercel site into public/
	$(PY) -m app.static_site

app:                ## run the Streamlit app locally
	streamlit run app/streamlit_app.py

refresh:            ## force-refresh current data, make sure the prior season exists, rebuild the site
	$(PY) -m src.data.pipeline --force
	$(PY) -m src.data.pipeline --season $(PRIOR)
	$(PY) -m app.static_site
