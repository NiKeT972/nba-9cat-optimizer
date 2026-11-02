"""Streamlit entry point: `streamlit run app/streamlit_app.py`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os  # noqa: E402

import streamlit as st  # noqa: E402

# Streamlit Community Cloud passes settings as secrets. Copy simple top-level values into the
# environment *before* src.config is imported, so NBA_OFFLINE etc. take effect.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float, bool)):
            os.environ.setdefault(_k, str(_v))
except Exception:  # noqa: BLE001 - no secrets file locally is fine
    pass

from app.common import sidebar  # noqa: E402

st.set_page_config(page_title="9-Cat Optimizer", page_icon="🏀", layout="wide")

VIEWS = Path(__file__).parent / "views"
pages = [
    st.Page(VIEWS / "rankings.py", title="Player Rankings", icon=":material/leaderboard:", default=True),
    st.Page(VIEWS / "my_team.py", title="My Team", icon=":material/groups:"),
    st.Page(VIEWS / "waivers.py", title="Waivers", icon=":material/person_add:"),
    st.Page(VIEWS / "trade.py", title="Trade Analyzer", icon=":material/swap_horiz:"),
    st.Page(VIEWS / "matchup.py", title="Matchup Simulator", icon=":material/casino:"),
    st.Page(VIEWS / "about.py", title="Method & Backtest", icon=":material/science:"),
]
nav = st.navigation(pages)
sidebar()
nav.run()
