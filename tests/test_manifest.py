import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stress_recovery_cycle import (  # noqa: E402
    B1500Controller,
    ThorlabsPowerMeterController,
    StressRecoverySession,
    StressRecoveryConfig,
    CycleConfig,
    SweepConfig,
    BiasConfig,
    SeriesMetadata,
)


def _phase(value):
    return CycleConfig(
        sweep=SweepConfig(start=0.0, stop=0.1, steps=3, dwell_s=0.0),
        bias=BiasConfig(mode="voltage", value=value, duration_s=0.02,
                        sample_interval_s=0.02),
        num_cycles=2,
    )


def test_two_phase_session_writes_layout_and_manifest(tmp_path):
    # Disconnected controllers take the engine's simulation branches.
    cfg = StressRecoveryConfig(
        stress=_phase(0.1), recovery=_phase(0.0),
        device_name="TST", output_folder=str(tmp_path),
        metadata=SeriesMetadata(wafer_id="W01", parent_session_id="prev"),
    )
    session = StressRecoverySession(B1500Controller(), ThorlabsPowerMeterController(), cfg)
    session.run()

    root = session.session_root
    assert root.name.startswith("TST_stress_recovery_cycle_")
    for sub in ("stress", "recovery"):
        names = sorted(p.name for p in (root / sub).iterdir())
        assert "cycle_summary.csv" in names
        # baseline (000) + one measurement after each of the 2 bias cycles
        assert [n for n in names if n.startswith("measurement_cycle_")] == [
            "measurement_cycle_000.csv", "measurement_cycle_001.csv",
            "measurement_cycle_002.csv"]
        assert [n for n in names if n.startswith("bias_cycle_")] == [
            "bias_cycle_001.csv", "bias_cycle_002.csv"]

    data = json.loads((root / "session_manifest.json").read_text(encoding="utf-8"))
    assert data["protocol_name"] == "stress_recovery_cycle"
    assert data["schema_version"] == "series-v1"
    assert data["session_id"] == root.name
    assert data["device_id"] == "TST"
    assert data["wafer_id"] == "W01"
    assert data["parent_session_id"] == "prev"
    assert data["completed"] is True
    assert data["phases"]["stress"]["bias"]["value"] == 0.1
    assert data["phases"]["recovery"]["bias"]["value"] == 0.0
    assert data["phases"]["recovery"]["measurement_points"] == 9


def test_stop_during_stress_skips_recovery(tmp_path):
    cfg = StressRecoveryConfig(device_name="TST", output_folder=str(tmp_path))
    session = StressRecoverySession(B1500Controller(), ThorlabsPowerMeterController(), cfg)
    # stop as soon as the first stress measurement point arrives
    session.stress_engine.on_measurement_point = lambda p: session.stop()
    session.run()

    data = json.loads((session.session_root / "session_manifest.json").read_text(encoding="utf-8"))
    assert data["completed"] is False
    assert data["stopped_in_phase"] == "stress"
    assert data["phases"]["recovery"]["measurement_points"] == 0


class _RecordingB1500(B1500Controller):
    """Pretends to be connected and records every command, with no instrument."""

    def __init__(self):
        super().__init__()
        self.connected = True
        self.inst = self
        self.commands = []

    def write(self, cmd):            # stands in for inst.write
        self.commands.append(cmd)

    def configure_for_sweep(self, smu, mode, compliance):
        self.commands.append("CONFIG")

    def set_bias_and_measure(self, smu, set_value, mode, compliance, dwell_s=0.1):
        self.commands.append(f"SET {set_value}")
        return set_value, 0.0

    def output_off(self, smu):
        self.commands.append("OFF")


def test_output_off_after_stop_during_baseline(tmp_path):
    """A stop during the baseline sweep must still switch the SMU off."""
    b1500 = _RecordingB1500()
    cfg = StressRecoveryConfig(device_name="TST", output_folder=str(tmp_path))
    session = StressRecoverySession(b1500, ThorlabsPowerMeterController(), cfg)
    session.stress_engine.on_measurement_point = lambda p: session.stop()
    session.run()

    assert b1500.commands[0] == "CONFIG"
    assert b1500.commands[-1] == "OFF"
