"""
Tests for evaluate.py. Run with:   pytest test_evaluate.py -v
In Jupyter:                          !pytest test_evaluate.py -v

Each test feeds the grader a made-up estimator whose right answer is obvious,
so a wrong verdict means the grader is broken, not the physics.
"""

import numpy as np
import pytest

from evaluate import grade

TRUE = [0, 15, 30, 45, 60, 75, 90]


def test_perfect_estimator_passes():
    r = grade(TRUE, TRUE)
    assert r["median_abs_err"] == 0 and r["slope"] == pytest.approx(1.0)
    assert r["PASS"]


def test_constant_45_passes_preregistered_but_fails_tracking():
    """The loophole found in b.1p1: a stuck estimate scores a perfect median."""
    r = grade(TRUE, [45] * 7)
    assert r["median_signed_err"] == 0          # looks perfect...
    assert r["pass_preregistered"]              # ...and passes the proposal's test
    assert r["slope"] == pytest.approx(0.0)
    assert not r["pass_tracking"]
    assert not r["PASS"]                        # but the full grade rejects it


def test_errors_that_cancel_fail_abs_criterion():
    """+20 and -20 average to a median near 0 but are not accurate."""
    est = [0, 15, 30 + 20, 45 + 0, 60 - 20, 75, 90]
    r = grade(TRUE, est)
    assert abs(r["median_signed_err"]) < 15     # signed median hides it
    assert r["median_abs_err"] == 20
    assert not r["PASS"]


def test_constant_offset_tracks_but_fails_accuracy():
    """Right trend, wrong level: slope 1 but 20 deg too high everywhere."""
    r = grade(TRUE, np.array(TRUE) + 20)
    assert r["slope"] == pytest.approx(1.0)
    assert not r["pass_abs_err"] and not r["PASS"]


def test_only_30_to_60_counts():
    """Huge errors at 0 and 90 deg must not affect the grade."""
    est = [80, 15, 30, 45, 60, 75, 0]
    r = grade(TRUE, est)
    assert r["n_maps"] == 3 and r["PASS"]


def test_real_b1p1_numbers_pass():
    """Low-S estimates from your b1p1_t900 run."""
    r = grade(TRUE, [22.9, 26.1, 28.9, 47.3, 52.7, 59.2, 55.9])
    assert r["PASS"] and r["slope"] > 0.7


def test_real_b_1p1_numbers_fail():
    """Low-S estimates from your super-Alfvenic b.1p1_t900 run."""
    r = grade(TRUE, [47.0, 45.3, 47.6, 46.0, 47.9, 44.3, 44.5])
    assert r["pass_preregistered"]              # the old test would pass it
    assert not r["PASS"]                        # the full grade does not


def test_too_few_maps_raises():
    with pytest.raises(ValueError):
        grade([0, 45, 90], [0, 45, 90])         # only one tilt in 30-60


def test_true_tilts_slightly_off_nominal_still_count():
    """In weak-field runs the true tilt is e.g. 59.98, not 60. It must still be graded."""
    true = [0.01, 15.02, 30.03, 45.01, 59.98, 75.0, 89.97]
    r = grade(true, true)
    assert r["n_maps"] == 3
