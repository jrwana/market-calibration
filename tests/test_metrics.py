"""Metrics on synthetic data with known answers, plus property tests."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from calib import metrics as M

RNG = np.random.default_rng(0)
N = 20_000


def calibrated(n: int = N, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    p = rng.uniform(0, 1, n)
    y = (rng.uniform(0, 1, n) < p).astype(float)
    return p, y


# ---------------------------------------------------------------- known answers


def test_calibrated_forecasts_have_near_zero_ece() -> None:
    p, y = calibrated()
    assert M.ece(p, y) < 0.02
    assert M.murphy(p, y).reliability < 0.001


def test_distorted_forecasts_are_worse() -> None:
    p, y = calibrated()
    for distorted in (p**2, np.sqrt(p), 0.5 + 0.5 * (p - 0.5)):
        assert M.ece(distorted, y) > M.ece(p, y) + 0.03
        assert M.murphy(distorted, y).reliability > M.murphy(p, y).reliability


def test_reliability_curve_tracks_diagonal_when_calibrated() -> None:
    p, y = calibrated()
    curve = M.reliability_curve(p, y, n_bins=10)
    assert curve.count.sum() == N
    assert np.all(np.abs(curve.mean_p - curve.freq) < 0.03)
    # Equal-count bins: sizes differ by at most one.
    assert curve.count.max() - curve.count.min() <= 1


def test_brier_known_values() -> None:
    assert M.brier(np.array([1.0, 0.0]), np.array([1.0, 0.0])) == 0.0
    assert M.brier(np.array([0.5, 0.5]), np.array([1.0, 0.0])) == 0.25
    assert M.brier(np.array([0.0]), np.array([1.0])) == 1.0


def test_log_loss_is_clipped() -> None:
    ll = M.log_loss(np.array([0.0]), np.array([1.0]))
    assert ll == pytest.approx(-np.log(M.CLIP_EPS))
    assert np.isfinite(M.log_loss(np.array([1.0, 0.0]), np.array([0.0, 1.0])))


def test_skill_score_of_base_rate_is_zero() -> None:
    y = (RNG.uniform(size=1000) < 0.3).astype(float)
    p = np.full_like(y, y.mean())
    assert M.brier_skill_score(p, y) == pytest.approx(0.0, abs=1e-12)
    assert M.brier_skill_score(y.copy(), y) == pytest.approx(1.0)


def test_favorite_longshot_measures_signed_gap() -> None:
    # Longshots priced at 10% that only hit 5%, favorites at 90% that hit 95%.
    p = np.r_[np.full(1000, 0.1), np.full(1000, 0.9)]
    y = np.r_[np.arange(1000) < 50, np.arange(1000) < 950].astype(float)
    fl = M.favorite_longshot(p, y, edges=np.array([0.0, 0.5, 1.0]))
    assert fl.gap[0] == pytest.approx(-0.05)
    assert fl.gap[1] == pytest.approx(0.05)


# ---------------------------------------------------------------- clustered bootstrap


def test_bootstrap_interval_contains_point_estimate() -> None:
    p, y = calibrated(2000)
    clusters = np.arange(2000) // 4
    est = M.cluster_bootstrap(M.brier, p, y, clusters, n_boot=500, seed=1)
    assert est.lo <= est.point <= est.hi


def test_clustering_widens_intervals_for_duplicated_events() -> None:
    """Ten perfect copies of each market carry no extra information. An iid bootstrap would be
    fooled into a narrow interval; the event-clustered bootstrap should not."""
    p, y = calibrated(500, seed=3)
    p10, y10 = np.repeat(p, 10), np.repeat(y, 10)
    by_market = np.arange(p10.size)
    by_event = np.repeat(np.arange(500), 10)
    iid = M.cluster_bootstrap(M.brier, p10, y10, by_market, n_boot=400, seed=2)
    clustered = M.cluster_bootstrap(M.brier, p10, y10, by_event, n_boot=400, seed=2)
    assert (clustered.hi - clustered.lo) > 2.5 * (iid.hi - iid.lo)


def test_bootstrap_is_reproducible() -> None:
    p, y = calibrated(300)
    c = np.arange(300) // 3
    a = M.cluster_bootstrap(M.ece, p, y, c, n_boot=200, seed=9)
    b = M.cluster_bootstrap(M.ece, p, y, c, n_boot=200, seed=9)
    assert a == b


def test_one_per_cluster_picks_exactly_one() -> None:
    clusters = np.array([3, 3, 1, 2, 2, 2])
    idx = M.one_per_cluster(clusters, seed=0)
    assert sorted(clusters[idx].tolist()) == [1, 2, 3]


# ---------------------------------------------------------------- properties

probs = arrays(np.float64, st.integers(1, 300), elements=st.floats(0.0, 1.0))


@st.composite
def forecasts(draw: st.DrawFn) -> tuple[np.ndarray, np.ndarray]:
    p = draw(probs)
    y = draw(arrays(np.float64, p.size, elements=st.sampled_from([0.0, 1.0])))
    return p, y


@settings(max_examples=300, deadline=None)
@given(forecasts())
def test_brier_in_unit_interval(py: tuple[np.ndarray, np.ndarray]) -> None:
    assert 0.0 <= M.brier(*py) <= 1.0


@settings(max_examples=200, deadline=None)
@given(arrays(np.float64, st.integers(1, 300), elements=st.sampled_from([0.0, 1.0])))
def test_perfect_forecasts_score_zero(y: np.ndarray) -> None:
    assert M.brier(y.copy(), y) == 0.0
    assert M.ece(y.copy(), y) == pytest.approx(0.0, abs=1e-12)


@settings(max_examples=300, deadline=None)
@given(forecasts(), st.integers(1, 20))
def test_murphy_identity(py: tuple[np.ndarray, np.ndarray], n_bins: int) -> None:
    """BS = REL - RES + UNC + WBV - WBC, exactly (Stephenson et al., 2008)."""
    p, y = py
    d = M.murphy(p, y, n_bins=n_bins)
    rebuilt = (
        d.reliability - d.resolution + d.uncertainty + d.within_bin_variance - d.within_bin_cov
    )
    assert rebuilt == pytest.approx(M.brier(p, y), abs=1e-10)
    assert d.reliability >= 0 and d.resolution >= 0 and d.uncertainty >= 0


@settings(max_examples=200, deadline=None)
@given(forecasts())
def test_metrics_ignore_row_order(py: tuple[np.ndarray, np.ndarray]) -> None:
    p, y = py
    perm = np.random.default_rng(0).permutation(p.size)
    assert M.brier(p[perm], y[perm]) == pytest.approx(M.brier(p, y))
    assert M.log_loss(p[perm], y[perm]) == pytest.approx(M.log_loss(p, y))


@settings(max_examples=200, deadline=None)
@given(forecasts())
def test_ece_in_unit_interval(py: tuple[np.ndarray, np.ndarray]) -> None:
    assert 0.0 <= M.ece(*py) <= 1.0
