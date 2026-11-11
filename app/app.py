"""Interactive calibration explorer. Reads only app/data/snapshots.parquet (written by `make eval`),
so it runs anywhere without API access or the DuckDB file."""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from calib import metrics as M  # noqa: E402

DATA = ROOT / "app" / "data" / "snapshots.parquet"
COLORS = {"Kalshi": "#2a78d6", "Polymarket": "#eb6834"}
LIQ_ORDER = ["<1k", "1k-10k", "10k-100k", ">=100k", "unknown"]

st.set_page_config(page_title="Prediction-market calibration", layout="wide")


@st.cache_data
def load() -> pd.DataFrame:
    df = pd.read_parquet(DATA)
    df["platform"] = df["source"].map({"kalshi": "Kalshi", "polymarket": "Polymarket"})
    return df


@st.cache_data
def summarize(df: pd.DataFrame, n_boot: int) -> pd.DataFrame:
    rows = []
    for plat, g in df.groupby("platform"):
        p, y, c = g["p"].to_numpy(), g["outcome"].to_numpy(float), g["event_id"].to_numpy()
        row: dict[str, object] = {
            "Platform": plat,
            "Markets": len(g),
            "Events": len(set(c)),
            "Base rate": f"{y.mean():.1%}",
        }
        for name, fn, fmt in (
            ("Brier", M.brier, ".4f"),
            ("Log loss", M.log_loss, ".3f"),
            ("ECE", M.ece, ".3f"),
            ("Skill (BSS)", M.brier_skill_score, "+.3f"),
        ):
            e = M.cluster_bootstrap(fn, p, y, c, n_boot=n_boot, seed=0)
            row[name] = f"{e.point:{fmt}}  [{e.lo:{fmt}}, {e.hi:{fmt}}]"
        rows.append(row)
    return pd.DataFrame(rows)


def curve_frame(df: pd.DataFrame, n_bins: int) -> pd.DataFrame:
    parts = []
    for plat, g in df.groupby("platform"):
        p, y = g["p"].to_numpy(), g["outcome"].to_numpy(float)
        c = M.reliability_curve(p, y, n_bins)
        # Wilson-style binomial band per bin for the explorer (the report uses the clustered one).
        se = np.sqrt(np.clip(c.freq * (1 - c.freq), 1e-9, None) / c.count)
        parts.append(
            pd.DataFrame(
                {
                    "platform": plat,
                    "price": c.mean_p,
                    "freq": c.freq,
                    "lo": np.clip(c.freq - 1.96 * se, 0, 1),
                    "hi": np.clip(c.freq + 1.96 * se, 0, 1),
                    "n": c.count,
                }
            )
        )
    return pd.concat(parts) if parts else pd.DataFrame()


df = load()

st.title("Are prediction-market prices calibrated?")
st.caption(
    "Resolved binary markets on Kalshi and Polymarket, priced at a fixed horizon before "
    "their scheduled close. If prices are probabilities, the dots sit on the diagonal."
)

with st.container():
    c1, c2, c3, c4 = st.columns(4)
    platforms = c1.multiselect(
        "Platform", sorted(df["platform"].unique()), default=sorted(df["platform"].unique())
    )
    horizon = c2.selectbox("Horizon", ["T-30d", "T-7d", "T-1d"], index=1)
    cats = c3.multiselect("Category", sorted(df["category"].unique()))
    liqs = c4.multiselect(
        "Volume before snapshot", [x for x in LIQ_ORDER if x in set(df["liquidity"])]
    )
    fixed = st.toggle("Fixed population only (markets priced at all three horizons)", value=False)

f = df[df["platform"].isin(platforms) & (df["horizon"] == horizon)]
if cats:
    f = f[f["category"].isin(cats)]
if liqs:
    f = f[f["liquidity"].isin(liqs)]
if fixed:
    f = f[f["all_horizons"]]

if len(f) < 20:
    st.warning(f"Only {len(f)} markets match these filters; widen them to see a curve.")
    st.stop()

n_bins = st.slider("Bins (equal count)", 5, 20, 10)
cf = curve_frame(f, n_bins)
color = alt.Color(
    "platform:N",
    scale=alt.Scale(domain=list(COLORS), range=list(COLORS.values())),
    legend=alt.Legend(title=None, orient="top-left"),
)
base = alt.Chart(cf).encode(
    x=alt.X(
        "price:Q", title="Market price", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%")
    )
)
diag = (
    alt.Chart(pd.DataFrame({"x": [0, 1], "y": [0, 1]}))
    .mark_line(strokeDash=[4, 3], color="#888", strokeWidth=1)
    .encode(x="x:Q", y="y:Q")
)
band = base.mark_area(opacity=0.15).encode(y="lo:Q", y2="hi:Q", color=color)
line = base.mark_line(strokeWidth=2).encode(
    y=alt.Y(
        "freq:Q",
        title="Share resolved YES",
        scale=alt.Scale(domain=[0, 1]),
        axis=alt.Axis(format="%"),
    ),
    color=color,
)
pts = base.mark_circle(size=80, opacity=1, stroke="white", strokeWidth=2).encode(
    y="freq:Q",
    color=color,
    tooltip=[
        alt.Tooltip("platform:N", title="Platform"),
        alt.Tooltip("price:Q", title="Mean price", format=".1%"),
        alt.Tooltip("freq:Q", title="Resolved YES", format=".1%"),
        alt.Tooltip("n:Q", title="Markets"),
    ],
)

left, right = st.columns([3, 2])
with left:
    st.altair_chart((diag + band + line + pts).properties(height=460), use_container_width=True)
with right:
    st.subheader("Metrics")
    st.dataframe(summarize(f, 300).set_index("Platform").T, use_container_width=True)
    st.caption(
        "95% intervals resample whole events (a ladder of related markets moves together). "
        "BSS compares against always forecasting the group's base rate. Log loss clips "
        "prices to [0.0001, 0.9999]."
    )

st.subheader("Sample of markets")
show = f.sample(min(200, len(f)), random_state=0)[
    ["platform", "title", "category", "p", "outcome", "liquidity", "price_type"]
].rename(columns={"p": "price", "outcome": "resolved YES"})
st.dataframe(
    show,
    use_container_width=True,
    hide_index=True,
    column_config={"price": st.column_config.NumberColumn(format="%.3f")},
)
