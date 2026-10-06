import sys
from pathlib import Path


def test_package_import():
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    import stress_recovery_cycle  # noqa: F401

    assert hasattr(stress_recovery_cycle, "CyclingEngine")
    assert hasattr(stress_recovery_cycle, "StressRecoverySession")
    assert hasattr(stress_recovery_cycle, "StressRecoveryCycleGUI")


def test_legacy_names():
    """Every public name of the original single-file script is still importable."""
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    from stress_recovery_cycle import app

    for name in ["TestPhase", "SweepConfig", "BiasConfig", "CycleConfig",
                 "MeasurementPoint", "BiasPoint", "CycleSummary",
                 "ThorlabsPowerMeterController", "B1500Controller", "CyclingEngine",
                 "TwoPhaseCycleWorker", "ResourceRefreshWorker", "CycleConfigWidget",
                 "PhaseMonitorWidget", "StressRecoveryCycleGUI", "main",
                 "TIME_DESIGN_LINEAR", "TIME_DESIGN_LOG",
                 "LOG_POINTS_PER_DECADE_OPTIONS", "LOG_CYCLE_MANTISSAS",
                 "PYVISA_AVAILABLE"]:
        assert hasattr(app, name), name
