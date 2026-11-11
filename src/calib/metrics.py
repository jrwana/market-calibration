"""Forecast-evaluation metrics. Every function takes plain arrays (p = forecast, y = 0/1 outcome).

References:
* Murphy (1973), "A new vector partition of the probability score."
* Stephenson, Coelho & Jolliffe (2008), "Two extra components in the Brier score decomposition":
  with binned continuous forecasts the classic three-term identity is only approximate; adding
  within-bin variance and covariance terms makes it exact.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from calib.config import CLIP_EPS

Array = NDArray[np.float64]
__all__ = ["CLIP_EPS"]


def _as_arrays(p: Array, y: Array) -> tuple[Array, Array]:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    if p.shape != y.shape or p.ndim != 1:
        raise ValueError("p and y must be 1-d arrays of the same length")
    return p, y


def brier(p: Array, y: Array) -> float:
    p, y = _as_arrays(p, y)
    return float(np.mean((p - y) ** 2))


def log_loss(p: Array, y: Array, eps: float = CLIP_EPS) -> float:
    """Mean negative log likelihood, with p clipped to [eps, 1 - eps] so a confident miss is
    finite (-log(1e-4) ~ 9.2) rather than infinite."""
    p, y = _as_arrays(p, y)
    q = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def equal_count_bins(p: Array, n_bins: int) -> list[NDArray[np.intp]]:
    """Indices of p split into ``n_bins`` groups of (nearly) equal size, in increasing p order.

    Equal-count bins keep every point on the reliability curve equally well estimated; fixed-width
    bins leave the middle of the range nearly empty because market prices pile up near 0 and 1.
    """
    order = np.argsort(p, kind="stable")
    return [b for b in np.array_split(order, min(n_bins, p.size)) if b.size]


@dataclass(frozen=True)
class ReliabilityCurve:
    mean_p: Array
    freq: Array
    count: NDArray[np.int64]


def reliability_curve(p: Array, y: Array, n_bins: int = 10) -> ReliabilityCurve:
    p, y = _as_arrays(p, y)
    bins = equal_count_bins(p, n_bins)
    return ReliabilityCurve(
        mean_p=np.array([p[b].mean() for b in bins]),
        freq=np.array([y[b].mean() for b in bins]),
        count=np.array([b.size for b in bins], dtype=np.int64),
    )


def ece(p: Array, y: Array, n_bins: int = 10) -> float:
    """Expected calibration error: count-weighted mean |realized frequency - mean forecast|."""
    c = reliability_curve(p, y, n_bins)
    return float(np.sum(c.count * np.abs(c.freq - c.mean_p)) / c.count.sum())


@dataclass(frozen=True)
class Murphy:
    reliability: float  # lower is better: miscalibration
    resolution: float  # higher is better: how far outcomes move from the base rate across bins
    uncertainty: float  # base rate * (1 - base rate); a property of the events, not the forecaster
    within_bin_variance: float
    within_bin_cov: float


def murphy(p: Array, y: Array, n_bins: int = 10) -> Murphy:
    """Brier = reliability - resolution + uncertainty + within_bin_variance - within_bin_cov."""
    p, y = _as_arrays(p, y)
    n = p.size
    ybar = y.mean()
    rel = res = wbv = wbc = 0.0
    for b in equal_count_bins(p, n_bins):
        pk, yk = p[b], y[b]
        pbar, obar = pk.mean(), yk.mean()
        rel += b.size * (pbar - obar) ** 2
        res += b.size * (obar - ybar) ** 2
        wbv += float(np.sum((pk - pbar) ** 2))
        wbc += 2.0 * float(np.sum((pk - pbar) * (yk - obar)))
    return Murphy(rel / n, res / n, float(ybar * (1 - ybar)), wbv / n, wbc / n)


def brier_skill_score(p: Array, y: Array) -> float:
    """1 - Brier / Brier(base rate). The reference forecasts the sample base rate for every event:
    0 means no better than always saying the average, 1 is perfect, negative is worse."""
    p, y = _as_arrays(p, y)
    ref = brier(np.full_like(y, y.mean()), y)
    if ref == 0.0:
        return 0.0 if brier(p, y) == 0.0 else -np.inf
    return 1.0 - brier(p, y) / ref


@dataclass(frozen=True)
class FavoriteLongshot:
    edges: Array
    mean_p: Array
    freq: Array
    gap: Array  # realized frequency - mean price; negative = overpriced
    count: NDArray[np.int64]


def favorite_longshot(p: Array, y: Array, edges: Array | None = None) -> FavoriteLongshot:
    """Realized frequency minus price, by fixed price bins. The classic finding is negative gaps
    for longshots (cheap contracts overpriced) and positive gaps for favorites."""
    p, y = _as_arrays(p, y)
    edges = np.linspace(0, 1, 11) if edges is None else np.asarray(edges, dtype=float)
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, edges.size - 2)
    mean_p, freq, count = [], [], []
    for k in range(edges.size - 1):
        mask = idx == k
        count.append(int(mask.sum()))
        mean_p.append(p[mask].mean() if mask.any() else np.nan)
        freq.append(y[mask].mean() if mask.any() else np.nan)
    mp, fr = np.array(mean_p), np.array(freq)
    return FavoriteLongshot(edges, mp, fr, fr - mp, np.array(count, dtype=np.int64))


# ---------------------------------------------------------------- uncertainty


@dataclass(frozen=True)
class Estimate:
    point: float
    lo: float
    hi: float
    n: int
    n_clusters: int


def cluster_bootstrap(
    stat: Callable[[Array, Array], float],
    p: Array,
    y: Array,
    clusters: NDArray[np.generic],
    n_boot: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> Estimate:
    """Percentile bootstrap that resamples whole events, not markets.

    Markets in one event (a candidate list, a threshold ladder) resolve together, so treating them
    as independent overstates the effective sample size and gives intervals that are too narrow.
    """
    p, y = _as_arrays(p, y)
    clusters = np.asarray(clusters)
    _, inverse = np.unique(clusters, return_inverse=True)
    members = [np.flatnonzero(inverse == k) for k in range(inverse.max() + 1)]
    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, len(members), len(members))
        idx = np.concatenate([members[k] for k in pick])
        stats[i] = stat(p[idx], y[idx])
    lo, hi = np.nanquantile(stats, [alpha / 2, 1 - alpha / 2])
    return Estimate(stat(p, y), float(lo), float(hi), int(p.size), len(members))


def one_per_cluster(clusters: NDArray[np.generic], seed: int = 0) -> NDArray[np.intp]:
    """Indices of one randomly chosen row per cluster (the robustness check)."""
    clusters = np.asarray(clusters)
    rng = np.random.default_rng(seed)
    order = rng.permutation(clusters.size)
    _, first = np.unique(clusters[order], return_index=True)
    return np.sort(order[first])
