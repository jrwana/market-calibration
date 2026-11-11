"""Scorecard and figures. Run: ``python -m calib.report`` (``make eval``).

Writes ``reports/scorecard.json``, ``reports/figures/*.png`` and the small export the Streamlit app
reads (``app/data/snapshots.parquet``).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from calib import db  # noqa: E402
from calib import metrics as M  # noqa: E402
from calib.config import (  # noqa: E402
    APP_DATA_DIR,
    CLIP_EPS,
    FIGURES_DIR,
    HORIZONS,
    LIQUIDITY_LABELS,
    REPORTS_DIR,
    SEED,
)

N_BOOT = 1000
N_BINS = 10
MIN_GROUP = 30  # don't report a breakdown cell with fewer markets than this

# Reference palette (dataviz skill): categorical slots 1-2, light surface, recessive ink.
COLORS = {"kalshi": "#2a78d6", "polymarket": "#eb6834"}
LABELS = {"kalshi": "Kalshi", "polymarket": "Polymarket"}
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
HORIZON_ORDER = list(HORIZONS)

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK_2,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "text.color": INK,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "legend.frameon": False,
    }
)


def load(con: Any) -> pd.DataFrame:
    df: pd.DataFrame = con.execute("""
        SELECT s.*, m.event_id, m.category, m.outcome, m.title,
               m.scheduled_close_ts
        FROM snapshots s JOIN markets m USING (source, market_id)
    """).df()
    # Fixed population: markets that have a snapshot at every horizon. Horizons otherwise compare
    # different kinds of market (short-lived ones can't have a T-30d price).
    n_h = df.groupby(["source", "market_id"])["horizon"].transform("nunique")
    df["all_horizons"] = n_h == len(HORIZONS)
    return df


def evaluate(g: pd.DataFrame, n_boot: int = N_BOOT) -> dict[str, Any]:
    p = g["p"].to_numpy(float)
    y = g["outcome"].to_numpy(float)
    c = g["event_id"].to_numpy()
    out: dict[str, Any] = {
        "n_markets": int(p.size),
        "n_events": int(np.unique(c).size),
        "base_rate": float(y.mean()),
        "mean_p": float(p.mean()),
    }
    for name, fn in (
        ("brier", M.brier),
        ("log_loss", M.log_loss),
        ("ece", M.ece),
        ("bss", M.brier_skill_score),
    ):
        e = M.cluster_bootstrap(fn, p, y, c, n_boot=n_boot, seed=SEED)
        out[name] = {"value": e.point, "ci95": [e.lo, e.hi]}
    out["murphy"] = asdict(M.murphy(p, y, N_BINS))
    return out


def grouped(df: pd.DataFrame, keys: list[str], n_boot: int = N_BOOT // 2) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for k, g in df.groupby(keys, sort=True):
        if len(g) < MIN_GROUP:
            continue
        key = dict(zip(keys, k if isinstance(k, tuple) else (k,), strict=True))
        rows.append({**key, **evaluate(g, n_boot)})
    return rows


def one_per_event(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for _, g in df.groupby(["source", "horizon"]):
        idx = M.one_per_cluster(g["event_id"].to_numpy(), seed=SEED)
        parts.append(g.iloc[idx])
    return pd.concat(parts)


# ---------------------------------------------------------------- figures


def _diag(ax: Any) -> None:
    ax.plot([0, 1], [0, 1], color=INK_2, lw=1, ls=(0, (4, 3)), zorder=1)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")


def fig_reliability(df: pd.DataFrame, path_name: str, title: str) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.8), sharey=True)
    for ax, h in zip(axes, HORIZON_ORDER, strict=True):
        _diag(ax)
        for src in ("kalshi", "polymarket"):
            g = df[(df.source == src) & (df.horizon == h)]
            if len(g) < MIN_GROUP:
                continue
            p, y, c = g["p"].to_numpy(), g["outcome"].to_numpy(float), g["event_id"].to_numpy()
            curve = M.reliability_curve(p, y, N_BINS)
            # Per-bin 95% intervals from the event-clustered bootstrap.
            bins = M.equal_count_bins(p, N_BINS)
            lo, hi = [], []
            for b in bins:
                e = M.cluster_bootstrap(
                    lambda _p, _y: float(_y.mean()), p[b], y[b], c[b], n_boot=300, seed=SEED
                )
                lo.append(e.lo)
                hi.append(e.hi)
            color = COLORS[src]
            ax.fill_between(curve.mean_p, lo, hi, color=color, alpha=0.15, lw=0, zorder=2)
            ax.plot(curve.mean_p, curve.freq, color=color, lw=2, zorder=3)
            ax.scatter(
                curve.mean_p,
                curve.freq,
                s=36,
                color=color,
                edgecolor=SURFACE,
                linewidth=2,
                zorder=4,
                label=f"{LABELS[src]} (n={len(g):,})",
            )
        ax.set_title(h)
        ax.set_xlabel("Market price (implied probability)")
        ax.legend(loc="upper left", fontsize=9)
    axes[0].set_ylabel("Share that resolved YES")
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold")
    fig.text(
        0.01,
        0.005,
        "Equal-count bins; bands are 95% event-clustered bootstrap intervals. "
        "Dashed line = perfect calibration.",
        color=INK_2,
        fontsize=8.5,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.97))
    fig.savefig(FIGURES_DIR / path_name, dpi=150)
    plt.close(fig)


def fig_favorite_longshot(df: pd.DataFrame) -> list[dict[str, Any]]:
    edges = np.linspace(0, 1, 11)
    centers = (edges[:-1] + edges[1:]) / 2
    fig, ax = plt.subplots(figsize=(8, 4.3))
    ax.axhline(0, color=INK_2, lw=1)
    rows: list[dict[str, Any]] = []
    width = 0.035
    for j, src in enumerate(("kalshi", "polymarket")):
        g = df[df.source == src]
        p, y, c = g["p"].to_numpy(), g["outcome"].to_numpy(float), g["event_id"].to_numpy()
        fl = M.favorite_longshot(p, y, edges)
        for k in range(len(centers)):
            mask = (p >= edges[k]) & ((p < edges[k + 1]) | (k == len(centers) - 1))
            if mask.sum() < MIN_GROUP:
                continue
            e = M.cluster_bootstrap(
                lambda _p, _y: float(_y.mean() - _p.mean()),
                p[mask],
                y[mask],
                c[mask],
                n_boot=500,
                seed=SEED,
            )
            x = centers[k] + (j - 0.5) * width * 1.15
            ax.bar(
                x,
                e.point,
                width=width,
                color=COLORS[src],
                zorder=3,
                label=LABELS[src],
            )
            ax.vlines(x, e.lo, e.hi, color=INK_2, lw=1.2, zorder=4)
            rows.append(
                {
                    "source": src,
                    "bin": [float(edges[k]), float(edges[k + 1])],
                    "n": int(fl.count[k]),
                    "gap": e.point,
                    "ci95": [e.lo, e.hi],
                }
            )
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles, strict=False))
    ax.legend(uniq.values(), uniq.keys(), loc="upper left")
    ax.set_xticks(edges)
    ax.set_xticklabels([f"{e:.0%}" for e in edges])
    ax.set_xlabel("Price bin")
    ax.set_ylabel("Realized frequency − price")
    ax.set_title("Favorite–longshot check (all horizons pooled)", loc="left")
    fig.text(
        0.01,
        0.005,
        "Below zero = contracts in that bin were overpriced. Whiskers: 95% "
        "event-clustered bootstrap. Bins with < 30 markets omitted.",
        color=INK_2,
        fontsize=8.5,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.97))
    fig.savefig(FIGURES_DIR / "favorite_longshot.png", dpi=150)
    plt.close(fig)
    return rows


def fig_breakdown(
    rows: list[dict[str, Any]], key: str, order: list[str], name: str, title: str
) -> None:
    """Dot plot of Brier skill score with CIs, one row per group, platforms side by side."""
    data = [r for r in rows if r["horizon"] == "T-7d"]
    present = [o for o in order if any(r[key] == o for r in data)]
    if not present:
        return
    fig, ax = plt.subplots(figsize=(8, 0.5 * len(present) + 1.4))
    ax.axvline(0, color=INK_2, lw=1)
    for j, src in enumerate(("kalshi", "polymarket")):
        for i, grp in enumerate(present):
            r = next((r for r in data if r[key] == grp and r["source"] == src), None)
            if r is None:
                continue
            yv = i + (j - 0.5) * 0.28
            lo, hi = r["bss"]["ci95"]
            ax.hlines(yv, lo, hi, color=COLORS[src], lw=2, zorder=3)
            ax.scatter(
                r["bss"]["value"],
                yv,
                s=40,
                color=COLORS[src],
                edgecolor=SURFACE,
                linewidth=2,
                zorder=4,
                label=LABELS[src],
            )
            ax.annotate(
                f"n={r['n_markets']}",
                (hi, yv),
                xytext=(4, 0),
                textcoords="offset points",
                va="center",
                fontsize=8,
                color=INK_2,
            )
    ax.set_yticks(range(len(present)))
    ax.set_yticklabels(present)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Brier skill score vs. base rate (higher is better; 0 = no skill)")
    ax.set_title(title, loc="left", pad=26)
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles, strict=False))
    ax.legend(
        uniq.values(),
        uniq.keys(),
        loc="lower left",
        bbox_to_anchor=(0, 1.02),
        ncol=2,
        borderaxespad=0,
    )
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / name, dpi=150)
    plt.close(fig)


def compare_llm(con: Any, df: pd.DataFrame) -> list[dict[str, Any]]:
    """Market vs. LLM vs. their average, on the same markets, plus the paired Brier difference
    (market minus LLM; negative = market better) with an event-clustered CI."""
    tables = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    if "llm_forecasts" not in tables:
        return []
    llm = con.execute("SELECT source, market_id, p AS p_llm FROM llm_forecasts").df()
    joined = df.merge(llm, on=["source", "market_id"], how="inner")
    rows: list[dict[str, Any]] = []
    for (src, h), g in joined.groupby(["source", "horizon"]):
        if len(g) < MIN_GROUP:
            continue
        y, c = g["outcome"].to_numpy(float), g["event_id"].to_numpy()
        pm, pl = g["p"].to_numpy(float), g["p_llm"].to_numpy(float)
        row: dict[str, Any] = {
            "source": src,
            "horizon": h,
            "n_markets": len(g),
            "n_events": int(np.unique(c).size),
        }
        for name, p in (("market", pm), ("llm", pl), ("ensemble", (pm + pl) / 2)):
            for metric, fn in (("brier", M.brier), ("log_loss", M.log_loss), ("ece", M.ece)):
                e = M.cluster_bootstrap(fn, p, y, c, n_boot=N_BOOT // 2, seed=SEED)
                row[f"{name}_{metric}"] = {"value": e.point, "ci95": [e.lo, e.hi]}
        d = (pm - y) ** 2 - (pl - y) ** 2
        e = M.cluster_bootstrap(
            lambda a, _b: float(a.mean()), d, np.zeros_like(d), c, n_boot=N_BOOT, seed=SEED
        )
        row["brier_diff_market_minus_llm"] = {"value": e.point, "ci95": [e.lo, e.hi]}
        rows.append(row)
    return rows


# ---------------------------------------------------------------- main


def main() -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    df = load(con)
    drops = con.execute("""
        SELECT source, horizon, reason, count(*) AS n FROM snapshot_drops GROUP BY ALL
    """).df()

    llm_rows = compare_llm(con, df)
    con.close()
    fixed = df[df.all_horizons]
    ope = one_per_event(df)
    scorecard: dict[str, Any] = {
        "notes": {
            "log_loss_clip": [CLIP_EPS, 1 - CLIP_EPS],
            "bins": f"{N_BINS} equal-count",
            "ci": f"95% percentile bootstrap, {N_BOOT} resamples of events (not markets)",
            "bss_reference": "in-sample base rate of the same group",
            "anchor": "scheduled close minus horizon; resolution time never used",
        },
        "headline": grouped(df, ["source", "horizon"], N_BOOT),
        "fixed_population": grouped(fixed, ["source", "horizon"], N_BOOT),
        "one_market_per_event": grouped(ope, ["source", "horizon"], N_BOOT),
        "by_category": grouped(df, ["source", "horizon", "category"]),
        "by_liquidity": grouped(df, ["source", "horizon", "liquidity"]),
        "by_price_type": grouped(df, ["source", "horizon", "price_type"]),
        "snapshot_drops": drops.to_dict("records"),
        "llm_comparison": llm_rows,
    }

    fig_reliability(df, "reliability.png", "Reliability by platform and horizon (all snapshots)")
    fig_reliability(
        fixed,
        "reliability_fixed_population.png",
        "Reliability, fixed population (markets with all three horizons)",
    )
    scorecard["favorite_longshot"] = fig_favorite_longshot(df)
    cats = sorted(df["category"].unique())
    fig_breakdown(
        scorecard["by_category"], "category", cats, "by_category.png", "Skill by category (T-7d)"
    )
    fig_breakdown(
        scorecard["by_liquidity"],
        "liquidity",
        [*LIQUIDITY_LABELS, "unknown"],
        "by_liquidity.png",
        "Skill by volume traded before the snapshot (T-7d)",
    )

    (REPORTS_DIR / "scorecard.json").write_text(json.dumps(scorecard, indent=2, default=float))
    export = df[
        [
            "source",
            "market_id",
            "event_id",
            "title",
            "category",
            "horizon",
            "snapshot_ts",
            "p",
            "price_type",
            "liquidity",
            "outcome",
            "all_horizons",
        ]
    ]
    export.to_parquet(APP_DATA_DIR / "snapshots.parquet", index=False)

    print("platform    horizon   n      events  Brier   BSS [95% CI]          ECE")
    for r in scorecard["headline"]:
        b = r["bss"]
        print(
            f"{r['source']:<11} {r['horizon']:<8} {r['n_markets']:<6} {r['n_events']:<7} "
            f"{r['brier']['value']:.4f}  {b['value']:+.3f} [{b['ci95'][0]:+.3f}, "
            f"{b['ci95'][1]:+.3f}]  {r['ece']['value']:.3f}"
        )


if __name__ == "__main__":
    main()
