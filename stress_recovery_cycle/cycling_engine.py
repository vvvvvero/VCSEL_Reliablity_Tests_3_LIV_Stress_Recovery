"""Cycling engine for one phase: measurement -> bias -> measurement x N."""

import csv
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np

from .b1500_controller import B1500Controller
from .models import (
    TIME_DESIGN_LINEAR,
    TIME_DESIGN_LOG,
    BiasPoint,
    CycleConfig,
    CycleSummary,
    MeasurementPoint,
    TestPhase,
    build_cycle_timing_plan,
)
from .thorlabs_power_meter import ThorlabsPowerMeterController


class CyclingEngine:
    """Runs one cycling phase (either stress or recovery).

    Pass *preset_folder* (a Path) to write directly into that directory instead
    of creating a new timestamped folder.
    """

    def __init__(self, b1500: B1500Controller,
                 power_meter: ThorlabsPowerMeterController,
                 config: CycleConfig,
                 preset_folder: Optional[Path] = None):
        self.b1500 = b1500
        self.power_meter = power_meter
        self.config = config
        self._preset_folder = preset_folder

        # Data storage
        self.measurement_data: List[MeasurementPoint] = []
        self.bias_data: List[BiasPoint] = []
        self.cycle_summaries: List[CycleSummary] = []

        # State
        self.running = False
        self.stop_requested = False
        self.current_phase = TestPhase.IDLE
        self.current_cycle = 0
        self.cycle_durations: List[float] = []

        # Callbacks
        self.on_measurement_point: Optional[Callable] = None
        self.on_bias_point: Optional[Callable] = None
        self.on_phase_change: Optional[Callable] = None
        self.on_cycle_complete: Optional[Callable] = None
        self.on_progress: Optional[Callable] = None
        self.on_log: Optional[Callable] = None

        self.session_folder: Optional[Path] = None

    # ---------------------------------------------------------------------- #

    def log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{ts}] {message}"
        print(msg)
        if self.on_log:
            self.on_log(msg)

    def set_phase(self, phase: TestPhase):
        self.current_phase = phase
        if self.on_phase_change:
            self.on_phase_change(phase)

    def stop(self):
        self.stop_requested = True

    # ---------------------------------------------------------------------- #

    def run(self) -> Tuple[List[MeasurementPoint], List[BiasPoint]]:
        """Execute the complete cycling test and return collected data."""
        self.running = True
        self.stop_requested = False
        self.measurement_data = []
        self.bias_data = []
        self.cycle_summaries = []

        self._create_session_folder()

        if self.power_meter.connected and self.config.enable_power_meter:
            self.power_meter.configure(wavelength_nm=self.config.power_wavelength_nm)
            self.log(f"Power meter configured: λ={self.config.power_wavelength_nm} nm")

        if self.b1500.connected:
            try:
                self.b1500.configure_for_sweep(
                    self.config.sweep.smu,
                    self.config.sweep.mode,
                    self.config.sweep.compliance
                )
                self.log("B1500 configured")
            except Exception as e:
                self.log(f"B1500 configuration error: {e}")
                self.running = False
                return self.measurement_data, self.bias_data

        bias_cfg = self.config.bias
        total_cycles = self.config.num_cycles
        cycle_durations, cumulative_elapsed = build_cycle_timing_plan(
            total_cycles, bias_cfg.duration_s, bias_cfg.time_design,
            bias_cfg.log_points_per_decade
        )
        self.cycle_durations = cycle_durations
        design_normalized = (bias_cfg.time_design or TIME_DESIGN_LINEAR).strip().lower()
        if design_normalized == TIME_DESIGN_LOG:
            design_label = f"LOG({bias_cfg.log_points_per_decade} cycles/decade)"
            duration_label = f"t0={bias_cfg.duration_s}s"
        else:
            design_label = "LINEAR"
            duration_label = f"cycle_dt={bias_cfg.duration_s}s"
        self.log(f"Starting cycling: {total_cycles} cycles | "
                 f"bias={bias_cfg.value}{'V' if bias_cfg.mode == 'voltage' else 'A'} "
                 f"| {duration_label} | time design={design_label}")

        preview_n = min(10, len(cycle_durations))
        preview = ", ".join(f"{v:g}" for v in cycle_durations[:preview_n])
        if len(cycle_durations) > preview_n:
            preview += ", ..."
        self.log(f"Phase cycle stress durations (s): {preview}")

        try:
            if self.config.initial_measurement:
                self.current_cycle = 0
                self._run_measurement_phase()
                if self.stop_requested:
                    return self.measurement_data, self.bias_data

            for cycle, cycle_duration_s in enumerate(cycle_durations, start=1):
                if self.stop_requested:
                    self.log("Stopped by user")
                    break

                self.current_cycle = cycle
                elapsed_s = cumulative_elapsed[cycle - 1]
                self.log(
                    f"\n{'='*50}\n"
                    f"CYCLE {cycle}/{total_cycles} | stress={cycle_duration_s:g}s | cumulative elapsed={elapsed_s:g}s\n"
                    f"{'='*50}"
                )

                self._run_bias_phase(cycle_duration_s)
                if self.stop_requested:
                    break

                self._run_measurement_phase()

                if self.on_progress:
                    self.on_progress(cycle, total_cycles)
                if self.on_cycle_complete:
                    self.on_cycle_complete(cycle)

            self._save_summary()
            self.set_phase(
                TestPhase.COMPLETED if not self.stop_requested else TestPhase.STOPPED
            )
            self.log(f"Done. {len(self.measurement_data)} measurement pts, "
                     f"{len(self.bias_data)} bias pts.")

        except Exception as e:
            import traceback
            self.log(f"Engine error: {e}")
            traceback.print_exc()
        finally:
            # Always leave the SMU off: also after a stop during the baseline
            # measurement or an engine error, not only after a normal finish.
            if self.b1500.connected:
                self.b1500.output_off(self.config.sweep.smu)
            self.running = False

        return self.measurement_data, self.bias_data

    # ---------------------------------------------------------------------- #
    # Internal phase runners
    # ---------------------------------------------------------------------- #

    def _run_measurement_phase(self):
        self.set_phase(TestPhase.MEASUREMENT)
        self.log(f"Measurement phase (cycle {self.current_cycle})")
        cfg = self.config.sweep
        setpoints = cfg.setpoints
        cycle_data: List[MeasurementPoint] = []

        for idx, setpoint in enumerate(setpoints):
            if self.stop_requested:
                break
            timestamp = time.time()

            if self.b1500.connected:
                voltage, current = self.b1500.set_bias_and_measure(
                    cfg.smu, setpoint, cfg.mode, cfg.compliance, cfg.dwell_s
                )
            else:
                voltage = setpoint if cfg.mode == "iv" else 0.0
                current = 0.0 if cfg.mode == "iv" else setpoint

            if self.power_meter.connected and self.config.enable_power_meter:
                power, pm_status = self.power_meter.measure_power()
            else:
                power, pm_status = 0.0, "No power meter"

            point = MeasurementPoint(
                cycle=self.current_cycle,
                point_index=idx,
                timestamp=timestamp,
                setpoint=setpoint,
                voltage=voltage,
                current=current,
                optical_power=power,
                status=pm_status
            )
            self.measurement_data.append(point)
            cycle_data.append(point)
            if self.on_measurement_point:
                self.on_measurement_point(point)

        self._save_measurement_cycle(cycle_data)
        if cycle_data:
            self.cycle_summaries.append(self._calculate_summary(cycle_data))
        self.log(f"Measurement done: {len(cycle_data)} points")

    def _run_bias_phase(self, duration_s: float):
        self.set_phase(TestPhase.BIAS)
        cfg = self.config.bias
        self.log(f"Bias phase: {cfg.value}{'V' if cfg.mode == 'voltage' else 'A'} "
                 f"for {duration_s}s")

        start_time = time.time()
        end_time = start_time + duration_s
        cycle_bias_data: List[BiasPoint] = []

        if self.b1500.connected:
            try:
                smu = self.config.sweep.smu
                with self.b1500.lock:
                    if cfg.mode == "voltage":
                        self.b1500.inst.write(f"DV {smu},0,{cfg.value},{cfg.compliance}")
                    else:
                        self.b1500.inst.write(f"DI {smu},0,{cfg.value},{cfg.compliance}")
            except Exception as e:
                self.log(f"Bias setup error: {e}")
                return

        sample_count = 0
        while time.time() < end_time and not self.stop_requested:
            timestamp = time.time()
            elapsed = timestamp - start_time

            if self.b1500.connected:
                voltage, current = self.b1500.set_bias_and_measure(
                    self.config.sweep.smu, cfg.value, cfg.mode,
                    cfg.compliance, dwell_s=0.01
                )
            else:
                voltage = cfg.value if cfg.mode == "voltage" else 0.0
                current = 0.0 if cfg.mode == "voltage" else cfg.value

            if self.power_meter.connected and self.config.enable_power_meter:
                power, pm_status = self.power_meter.measure_power()
            else:
                power, pm_status = 0.0, "No power meter"

            point = BiasPoint(
                cycle=self.current_cycle,
                timestamp=timestamp,
                elapsed_s=elapsed,
                voltage=voltage,
                current=current,
                optical_power=power,
                status=pm_status
            )
            self.bias_data.append(point)
            cycle_bias_data.append(point)
            sample_count += 1
            if self.on_bias_point:
                self.on_bias_point(point)

            if sample_count % max(1, int(10 / cfg.sample_interval_s)) == 0:
                self.log(f"  Bias: {elapsed:.1f}s, I={current:.4e}A, P={power:.4e}W")

            next_t = start_time + sample_count * cfg.sample_interval_s
            sleep_t = next_t - time.time()
            if sleep_t > 0:
                time.sleep(min(sleep_t, 0.5))

        self._save_bias_cycle(cycle_bias_data)
        self.log(f"Bias done: {len(cycle_bias_data)} samples, "
                 f"duration: {time.time() - start_time:.1f}s")

    # ---------------------------------------------------------------------- #
    # Summary and file I/O
    # ---------------------------------------------------------------------- #

    def _calculate_summary(self, data: List[MeasurementPoint]) -> CycleSummary:
        voltages = [p.voltage for p in data]
        currents = [p.current for p in data]
        powers = [p.optical_power for p in data]

        peak_current = max(currents) if currents else 0.0
        peak_power = max(powers) if powers else 0.0

        threshold_v = 0.0
        for v, i in zip(voltages, currents):
            if abs(i) > 1e-6:
                threshold_v = v
                break

        series_r = 0.0
        if len(voltages) > 5:
            try:
                n_fit = max(3, len(voltages) // 3)
                v_fit = np.array(voltages[-n_fit:])
                i_fit = np.array(currents[-n_fit:])
                if np.std(i_fit) > 0:
                    slope, _ = np.polyfit(i_fit, v_fit, 1)
                    series_r = abs(slope)
            except Exception:
                pass

        return CycleSummary(
            cycle=self.current_cycle,
            timestamp=datetime.now().isoformat(),
            peak_current=peak_current,
            peak_power=peak_power,
            threshold_voltage=threshold_v,
            series_resistance=series_r
        )

    def _create_session_folder(self):
        if self._preset_folder is not None:
            self.session_folder = Path(self._preset_folder)
            self.session_folder.mkdir(parents=True, exist_ok=True)
            self.log(f"Output folder: {self.session_folder}")
            return
        # Fallback: create a timestamped folder (only used when engine is run standalone)
        base = Path(self.config.output_folder)
        if not base.is_absolute():
            base = Path.cwd() / base
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_folder = base / f"{self.config.device_name}_cycle_{ts}"
        self.session_folder.mkdir(parents=True, exist_ok=True)
        self.log(f"Session folder: {self.session_folder}")

    def _save_measurement_cycle(self, data: List[MeasurementPoint]):
        if not data or not self.config.autosave or not self.session_folder:
            return
        fp = self.session_folder / f"measurement_cycle_{self.current_cycle:03d}.csv"
        with open(fp, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(["Point", "Timestamp", "Setpoint", "Voltage_V",
                        "Current_A", "Optical_Power_W", "Status"])
            for p in data:
                w.writerow([p.point_index,
                             datetime.fromtimestamp(p.timestamp).isoformat(),
                             f"{p.setpoint:.6e}", f"{p.voltage:.6e}",
                             f"{p.current:.6e}", f"{p.optical_power:.6e}",
                             p.status])

    def _save_bias_cycle(self, data: List[BiasPoint]):
        if not data or not self.config.autosave or not self.session_folder:
            return
        fp = self.session_folder / f"bias_cycle_{self.current_cycle:03d}.csv"
        with open(fp, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(["Timestamp", "Elapsed_s", "Voltage_V",
                        "Current_A", "Optical_Power_W", "Status"])
            for p in data:
                w.writerow([datetime.fromtimestamp(p.timestamp).isoformat(),
                             f"{p.elapsed_s:.3f}", f"{p.voltage:.6e}",
                             f"{p.current:.6e}", f"{p.optical_power:.6e}",
                             p.status])

    def _save_summary(self):
        if not self.cycle_summaries or not self.session_folder:
            return
        fp = self.session_folder / "cycle_summary.csv"
        with open(fp, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(["Cycle", "Timestamp", "Peak_Current_A", "Peak_Power_W",
                        "Threshold_V", "Series_Resistance_Ohm"])
            for s in self.cycle_summaries:
                w.writerow([s.cycle, s.timestamp, f"{s.peak_current:.6e}",
                             f"{s.peak_power:.6e}", f"{s.threshold_voltage:.4f}",
                             f"{s.series_resistance:.4f}"])
        self.log(f"Summary saved: {fp}")
