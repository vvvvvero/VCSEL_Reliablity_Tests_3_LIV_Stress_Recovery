import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stress_recovery_cycle import build_cycle_timing_plan  # noqa: E402


def test_linear_plan():
    durations, cumulative = build_cycle_timing_plan(4, 60.0, "linear", 5)
    assert durations == [60.0] * 4
    assert cumulative == [60.0, 120.0, 180.0, 240.0]


def test_log_plan_five_per_decade():
    durations, _ = build_cycle_timing_plan(7, 10.0, "log", 5)
    assert durations == pytest.approx([10, 20, 30, 50, 80, 100, 200])


def test_log_density_snaps_to_nearest_option():
    # 4 is not an option; it snaps to 3 (mantissas 1, 2, 5)
    durations, _ = build_cycle_timing_plan(4, 1.0, "log", 4)
    assert durations == pytest.approx([1, 2, 5, 10])
