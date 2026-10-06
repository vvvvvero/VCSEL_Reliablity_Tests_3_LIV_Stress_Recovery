"""Two-phase session: stress cycling, then recovery cycling, on the same instruments.

This module has no Qt dependency, so a full run can be scripted or tested
without the GUI. The GUI worker thread drives the same object.
"""

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .b1500_controller import B1500Controller
from .cycling_engine import CyclingEngine
from .models import StressRecoveryConfig
from .thorlabs_power_meter import ThorlabsPowerMeterController


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def safe_name(name: str) -> str:
    return name.replace(" ", "_").replace("/", "_").replace("\\", "_")


class StressRecoverySession:
    """Creates the session folder layout and runs both phases back to back.

    Layout::

        <output_folder>/<device>_stress_recovery_cycle_<timestamp>/
            session_manifest.json
            stress/     measurement_cycle_NNN.csv, bias_cycle_NNN.csv, cycle_summary.csv
            recovery/   measurement_cycle_NNN.csv, bias_cycle_NNN.csv, cycle_summary.csv
    """

    def __init__(self, b1500: B1500Controller,
                 power_meter: ThorlabsPowerMeterController,
                 config: StressRecoveryConfig):
        self.b1500 = b1500
        self.power_meter = power_meter
        self.config = config

        device = config.device_name.strip() or "Device"
        config.stress.device_name = device
        config.recovery.device_name = device
        config.stress.autosave = config.autosave
        config.recovery.autosave = config.autosave

        base = Path(config.output_folder)
        if not base.is_absolute():
            base = Path.cwd() / base
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_root = base / f"{safe_name(device)}_stress_recovery_cycle_{ts}"
        self.stress_folder = self.session_root / "stress"
        self.recovery_folder = self.session_root / "recovery"
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.stress_folder.mkdir(parents=True, exist_ok=True)
        self.recovery_folder.mkdir(parents=True, exist_ok=True)

        meta = config.metadata
        if not meta.session_id:
            meta.session_id = self.session_root.name
        if not meta.device_id:
            meta.device_id = device

        self.stress_engine = CyclingEngine(b1500, power_meter, config.stress,
                                           preset_folder=self.stress_folder)
        self.recovery_engine = CyclingEngine(b1500, power_meter, config.recovery,
                                             preset_folder=self.recovery_folder)

        self.on_log: Optional[Callable[[str], None]] = None
        self.on_overall_phase: Optional[Callable[[str], None]] = None  # "stress" | "recovery" | "done"

        self.stop_requested = False
        self.stopped_in: str = ""
        self.t_start: Optional[float] = None
        self.t_end: Optional[float] = None

    def _log(self, message: str):
        if self.on_log:
            self.on_log(message)
        else:
            print(message)

    def _phase(self, name: str):
        if self.on_overall_phase:
            self.on_overall_phase(name)

    def stop(self):
        self.stop_requested = True
        self.stress_engine.stop()
        self.recovery_engine.stop()

    def run(self):
        """Run stress cycling, then recovery cycling unless stopped during stress."""
        self.t_start = time.time()
        try:
            self._log("=" * 55)
            self._log("  PHASE 1: STRESS CYCLING")
            self._log("=" * 55)
            self._phase("stress")
            self.stress_engine.run()

            if self.stress_engine.stop_requested:
                self.stopped_in = "stress"
                self._log("Stopped during stress phase – skipping recovery.")
                return

            self._log("")
            self._log("=" * 55)
            self._log("  PHASE 2: RECOVERY CYCLING")
            self._log("=" * 55)
            self._phase("recovery")
            self.recovery_engine.run()
            if self.recovery_engine.stop_requested:
                self.stopped_in = "recovery"
        finally:
            self.t_end = time.time()
            self._save_session_manifest()
            self._phase("done")

    def _phase_record(self, engine: CyclingEngine, folder: Path) -> dict:
        cfg = engine.config
        return {
            "folder": folder.name,
            "sweep": asdict(cfg.sweep),
            "bias": asdict(cfg.bias),
            "num_cycles": cfg.num_cycles,
            "initial_measurement": cfg.initial_measurement,
            "enable_power_meter": cfg.enable_power_meter,
            "power_wavelength_nm": cfg.power_wavelength_nm,
            "cycle_bias_durations_s": engine.cycle_durations,
            "measurement_points": len(engine.measurement_data),
            "bias_points": len(engine.bias_data),
            "cycles_summarised": len(engine.cycle_summaries),
        }

    def _save_session_manifest(self):
        """Write a run manifest aligned to the Series V1 schema."""
        meta = self.config.metadata
        manifest = asdict(meta)
        manifest.update({
            "timestamp_start_utc": _utc(self.t_start or time.time()),
            "timestamp_end_utc": _utc(self.t_end or time.time()),
            "device_name": self.config.device_name,
            "b1500_idn": self.b1500.idn,
            "power_meter_idn": self.power_meter.idn,
            "completed": not self.stopped_in,
            "stopped_in_phase": self.stopped_in,
            "phases": {
                "stress": self._phase_record(self.stress_engine, self.stress_folder),
                "recovery": self._phase_record(self.recovery_engine, self.recovery_folder),
            },
            "output_files": {
                "summary": "<phase>/cycle_summary.csv",
                "measurement_glob": "<phase>/measurement_cycle_*.csv",
                "bias_glob": "<phase>/bias_cycle_*.csv",
            },
        })
        fp = self.session_root / "session_manifest.json"
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        self._log(f"Session manifest saved: {fp}")
