"""Plotly figures shared by the Streamlit app and the static portfolio site.

Colour roles (validated reference palette):
* identity  - me = blue, opponent = orange (the same in every chart)
* polarity  - good = blue, bad = red, neutral grey midpoint (z-scores, deltas)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from src import config

ME = "#2a78d6"
OPP = "#eb6834"
GOOD = "#2a78d6"
BAD = "#e34948"
NEUTRAL = "#898781"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

LIGHT = {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
         "grid": "#e1e0d9", "axis": "#c3c2b7", "band": "rgba(137,135,129,0.12)"}
DARK = {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835", "band": "rgba(195,194,183,0.10)"}


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def style(fig: go.Figure, height: int = 380, dark: bool = False, transparent: bool = True) -> go.Figure:
    """Apply the shared look: system font, recessive grid, transparent background, top legend."""
    t = DARK if dark else LIGHT
    bg = "rgba(0,0,0,0)" if transparent else t["surface"]
    fig.update_layout(
        height=height,
        font=dict(family=FONT, size=13, color=t["ink2"]),
        paper_bgcolor=bg,
        plot_bgcolor=bg,
        margin=dict(l=8, r=8, t=36, b=8),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=None),
        hoverlabel=dict(font=dict(family=FONT)),
        bargap=0.35,
    )
    fig.update_xaxes(gridcolor=t["grid"], linecolor=t["axis"], zerolinecolor=t["axis"], tickfont_color=t["muted"])
    fig.update_yaxes(gridcolor=t["grid"], linecolor=t["axis"], zerolinecolor=t["axis"], tickfont_color=t["muted"])
    fig.update_polars(bgcolor=bg, radialaxis=dict(gridcolor=t["grid"], linecolor=t["axis"], tickfont_color=t["muted"]),
                      angularaxis=dict(gridcolor=t["grid"], linecolor=t["axis"]))
    return fig


def category_radar(teams: dict[str, pd.Series], title: str = "") -> go.Figure:
    """Radar of team category strength (summed z-scores), one trace per team.

    Args:
        teams: ``{label: Series indexed by config.CATEGORIES}``. First = me, second = opponent.
    """
    labels = [config.CAT_LABELS[c] for c in config.CATEGORIES]
    colors = [ME, OPP]
    fig = go.Figure()
    lo = min(float(s.min()) for s in teams.values())
    hi = max(float(s.max()) for s in teams.values())
    pad = max(1.0, 0.1 * (hi - lo))
    for i, (name, s) in enumerate(teams.items()):
        vals = [float(s[c]) for c in config.CATEGORIES]
        color = colors[i % len(colors)]
        fig.add_trace(go.Scatterpolar(
            r=vals + vals[:1], theta=labels + labels[:1], name=name, mode="lines+markers",
            line=dict(color=color, width=2), marker=dict(size=8, color=color),
            fill="toself", fillcolor=_rgba(color, 0.12),
            hovertemplate="%{theta}: %{r:+.2f} z<extra>" + name + "</extra>",
        ))
    if lo < 0 < hi:
        fig.add_trace(go.Scatterpolar(
            r=[0] * (len(labels) + 1), theta=labels + labels[:1], name="League-average team", mode="lines",
            line=dict(color=NEUTRAL, width=1, dash="dot"), hoverinfo="skip",
        ))
    fig.update_layout(title=dict(text=title, x=0, font=dict(size=15)),
                      polar=dict(radialaxis=dict(range=[min(lo, 0) - pad, max(hi, 0) + pad], tickformat="+.0f",
                                                 angle=90, nticks=4, tickfont=dict(size=10))))
    style(fig, height=460)
    fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5),
                      margin=dict(t=48, b=48))
    return fig


def win_probability_bars(categories: pd.DataFrame) -> go.Figure:
    """Horizontal bars of P(win) per category, drawn from the 50% line.

    Blue = I'm favoured, orange = opponent favoured. The shaded band marks the
    'close' range worth targeting.
    """
    df = categories.iloc[::-1]
    p = df["p_win"] + config.SIM_TIE_VALUE * df["p_tie"]
    colors = [ME if v >= 0.5 else OPP for v in p]
    fig = go.Figure(go.Bar(
        y=df.index, x=p - 0.5, base=0.5, orientation="h", marker=dict(color=colors, cornerradius=4),
        customdata=np.column_stack([p, df["my_mean"], df["opp_mean"]]),
        hovertemplate="%{y}: %{customdata[0]:.0%} to win<br>"
                      "me %{customdata[1]:.3~f} vs opp %{customdata[2]:.3~f}<extra></extra>",
        showlegend=False,
    ))
    # Value labels sit just across the 50% line from their bar, so they never collide with the axis.
    fig.add_trace(go.Scatter(
        y=df.index, x=[0.5 - 0.015 if v >= 0.5 else 0.5 + 0.015 for v in p], mode="text",
        text=[f"{v:.0%}" for v in p], textposition=["middle left" if v >= 0.5 else "middle right" for v in p],
        hoverinfo="skip", showlegend=False,
    ))
    lo, hi = config.SIM_CLOSE_BAND
    fig.add_vrect(x0=lo, x1=hi, fillcolor="rgba(137,135,129,0.12)", line_width=0, layer="below")
    fig.add_vline(x=0.5, line=dict(color=NEUTRAL, width=1))
    fig.update_xaxes(range=[0, 1], tickformat=".0%", dtick=0.25, title=None)
    fig.update_yaxes(showgrid=False)
    fig.update_layout(title=dict(text="Chance to win each category", x=0, font=dict(size=15)))
    return style(fig, height=380)


def score_distribution_bars(dist: pd.Series) -> go.Figure:
    """P(I win exactly k categories). Blue = matchup win, orange = loss, grey = tie."""
    n = len(config.CATEGORIES)
    colors = [ME if k > n / 2 else OPP if k < n / 2 else NEUTRAL for k in dist.index]
    fig = go.Figure(go.Bar(
        x=[f"{k:g}" for k in dist.index], y=dist.values, marker=dict(color=colors, cornerradius=4),
        hovertemplate="%{x} categories: %{y:.1%}<extra></extra>", showlegend=False,
    ))
    fig.update_yaxes(tickformat=".0%", title=None)
    fig.update_xaxes(title="Categories I win", type="category")
    fig.update_layout(title=dict(text="Final score distribution", x=0, font=dict(size=15)))
    return style(fig, height=300)


def diverging_category_bars(values: pd.Series, title: str, fmt: str = "+.2f", suffix: str = "") -> go.Figure:
    """Bars per category coloured by sign (blue good, red bad), e.g. trade z-deltas or team strengths."""
    s = values.iloc[::-1]
    span = max(abs(float(s.min())), abs(float(s.max())), 1e-9) * 1.1
    fig = go.Figure(go.Bar(
        y=list(s.index), x=s.values, orientation="h",
        marker=dict(color=[GOOD if v >= 0 else BAD for v in s.values], cornerradius=4),
        hovertemplate="%{y}: %{x:" + fmt + "}" + suffix + "<extra></extra>", showlegend=False,
    ))
    # Labels sit just across the zero line from their bar, so they never collide with the axis labels.
    nudge = 0.02 * span
    fig.add_trace(go.Scatter(
        y=list(s.index), x=[-nudge if v >= 0 else nudge for v in s.values], mode="text",
        text=[f"{v:{fmt}}{suffix}" for v in s.values],
        textposition=["middle left" if v >= 0 else "middle right" for v in s.values],
        hoverinfo="skip", showlegend=False,
    ))
    fig.add_vline(x=0, line=dict(color=NEUTRAL, width=1))
    fig.update_yaxes(showgrid=False)
    fig.update_xaxes(range=[-span, span], title=None)
    fig.update_layout(title=dict(text=title, x=0, font=dict(size=15)))
    return style(fig, height=max(260, 34 * len(s) + 80))


def backtest_improvement_bars(summary: pd.DataFrame) -> go.Figure:
    """% MAE reduction of the blend model vs the naive season average, per category."""
    col = "blend_vs_season_avg_%"
    s = summary[col].rename(index=config.CAT_LABELS)
    return diverging_category_bars(s, "Error reduction vs. season average", fmt="+.1f", suffix="%")


def z_cell_color(v: float, cap: float = 2.5) -> str:
    """CSS background for a z-score table cell: blue tint above 0, red tint below, alpha by magnitude."""
    if pd.isna(v) or v == 0:
        return ""
    a = min(abs(v) / cap, 1.0) * 0.35
    return f"background-color: {_rgba(GOOD if v > 0 else BAD, a)}"
