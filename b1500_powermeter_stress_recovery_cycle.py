#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
B1500 + Thorlabs Power Meter  –  Two-Phase Stress-Recovery Cycling Test

Two sequential cycling phases, each with the structure:
  Measurement → Bias → Measurement → Bias → ...  (N cycles)

  Phase 1 "Stress":    high-stress bias, N_stress cycles
  Phase 2 "Recovery":  lower/zero recovery bias, N_recovery cycles

Both phases run back-to-back on the same instruments with fully independent
parameters (sweep range, bias value/duration, number of cycles, compliance …).

Data layout:
  <save_folder>/<device>_stress_recovery_cycle_<timestamp>/
      stress/       ← measurement_cycle_NNN.csv, bias_cycle_NNN.csv, cycle_summary.csv
      recovery/     ← measurement_cycle_NNN.csv, bias_cycle_NNN.csv, cycle_summary.csv

Author: Veronica GaoZhan
Date: June 2026
"""

import sys
import os
import csv
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox,
    QDoubleSpinBox, QTextEdit, QFileDialog, QMessageBox, QProgressBar,
    QGridLayout, QCheckBox, QSplitter, QScrollArea, QTabWidget, QStatusBar,
    QFrame, QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont

import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False
    print("Warning: pyvisa not installed. Install with: pip install pyvisa pyvisa-py")

try:
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
except (AttributeError, OSError, TypeError):
    pass


TIME_DESIGN_LINEAR = "linear"
TIME_DESIGN_LOG = "log"
LOG_POINTS_PER_DECADE_OPTIONS = (1, 2, 3, 5, 10)
LOG_CYCLE_MANTISSAS = {
    1: (1.0,),
    2: (1.0, 3.0),
    3: (1.0, 2.0, 5.0),
    5: (1.0, 2.0, 3.0, 5.0, 8.0),
    10: (1.0, 1.3, 1.6, 2.0, 2.5, 3.2, 4.0, 5.0, 6.3, 8.0),
}


# =============================================================================
# Enums and Data Classes
# =============================================================================

class TestPhase(Enum):
    IDLE = "idle"
    MEASUREMENT = "measurement"
    BIAS = "bias"
    COMPLETED = "completed"
    STOPPED = "stopped"


@dataclass
class SweepConfig:
    """Configuration for IV/VI sweep measurement."""
    smu: int = 1
    mode: str = "iv"        # "iv" (source V, measure I) or "vi" (source I, measure V)
    start: float = 0.0
    stop: float = 2.0
    steps: int = 21
    dwell_s: float = 0.1
    compliance: float = 0.1

    @property
    def setpoints(self) -> List[float]:
        if self.steps < 2:
            return [self.start]
        return [
            self.start + i * (self.stop - self.start) / (self.steps - 1)
            for i in range(self.steps)
        ]


@dataclass
class BiasConfig:
    """Configuration for the bias (stress or recovery) phase within each cycle."""
    mode: str = "voltage"           # "voltage" or "current"
    value: float = 2.0              # stress/recovery voltage (V) or current (A)
    duration_s: float = 60.0        # linear: per-cycle duration; log: start duration
    time_design: str = TIME_DESIGN_LINEAR  # "linear" or "log"
    log_points_per_decade: int = 5  # options: 1/2/3/5/10
    sample_interval_s: float = 1.0  # monitoring sampling interval during bias
    compliance: float = 0.1         # compliance (A for voltage mode, V for current mode)


@dataclass
class CycleConfig:
    """Configuration for one complete cycling phase (stress or recovery)."""
    # Sweep settings
    sweep: SweepConfig = field(default_factory=SweepConfig)
    # Bias settings
    bias: BiasConfig = field(default_factory=BiasConfig)
    # Cycle settings
    num_cycles: int = 10
    initial_measurement: bool = True   # perform baseline measurement before cycle 1
    # Power meter settings
    enable_power_meter: bool = True
    power_wavelength_nm: float = 850.0
    # Output settings (subfolder path is set programmatically before run)
    device_name: str = "Device_001"
    autosave: bool = True


@dataclass
class MeasurementPoint:
    cycle: int
    point_index: int
    timestamp: float
    setpoint: float
    voltage: float
    current: float
    optical_power: float
    status: str = "OK"


@dataclass
class BiasPoint:
    cycle: int
    timestamp: float
    elapsed_s: float
    voltage: float
    current: float
    optical_power: float
    status: str = "OK"


@dataclass
class CycleSummary:
    cycle: int
    timestamp: str
    peak_current: float
    peak_power: float
    threshold_voltage: float
    series_resistance: float


# =============================================================================
# Thorlabs Power Meter Controller
# =============================================================================

class ThorlabsPowerMeterController:
    """Controller for Thorlabs PM100D/PM400 power meters via VISA."""

    SCPI_IDN = "*IDN?"
    SCPI_MEAS_POWER = "MEAS:POW?"
    SCPI_CONF_POWER = "CONF:POW"
    SCPI_SET_WAVELENGTH = "SENS:CORR:WAV {}"
    SCPI_AUTO_RANGE_ON = "SENS:POW:RANG:AUTO ON"
    SCPI_SET_AVERAGES = "SENS:AVER:COUN {}"

    def __init__(self):
        self.rm = None
        self.inst = None
        self.resource: Optional[str] = None
        self.idn: str = ""
        self.lock = threading.Lock()
        self.connected = False

    def _resource_manager(self):
        try:
            return pyvisa.ResourceManager()
        except Exception:
            return pyvisa.ResourceManager("@py")

    def list_resources(self, filter_pattern: str = "") -> List[str]:
        if not PYVISA_AVAILABLE:
            return []
        rm = self._resource_manager()
        try:
            all_res = rm.list_resources()
            if filter_pattern:
                return sorted(r for r in all_res if filter_pattern.upper() in r.upper())
            return sorted(all_res)
        except Exception:
            return []
        finally:
            try:
                rm.close()
            except Exception:
                pass

    def connect(self, resource: str, timeout_ms: int = 5000) -> Tuple[bool, str]:
        self.disconnect()
        try:
            self.rm = self._resource_manager()
            self.inst = self.rm.open_resource(resource)
            self.inst.timeout = timeout_ms
            self.inst.write_termination = "\n"
            self.inst.read_termination = "\n"
            with self.lock:
                self.idn = self.inst.query(self.SCPI_IDN).strip()
                self.inst.write(self.SCPI_CONF_POWER)
                time.sleep(0.1)
            self.resource = resource
            self.connected = True
            return True, f"Connected: {self.idn}"
        except Exception as exc:
            self.disconnect()
            return False, f"Connection failed: {exc}"

    def disconnect(self) -> None:
        if self.inst is not None:
            try:
                self.inst.close()
            except Exception:
                pass
        if self.rm is not None:
            try:
                self.rm.close()
            except Exception:
                pass
        self.inst = None
        self.rm = None
        self.resource = None
        self.idn = ""
        self.connected = False

    def configure(self, wavelength_nm: float, auto_range: bool = True,
                  averages: int = 1) -> bool:
        if not self.inst:
            return False
        try:
            with self.lock:
                self.inst.write(self.SCPI_SET_WAVELENGTH.format(wavelength_nm))
                time.sleep(0.05)
                if auto_range:
                    self.inst.write(self.SCPI_AUTO_RANGE_ON)
                self.inst.write(self.SCPI_SET_AVERAGES.format(averages))
            return True
        except Exception:
            return False

    def measure_power(self) -> Tuple[float, str]:
        if not self.inst:
            return 0.0, "Not connected"
        try:
            with self.lock:
                resp = self.inst.query(self.SCPI_MEAS_POWER).strip()
            return float(resp), "OK"
        except ValueError:
            return 0.0, "Parse error"
        except Exception as e:
            return 0.0, f"Error: {e}"


# =============================================================================
# B1500 Controller
# =============================================================================

class B1500Controller:
    """Controller for Keysight B1500 Semiconductor Parameter Analyzer."""

    def __init__(self):
        self.rm = None
        self.inst = None
        self.resource: Optional[str] = None
        self.idn: str = ""
        self.lock = threading.Lock()
        self.connected = False

    def _resource_manager(self):
        try:
            return pyvisa.ResourceManager()
        except Exception:
            return pyvisa.ResourceManager("@py")

    def list_all_resources(self) -> List[str]:
        if not PYVISA_AVAILABLE:
            return []
        rm = self._resource_manager()
        try:
            return sorted(rm.list_resources())
        except Exception:
            return []
        finally:
            try:
                rm.close()
            except Exception:
                pass

    def connect(self, resource: str, timeout_ms: int = 15000) -> Tuple[bool, str]:
        self.disconnect()
        try:
            self.rm = self._resource_manager()
            self.inst = self.rm.open_resource(resource)
            self.inst.timeout = timeout_ms
            self.inst.write_termination = "\n"
            self.inst.read_termination = "\n"
            with self.lock:
                self.idn = self.inst.query("*IDN?").strip()
                self.inst.write("FMT 1,0")
                time.sleep(0.1)
            self.resource = resource
            self.connected = True
            return True, f"Connected: {self.idn}"
        except Exception as exc:
            self.disconnect()
            return False, f"Connection failed: {exc}"

    def disconnect(self) -> None:
        if self.inst is not None:
            try:
                self.inst.close()
            except Exception:
                pass
        if self.rm is not None:
            try:
                self.rm.close()
            except Exception:
                pass
        self.inst = None
        self.rm = None
        self.resource = None
        self.idn = ""
        self.connected = False

    def _safe_read(self) -> str:
        if not self.inst:
            return ""
        try:
            raw = self.inst.read_raw()
            for enc in ['ascii', 'latin-1', 'utf-8']:
                try:
                    return raw.decode(enc).strip()
                except UnicodeDecodeError:
                    continue
            return raw.decode('ascii', errors='ignore').strip()
        except Exception:
            return ""

    def configure_for_sweep(self, smu: int, mode: str, compliance: float) -> None:
        with self.lock:
            if not self.inst:
                raise RuntimeError("Not connected")
            try:
                for _ in range(5):
                    err = self.inst.query("ERR?")
                    if err.strip().startswith("0"):
                        break
            except Exception:
                pass
            time.sleep(0.1)
            self.inst.write("FMT 1,0")
            time.sleep(0.05)
            self.inst.write(f"CN {smu}")
            time.sleep(0.1)
            self.inst.write(f"AAD {smu},1")
            time.sleep(0.05)
            self.inst.write("AV 1,0")
            time.sleep(0.05)
            if mode == "iv":
                self.inst.write(f"RI {smu},0")
            else:
                self.inst.write(f"RV {smu},0")
            time.sleep(0.05)
            self.inst.write(f"MM 1,{smu}")

    def set_bias_and_measure(self, smu: int, set_value: float, mode: str,
                             compliance: float, dwell_s: float = 0.1) -> Tuple[float, float]:
        with self.lock:
            if not self.inst:
                raise RuntimeError("Not connected")
            try:
                if mode in ["iv", "voltage"]:
                    self.inst.write(f"DV {smu},0,{set_value},{compliance}")
                else:
                    self.inst.write(f"DI {smu},0,{set_value},{compliance}")
                if dwell_s > 0:
                    time.sleep(dwell_s)
                self.inst.write("XE")
                old_to = self.inst.timeout
                self.inst.timeout = 5000
                try:
                    resp = self._safe_read()
                finally:
                    self.inst.timeout = old_to
            except Exception:
                return (set_value, 0.0) if mode in ["iv", "voltage"] else (0.0, set_value)

        try:
            if not resp:
                return (set_value, 0.0) if mode in ["iv", "voltage"] else (0.0, set_value)
            parts = resp.replace(";", ",").split(",")
            values = []
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                try:
                    num_start = 0
                    for i, c in enumerate(p):
                        if c in '+-0123456789.':
                            num_start = i
                            break
                    num_str = p[num_start:]
                    if num_str:
                        values.append(float(num_str))
                except Exception:
                    pass
            if values:
                measured = values[0]
                return (set_value, measured) if mode in ["iv", "voltage"] else (measured, set_value)
        except Exception:
            pass
        return (set_value, 0.0) if mode in ["iv", "voltage"] else (0.0, set_value)

    def output_off(self, smu: int) -> None:
        if not self.inst:
            return
        with self.lock:
            try:
                self.inst.write(f"DV {smu},0,0,0.01")
                time.sleep(0.05)
                self.inst.write(f"CL {smu}")
            except Exception:
                pass


# =============================================================================
# Cycling Engine  (one phase: measurement → bias → measurement × N)
# =============================================================================

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

    def _build_cycle_timing_plan(self, total_cycles: int, base_duration_s: float,
                                 time_design: str,
                                 log_points_per_decade: int) -> Tuple[List[float], List[float]]:
        """Return per-cycle durations and cumulative elapsed times for one phase."""
        base = max(1e-9, float(base_duration_s))
        design = (time_design or TIME_DESIGN_LINEAR).strip().lower()

        if design == TIME_DESIGN_LOG:
            ppd = int(log_points_per_decade)
            if ppd not in LOG_POINTS_PER_DECADE_OPTIONS:
                ppd = min(LOG_POINTS_PER_DECADE_OPTIONS, key=lambda v: abs(v - ppd))
            mantissas = LOG_CYCLE_MANTISSAS[ppd]
            durations = []
            for idx in range(total_cycles):
                decade = idx // ppd
                m = mantissas[idx % ppd]
                durations.append(base * m * (10 ** decade))
        else:
            durations = [base for _ in range(total_cycles)]

        durations = [max(1e-9, float(v)) for v in durations]
        cumulative_elapsed: List[float] = []
        running = 0.0
        for dt in durations:
            running += dt
            cumulative_elapsed.append(running)

        return durations, cumulative_elapsed

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
        cycle_durations, cumulative_elapsed = self._build_cycle_timing_plan(
            total_cycles, bias_cfg.duration_s, bias_cfg.time_design,
            bias_cfg.log_points_per_decade
        )
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

            if self.b1500.connected:
                self.b1500.output_off(self.config.sweep.smu)

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
        base = Path(self.config.__dict__.get("output_folder", "results"))
        if not base.is_absolute():
            base = Path(__file__).parent / base
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


# =============================================================================
# Worker Thread  (runs stress phase then recovery phase sequentially)
# =============================================================================

class TwoPhaseCycleWorker(QThread):
    """Worker that runs the stress cycling engine, then the recovery cycling engine."""

    # --- Stress phase signals ---
    stress_measurement_point = pyqtSignal(object)
    stress_bias_point = pyqtSignal(object)
    stress_phase_change = pyqtSignal(object)
    stress_cycle_complete = pyqtSignal(int)
    stress_progress = pyqtSignal(int, int)

    # --- Recovery phase signals ---
    recovery_measurement_point = pyqtSignal(object)
    recovery_bias_point = pyqtSignal(object)
    recovery_phase_change = pyqtSignal(object)
    recovery_cycle_complete = pyqtSignal(int)
    recovery_progress = pyqtSignal(int, int)

    # --- Shared ---
    log_message = pyqtSignal(str)
    overall_phase = pyqtSignal(str)   # "stress" | "recovery" | "done"
    finished_signal = pyqtSignal()

    def __init__(self, stress_engine: CyclingEngine, recovery_engine: CyclingEngine):
        super().__init__()
        self._se = stress_engine
        self._re = recovery_engine
        self._bind_stress()
        self._bind_recovery()

    def _bind_stress(self):
        self._se.on_measurement_point = lambda p: self.stress_measurement_point.emit(p)
        self._se.on_bias_point = lambda p: self.stress_bias_point.emit(p)
        self._se.on_phase_change = lambda p: self.stress_phase_change.emit(p)
        self._se.on_cycle_complete = lambda c: self.stress_cycle_complete.emit(c)
        self._se.on_progress = lambda c, t: self.stress_progress.emit(c, t)
        self._se.on_log = lambda m: self.log_message.emit(m)

    def _bind_recovery(self):
        self._re.on_measurement_point = lambda p: self.recovery_measurement_point.emit(p)
        self._re.on_bias_point = lambda p: self.recovery_bias_point.emit(p)
        self._re.on_phase_change = lambda p: self.recovery_phase_change.emit(p)
        self._re.on_cycle_complete = lambda c: self.recovery_cycle_complete.emit(c)
        self._re.on_progress = lambda c, t: self.recovery_progress.emit(c, t)
        self._re.on_log = lambda m: self.log_message.emit(m)

    def stop(self):
        self._se.stop()
        self._re.stop()

    def run(self):
        try:
            self.log_message.emit("=" * 55)
            self.log_message.emit("  PHASE 1: STRESS CYCLING")
            self.log_message.emit("=" * 55)
            self.overall_phase.emit("stress")
            self._se.run()

            if self._se.stop_requested:
                self.log_message.emit("Stopped during stress phase – skipping recovery.")
                self.finished_signal.emit()
                return

            self.log_message.emit("")
            self.log_message.emit("=" * 55)
            self.log_message.emit("  PHASE 2: RECOVERY CYCLING")
            self.log_message.emit("=" * 55)
            self.overall_phase.emit("recovery")
            self._re.run()

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.log_message.emit(f"Worker error: {e}")
        finally:
            self.overall_phase.emit("done")
            self.finished_signal.emit()


class ResourceRefreshWorker(QThread):
    resources_ready = pyqtSignal(object)
    refresh_failed = pyqtSignal(str)

    def __init__(self, fetch_fn: Callable[[], List[str]]):
        super().__init__()
        self.fetch_fn = fetch_fn

    def run(self):
        try:
            self.resources_ready.emit(self.fetch_fn())
        except Exception as e:
            self.refresh_failed.emit(str(e))


# =============================================================================
# Per-phase Configuration Widget
# =============================================================================

class CycleConfigWidget(QWidget):
    """Self-contained configuration panel for one cycling phase (stress or recovery)."""

    def __init__(self, phase_name: str,
                 default_bias_value: float = 2.0,
                 default_bias_duration: float = 60.0,
                 default_num_cycles: int = 10,
                 parent=None):
        super().__init__(parent)
        self.phase_name = phase_name
        self._active_time_design = TIME_DESIGN_LINEAR
        self._cycle_counts_by_design = {
            TIME_DESIGN_LINEAR: int(default_num_cycles),
            TIME_DESIGN_LOG: int(default_num_cycles),
        }
        self._build_ui(default_bias_value, default_bias_duration, default_num_cycles)

    def _build_ui(self, default_bias: float, default_dur: float, default_cycles: int):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        # ---- IV Sweep ----
        sweep_grp = QGroupBox("IV Measurement Sweep")
        sl = QGridLayout(sweep_grp)

        sl.addWidget(QLabel("SMU:"), 0, 0)
        self.spin_smu = QSpinBox()
        self.spin_smu.setRange(1, 10)
        self.spin_smu.setValue(1)
        sl.addWidget(self.spin_smu, 0, 1)

        sl.addWidget(QLabel("Mode:"), 0, 2)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["IV (V→I)", "VI (I→V)"])
        sl.addWidget(self.combo_mode, 0, 3)

        sl.addWidget(QLabel("Start:"), 1, 0)
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setRange(-200, 200)
        self.spin_start.setDecimals(4)
        self.spin_start.setValue(0.0)
        sl.addWidget(self.spin_start, 1, 1)

        sl.addWidget(QLabel("Stop:"), 1, 2)
        self.spin_stop = QDoubleSpinBox()
        self.spin_stop.setRange(-200, 200)
        self.spin_stop.setDecimals(4)
        self.spin_stop.setValue(2.0)
        sl.addWidget(self.spin_stop, 1, 3)

        sl.addWidget(QLabel("Steps:"), 2, 0)
        self.spin_steps = QSpinBox()
        self.spin_steps.setRange(2, 1001)
        self.spin_steps.setValue(21)
        sl.addWidget(self.spin_steps, 2, 1)

        sl.addWidget(QLabel("Dwell (s):"), 2, 2)
        self.spin_dwell = QDoubleSpinBox()
        self.spin_dwell.setRange(0, 10)
        self.spin_dwell.setDecimals(3)
        self.spin_dwell.setValue(0.1)
        sl.addWidget(self.spin_dwell, 2, 3)

        sl.addWidget(QLabel("Compliance:"), 3, 0)
        self.spin_compliance = QDoubleSpinBox()
        self.spin_compliance.setRange(1e-9, 200)
        self.spin_compliance.setDecimals(6)
        self.spin_compliance.setValue(0.1)
        sl.addWidget(self.spin_compliance, 3, 1)

        outer.addWidget(sweep_grp)

        # ---- Bias ----
        bias_grp = QGroupBox(f"{self.phase_name.capitalize()} Bias Parameters")
        bl = QGridLayout(bias_grp)

        bl.addWidget(QLabel("Bias Mode:"), 0, 0)
        self.combo_bias_mode = QComboBox()
        self.combo_bias_mode.addItems(["Constant Voltage", "Constant Current"])
        self.combo_bias_mode.currentIndexChanged.connect(self._on_bias_mode_changed)
        bl.addWidget(self.combo_bias_mode, 0, 1)

        self.lbl_bias_value = QLabel("Bias Value (V):")
        bl.addWidget(self.lbl_bias_value, 0, 2)
        self.spin_bias_value = QDoubleSpinBox()
        self.spin_bias_value.setRange(-200, 200)
        self.spin_bias_value.setDecimals(4)
        self.spin_bias_value.setValue(default_bias)
        bl.addWidget(self.spin_bias_value, 0, 3)

        self.lbl_linear_duration = QLabel("Linear cycle duration (s):")
        bl.addWidget(self.lbl_linear_duration, 1, 0)
        self.spin_duration_linear = QDoubleSpinBox()
        self.spin_duration_linear.setRange(1e-6, 1_000_000)
        self.spin_duration_linear.setDecimals(6)
        self.spin_duration_linear.setSingleStep(0.01)
        self.spin_duration_linear.setValue(default_dur)
        self.spin_duration_linear.setToolTip(
            "Used only in Linear mode: fixed stress duration per cycle."
        )
        self.spin_duration_linear.valueChanged.connect(lambda *_: self._update_cycle_mode_hint())
        bl.addWidget(self.spin_duration_linear, 1, 1)

        self.lbl_log_t0 = QLabel("Log start duration t0 (s):")
        bl.addWidget(self.lbl_log_t0, 1, 2)
        self.spin_duration_log_t0 = QDoubleSpinBox()
        self.spin_duration_log_t0.setRange(1e-6, 1_000_000)
        self.spin_duration_log_t0.setDecimals(6)
        self.spin_duration_log_t0.setSingleStep(0.01)
        self.spin_duration_log_t0.setValue(default_dur)
        self.spin_duration_log_t0.setToolTip(
            "Used only in Log mode: first-cycle stress duration t0."
        )
        self.spin_duration_log_t0.valueChanged.connect(lambda *_: self._update_cycle_mode_hint())
        bl.addWidget(self.spin_duration_log_t0, 1, 3)

        bl.addWidget(QLabel("Sample Rate (s):"), 2, 0)
        self.spin_sample_interval = QDoubleSpinBox()
        self.spin_sample_interval.setRange(0.1, 60)
        self.spin_sample_interval.setDecimals(2)
        self.spin_sample_interval.setValue(1.0)
        bl.addWidget(self.spin_sample_interval, 2, 1)

        bl.addWidget(QLabel("Time Design:"), 2, 2)
        self.combo_time_design = QComboBox()
        self.combo_time_design.addItem("Linear (equal cycle dt)", TIME_DESIGN_LINEAR)
        self.combo_time_design.addItem("Log", TIME_DESIGN_LOG)
        self.combo_time_design.currentIndexChanged.connect(self._on_time_design_changed)
        bl.addWidget(self.combo_time_design, 2, 3)

        self.lbl_log_ppd = QLabel("Log cycles/decade (density D):")
        bl.addWidget(self.lbl_log_ppd, 3, 0)
        self.combo_log_ppd = QComboBox()
        for option in LOG_POINTS_PER_DECADE_OPTIONS:
            self.combo_log_ppd.addItem(str(option), option)
        self.combo_log_ppd.setCurrentIndex(LOG_POINTS_PER_DECADE_OPTIONS.index(5))
        self.combo_log_ppd.setToolTip(
            "Log density D: how many cycles are placed in each decade. "
            "This controls spacing only, not total cycle count N."
        )
        self.combo_log_ppd.currentIndexChanged.connect(lambda *_: self._update_cycle_mode_hint())
        bl.addWidget(self.combo_log_ppd, 3, 1)

        self.lbl_bias_compliance = QLabel("Compliance (A):")
        bl.addWidget(self.lbl_bias_compliance, 3, 2)
        self.spin_bias_compliance = QDoubleSpinBox()
        self.spin_bias_compliance.setRange(1e-9, 200)
        self.spin_bias_compliance.setDecimals(6)
        self.spin_bias_compliance.setValue(0.1)
        bl.addWidget(self.spin_bias_compliance, 3, 3)

        outer.addWidget(bias_grp)

        # ---- Cycle Settings ----
        cycle_grp = QGroupBox("Cycle Settings")
        cl = QGridLayout(cycle_grp)

        cl.addWidget(QLabel("Total Number of Cycles (N):"), 0, 0)
        self.spin_num_cycles = QSpinBox()
        self.spin_num_cycles.setRange(1, 10000)
        self.spin_num_cycles.setValue(default_cycles)
        self.spin_num_cycles.setToolTip(
            "Total loop count N for this phase (Stress/Recovery).")
        self.spin_num_cycles.valueChanged.connect(self._on_cycle_no_changed)
        cl.addWidget(self.spin_num_cycles, 0, 1)

        self.lbl_cycle_mode_hint = QLabel()
        self.lbl_cycle_mode_hint.setStyleSheet("color:#555; font-size:9pt;")
        self.lbl_cycle_mode_hint.setWordWrap(True)
        cl.addWidget(self.lbl_cycle_mode_hint, 1, 0, 1, 4)

        self.check_initial_meas = QCheckBox("Initial Measurement (Baseline)")
        self.check_initial_meas.setChecked(True)
        cl.addWidget(self.check_initial_meas, 2, 0, 1, 4)
        self.check_initial_meas.toggled.connect(lambda *_: self._update_cycle_mode_hint())

        self.spin_steps.valueChanged.connect(lambda *_: self._update_cycle_mode_hint())
        self.spin_dwell.valueChanged.connect(lambda *_: self._update_cycle_mode_hint())

        self._on_time_design_changed()

        outer.addWidget(cycle_grp)

        # ---- Power Meter ----
        pm_grp = QGroupBox("Power Meter")
        pl = QGridLayout(pm_grp)

        self.check_enable_pm = QCheckBox("Enable Power Measurement")
        self.check_enable_pm.setChecked(True)
        pl.addWidget(self.check_enable_pm, 0, 0, 1, 2)

        pl.addWidget(QLabel("Wavelength (nm):"), 1, 0)
        self.spin_wavelength = QDoubleSpinBox()
        self.spin_wavelength.setRange(200, 2000)
        self.spin_wavelength.setValue(850)
        pl.addWidget(self.spin_wavelength, 1, 1)

        outer.addWidget(pm_grp)
        outer.addStretch()

    def _on_bias_mode_changed(self, index: int):
        if index == 0:
            self.lbl_bias_value.setText("Bias Value (V):")
            self.lbl_bias_compliance.setText("Compliance (A):")
            self.spin_bias_compliance.setRange(1e-9, 200)
        else:
            self.lbl_bias_value.setText("Bias Value (A):")
            self.lbl_bias_compliance.setText("Compliance (V):")
            self.spin_bias_compliance.setRange(0, 200)

    def _on_time_design_changed(self, *_):
        new_design = str(self.combo_time_design.currentData() or TIME_DESIGN_LINEAR)

        if not hasattr(self, "spin_num_cycles"):
            is_log_early = new_design == TIME_DESIGN_LOG
            self.lbl_log_ppd.setVisible(is_log_early)
            self.combo_log_ppd.setVisible(is_log_early)
            self._active_time_design = new_design
            return

        # Save current value under the previous active mode, then restore the
        # mode-specific cycle count for the new mode.
        prev_design = self._active_time_design
        self._cycle_counts_by_design[prev_design] = int(self.spin_num_cycles.value())
        self._active_time_design = new_design
        new_count = int(self._cycle_counts_by_design.get(new_design, self.spin_num_cycles.value()))
        if self.spin_num_cycles.value() != new_count:
            self.spin_num_cycles.blockSignals(True)
            self.spin_num_cycles.setValue(new_count)
            self.spin_num_cycles.blockSignals(False)

        is_log = new_design == TIME_DESIGN_LOG
        self.lbl_log_ppd.setVisible(is_log)
        self.combo_log_ppd.setVisible(is_log)
        self._update_cycle_mode_hint()

    def _on_cycle_no_changed(self, value: int):
        self._cycle_counts_by_design[self._active_time_design] = int(value)
        self._update_cycle_mode_hint()

    @staticmethod
    def _build_preview_timing(total_cycles: int, base_duration_s: float,
                              time_design: str, log_points_per_decade: int) -> List[float]:
        base = max(1e-9, float(base_duration_s))
        design = (time_design or TIME_DESIGN_LINEAR).strip().lower()
        if design == TIME_DESIGN_LOG:
            ppd = int(log_points_per_decade)
            if ppd not in LOG_POINTS_PER_DECADE_OPTIONS:
                ppd = min(LOG_POINTS_PER_DECADE_OPTIONS, key=lambda v: abs(v - ppd))
            mantissas = LOG_CYCLE_MANTISSAS[ppd]
            return [base * mantissas[idx % ppd] * (10 ** (idx // ppd)) for idx in range(total_cycles)]
        return [base for _ in range(total_cycles)]

    def _update_cycle_mode_hint(self):
        active_label = "LOG" if self._active_time_design == TIME_DESIGN_LOG else "LINEAR"
        linear_n = int(self._cycle_counts_by_design.get(TIME_DESIGN_LINEAR, self.spin_num_cycles.value()))
        log_n = int(self._cycle_counts_by_design.get(TIME_DESIGN_LOG, self.spin_num_cycles.value()))
        log_density = int(self.combo_log_ppd.currentData() or LOG_POINTS_PER_DECADE_OPTIONS[0])
        active_n = log_n if self._active_time_design == TIME_DESIGN_LOG else linear_n
        linear_dt = float(self.spin_duration_linear.value())
        log_t0 = float(self.spin_duration_log_t0.value())
        linear_bias_total = linear_dt * linear_n
        log_bias_total = sum(self._build_preview_timing(
            log_n, log_t0, TIME_DESIGN_LOG, log_density
        ))
        steps = int(self.spin_steps.value())
        dwell = float(self.spin_dwell.value())
        init_extra = 1 if self.check_initial_meas.isChecked() else 0
        linear_meas_total = (linear_n + init_extra) * steps * dwell
        log_meas_total = (log_n + init_extra) * steps * dwell
        self.lbl_cycle_mode_hint.setText(
            f"Meaning: D={log_density} cycles/decade is log spacing density only; "
            f"N={active_n} is total Stress->Measurement loop count.\n"
            f"Active mode: {active_label} | Stored N -> Linear={linear_n}, Log={log_n}\n"
            f"Estimated total duration (bias only): Linear={linear_bias_total:g}s, Log={log_bias_total:g}s\n"
            f"Estimated total duration (+measurement approx): "
            f"Linear={linear_bias_total + linear_meas_total:g}s, "
            f"Log={log_bias_total + log_meas_total:g}s"
        )

    def cycle_count_for_design(self, design: str) -> int:
        return int(self._cycle_counts_by_design.get(design, self.spin_num_cycles.value()))

    def set_cycle_count_for_design(self, design: str, value: int):
        count = int(max(1, value))
        self._cycle_counts_by_design[design] = count
        if self._active_time_design == design:
            self.spin_num_cycles.blockSignals(True)
            self.spin_num_cycles.setValue(count)
            self.spin_num_cycles.blockSignals(False)
        self._update_cycle_mode_hint()

    def active_time_design(self) -> str:
        return self._active_time_design

    def build_config(self, device_name: str, autosave: bool) -> CycleConfig:
        mode = "iv" if self.combo_mode.currentIndex() == 0 else "vi"
        bias_mode = "voltage" if self.combo_bias_mode.currentIndex() == 0 else "current"
        return CycleConfig(
            sweep=SweepConfig(
                smu=self.spin_smu.value(),
                mode=mode,
                start=self.spin_start.value(),
                stop=self.spin_stop.value(),
                steps=self.spin_steps.value(),
                dwell_s=self.spin_dwell.value(),
                compliance=self.spin_compliance.value()
            ),
            bias=BiasConfig(
                mode=bias_mode,
                value=self.spin_bias_value.value(),
                duration_s=(
                    self.spin_duration_linear.value()
                    if self.active_time_design() == TIME_DESIGN_LINEAR
                    else self.spin_duration_log_t0.value()
                ),
                time_design=str(self.combo_time_design.currentData()),
                log_points_per_decade=int(self.combo_log_ppd.currentData()),
                sample_interval_s=self.spin_sample_interval.value(),
                compliance=self.spin_bias_compliance.value()
            ),
            num_cycles=self.spin_num_cycles.value(),
            initial_measurement=self.check_initial_meas.isChecked(),
            enable_power_meter=self.check_enable_pm.isChecked(),
            power_wavelength_nm=self.spin_wavelength.value(),
            device_name=device_name,
            autosave=autosave
        )


# =============================================================================
# Per-phase Plot Widget
# =============================================================================

class PhaseMonitorWidget(QWidget):
    """Live-updating plot panel for one cycling phase."""

    def __init__(self, phase_label: str, parent=None):
        super().__init__(parent)
        self.phase_label = phase_label
        self._meas_voltages: List[float] = []
        self._meas_currents: List[float] = []
        self._meas_powers: List[float] = []
        self._meas_cycles: List[int] = []
        self._bias_times: List[float] = []
        self._bias_currents: List[float] = []
        self._bias_powers: List[float] = []
        self._sum_cycles: List[int] = []
        self._sum_peak_currents: List[float] = []
        self._sum_peak_powers: List[float] = []

        self._dirty_meas = False
        self._dirty_bias = False
        self._dirty_deg = False

        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.figure = Figure(figsize=(12, 8), dpi=100)
        self.canvas = FigureCanvas(self.figure)

        self.ax_iv = self.figure.add_subplot(2, 2, 1)
        self.ax_li = self.figure.add_subplot(2, 2, 2)
        self.ax_bias = self.figure.add_subplot(2, 2, 3)
        self.ax_deg = self.figure.add_subplot(2, 2, 4)

        self._init_axes()

        # Pre-create persistent twin axes and line objects
        self._ax_bias_twin = self.ax_bias.twinx()
        self.ax_bias.set_ylabel("Current (A)", color="blue")
        self.ax_bias.tick_params(axis='y', labelcolor='blue')
        self._ax_bias_twin.set_ylabel("Optical Power (W)", color="red")
        self._ax_bias_twin.tick_params(axis='y', labelcolor='red')

        self._ax_deg_twin = self.ax_deg.twinx()
        self.ax_deg.set_ylabel("Peak Current (A)", color="blue")
        self.ax_deg.tick_params(axis='y', labelcolor='blue')
        self._ax_deg_twin.set_ylabel("Peak Power (W)", color="red")
        self._ax_deg_twin.tick_params(axis='y', labelcolor='red')

        self._line_bias_I, = self.ax_bias.plot([], [], 'b-', lw=1, label='Current')
        self._line_bias_P, = self._ax_bias_twin.plot([], [], 'r-', lw=1, label='Power')
        self.ax_bias.legend([self._line_bias_I, self._line_bias_P],
                            ['Current', 'Power'], loc='upper right')

        self._line_deg_I, = self.ax_deg.plot([], [], 'bo-', ms=5, label='Peak I')
        self._line_deg_P, = self._ax_deg_twin.plot([], [], 'rs-', ms=5, label='Peak P')
        self.ax_deg.legend([self._line_deg_I, self._line_deg_P],
                           ['Peak Current', 'Peak Power'], loc='upper right')

        self.figure.tight_layout()
        layout.addWidget(self.canvas)

    def _init_axes(self):
        for ax, xlabel, ylabel, title in [
            (self.ax_iv, "Voltage (V)", "Current (A)", f"[{self.phase_label}] I-V"),
            (self.ax_li, "Current (A)", "Optical Power (W)", f"[{self.phase_label}] L-I"),
            (self.ax_bias, "Time (s)", "Current / Power", f"[{self.phase_label}] Bias Monitor"),
            (self.ax_deg, "Cycle", "Peak Values", f"[{self.phase_label}] Degradation"),
        ]:
            ax.clear()
            ax.set_xlabel(xlabel)
            ax.set_ylabel(ylabel)
            ax.set_title(title)
            ax.grid(True, alpha=0.3)
        self.canvas.draw_idle()

    def reset(self):
        self._meas_voltages.clear()
        self._meas_currents.clear()
        self._meas_powers.clear()
        self._meas_cycles.clear()
        self._bias_times.clear()
        self._bias_currents.clear()
        self._bias_powers.clear()
        self._sum_cycles.clear()
        self._sum_peak_currents.clear()
        self._sum_peak_powers.clear()
        self._dirty_meas = self._dirty_bias = self._dirty_deg = False
        # Reset line data
        self._line_bias_I.set_data([], [])
        self._line_bias_P.set_data([], [])
        self._line_deg_I.set_data([], [])
        self._line_deg_P.set_data([], [])
        self._init_axes()

    # ---------------------------------------------------------------------- #
    # Data ingestion
    # ---------------------------------------------------------------------- #

    def add_measurement_point(self, point: MeasurementPoint):
        self._meas_voltages.append(point.voltage)
        self._meas_currents.append(point.current)
        self._meas_powers.append(point.optical_power)
        self._meas_cycles.append(point.cycle)
        self._dirty_meas = True

    def add_bias_point(self, point: BiasPoint):
        self._bias_times.append(point.elapsed_s)
        self._bias_currents.append(point.current)
        self._bias_powers.append(point.optical_power)
        self._dirty_bias = True

    def clear_bias_data(self):
        self._bias_times.clear()
        self._bias_currents.clear()
        self._bias_powers.clear()
        self._line_bias_I.set_data([], [])
        self._line_bias_P.set_data([], [])
        self._dirty_bias = False

    def add_cycle_summary(self, summary: CycleSummary):
        self._sum_cycles.append(summary.cycle)
        self._sum_peak_currents.append(summary.peak_current)
        self._sum_peak_powers.append(summary.peak_power)
        self._dirty_deg = True

    # ---------------------------------------------------------------------- #
    # Plot update (throttled)
    # ---------------------------------------------------------------------- #

    def _flush(self):
        needs_draw = False
        if self._dirty_meas:
            self._dirty_meas = False
            self._update_meas_plots()
            needs_draw = True
        if self._dirty_bias:
            self._dirty_bias = False
            self._update_bias_plot()
            needs_draw = True
        if self._dirty_deg:
            self._dirty_deg = False
            self._update_deg_plot()
            needs_draw = True
        if needs_draw:
            self.canvas.draw_idle()

    def _update_meas_plots(self):
        if not self._meas_voltages:
            return
        cur_cycle = self._meas_cycles[-1]
        mask = [c == cur_cycle for c in self._meas_cycles]
        v = [x for x, m in zip(self._meas_voltages, mask) if m]
        i = [x for x, m in zip(self._meas_currents, mask) if m]
        p = [x for x, m in zip(self._meas_powers, mask) if m]

        self.ax_iv.clear()
        self.ax_iv.plot(v, i, 'b.-', lw=1, ms=3)
        self.ax_iv.set_xlabel("Voltage (V)")
        self.ax_iv.set_ylabel("Current (A)")
        self.ax_iv.set_title(f"[{self.phase_label}] I-V (Cycle {cur_cycle})")
        self.ax_iv.grid(True, alpha=0.3)

        self.ax_li.clear()
        self.ax_li.plot(i, p, 'r.-', lw=1, ms=3)
        self.ax_li.set_xlabel("Current (A)")
        self.ax_li.set_ylabel("Optical Power (W)")
        self.ax_li.set_title(f"[{self.phase_label}] L-I (Cycle {cur_cycle})")
        self.ax_li.grid(True, alpha=0.3)

    def _update_bias_plot(self):
        if not self._bias_times:
            return
        self._line_bias_I.set_data(self._bias_times, self._bias_currents)
        self._line_bias_P.set_data(self._bias_times, self._bias_powers)
        self.ax_bias.relim()
        self.ax_bias.autoscale_view()
        self._ax_bias_twin.relim()
        self._ax_bias_twin.autoscale_view()
        self.ax_bias.set_title(
            f"[{self.phase_label}] Bias Monitor ({len(self._bias_times)} samples)")

    def _update_deg_plot(self):
        if not self._sum_cycles:
            return
        self._line_deg_I.set_data(self._sum_cycles, self._sum_peak_currents)
        self._line_deg_P.set_data(self._sum_cycles, self._sum_peak_powers)
        self.ax_deg.relim()
        self.ax_deg.autoscale_view()
        self._ax_deg_twin.relim()
        self._ax_deg_twin.autoscale_view()

    def stop_timer(self):
        self._timer.stop()


# =============================================================================
# Main GUI
# =============================================================================

class StressRecoveryCycleGUI(QMainWindow):
    """Two-phase stress-recovery cycling test GUI."""

    def __init__(self):
        super().__init__()
        self.b1500 = B1500Controller()
        self.power_meter = ThorlabsPowerMeterController()
        self.worker: Optional[TwoPhaseCycleWorker] = None
        self.resource_worker: Optional[ResourceRefreshWorker] = None
        self._syncing_phase_cycle_controls = False

        self.setWindowTitle("B1500 Stress-Recovery Cycling Test (Two-Phase)")
        self.setMinimumSize(1600, 960)

        self._setup_ui()
        self._wire_phase_cycle_sync()
        self._active_phase: str = "stress"   # track which tab is showing
        self.refresh_resources()

    # ------------------------------------------------------------------ UI --

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)

        # Top-level horizontal splitter so the user can drag the left/right boundary
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)

        # ================================================================
        # LEFT SIDE  – scrollable control panel
        # ================================================================
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(6, 6, 6, 6)
        left_layout.setSpacing(8)

        # The left panel is wrapped in a QScrollArea so that when the window
        # is made narrower / shorter the controls remain reachable.
        # * setWidgetResizable(True) lets the panel grow to fill empty space,
        #   but the panel's minimumSizeHint() still drives the scroll bars:
        #   as soon as the viewport is smaller than that hint, scroll bars appear.
        # * Horizontal scroll is disabled; vertical is always available when needed.
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        left_scroll.setMinimumWidth(460)        # minimum drag width
        left_scroll.setWidget(left_panel)

        # 1. Device Connection
        left_layout.addWidget(self._make_connection_group())

        # 2. Phase config tabs
        #    Each tab wraps its CycleConfigWidget in its own QScrollArea so the
        #    individual phase settings panels are independently scrollable and
        #    can be resized by dragging the tab area's height.
        phase_tabs = QTabWidget()
        # Give the tab widget a generous minimum height so it does not get
        # squashed, and no maximum so it can grow when the window is tall.
        phase_tabs.setMinimumHeight(620)

        stress_scroll = QScrollArea()
        stress_scroll.setWidgetResizable(True)
        stress_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        stress_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.stress_cfg = CycleConfigWidget("stress",
                                            default_bias_value=2.0,
                                            default_bias_duration=60.0,
                                            default_num_cycles=10)
        stress_scroll.setWidget(self.stress_cfg)
        phase_tabs.addTab(stress_scroll, "⚡ Stress Phase")

        recovery_scroll = QScrollArea()
        recovery_scroll.setWidgetResizable(True)
        recovery_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        recovery_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.recovery_cfg = CycleConfigWidget("recovery",
                                              default_bias_value=0.0,
                                              default_bias_duration=120.0,
                                              default_num_cycles=10)
        recovery_scroll.setWidget(self.recovery_cfg)
        phase_tabs.addTab(recovery_scroll, "🔄 Recovery Phase")

        # Use a vertical splitter so the user can drag the boundary between
        # the phase-config tabs and the controls below them.
        left_vsplit = QSplitter(Qt.Vertical)
        left_vsplit.setChildrenCollapsible(False)
        left_vsplit.addWidget(phase_tabs)

        # Lower section of the left panel: output + buttons + log
        lower_widget = QWidget()
        lower_layout = QVBoxLayout(lower_widget)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(6)

        # 3. Output
        lower_layout.addWidget(self._make_output_group())

        # 4. Control buttons
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("▶ Start Test")
        self.btn_start.setMinimumHeight(42)
        self.btn_start.setStyleSheet(
            "background-color:#4CAF50;color:white;font-weight:bold;font-size:14px;")
        self.btn_start.clicked.connect(self.start_test)
        btn_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■ Stop")
        self.btn_stop.setMinimumHeight(42)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet("font-size:14px;")
        self.btn_stop.clicked.connect(self.stop_test)
        btn_layout.addWidget(self.btn_stop)
        lower_layout.addLayout(btn_layout)

        # Progress bar
        self.progress_bar = QProgressBar()
        lower_layout.addWidget(self.progress_bar)

        # Phase label
        self.lbl_phase = QLabel("Phase: IDLE")
        self.lbl_phase.setStyleSheet("font-weight:bold;font-size:12px;")
        lower_layout.addWidget(self.lbl_phase)

        # Log
        log_grp = QGroupBox("Log")
        log_lay = QVBoxLayout(log_grp)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(100)
        log_lay.addWidget(self.log_text)
        lower_layout.addWidget(log_grp)

        left_vsplit.addWidget(lower_widget)
        # Start the vertical split roughly 65 % / 35 %
        left_vsplit.setSizes([620, 280])

        left_layout.addWidget(left_vsplit)

        main_splitter.addWidget(left_scroll)

        # ================================================================
        # RIGHT SIDE  – plots
        # ================================================================
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        self.plot_tabs = QTabWidget()

        self.stress_plot = PhaseMonitorWidget("Stress")
        self.recovery_plot = PhaseMonitorWidget("Recovery")
        self.plot_tabs.addTab(self.stress_plot, "Stress Phase Plots")
        self.plot_tabs.addTab(self.recovery_plot, "Recovery Phase Plots")

        # Summary table tab
        table_tab = QWidget()
        table_lay = QVBoxLayout(table_tab)
        summary_sub_tabs = QTabWidget()

        self.stress_table = self._make_summary_table()
        summary_sub_tabs.addTab(self.stress_table, "Stress Summaries")
        self.recovery_table = self._make_summary_table()
        summary_sub_tabs.addTab(self.recovery_table, "Recovery Summaries")
        table_lay.addWidget(summary_sub_tabs)
        self.plot_tabs.addTab(table_tab, "Cycle Summaries")

        right_layout.addWidget(self.plot_tabs)

        main_splitter.addWidget(right_panel)

        # Initial split: ~500 px left, rest goes to the right panel
        main_splitter.setSizes([500, 1100])
        main_splitter.setStretchFactor(0, 0)   # left: does not auto-stretch
        main_splitter.setStretchFactor(1, 1)   # right: absorbs extra space

        root.addWidget(main_splitter)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready – connect instruments to begin")

    def _wire_phase_cycle_sync(self):
        # Keep time-design cycle controls synchronized between stress/recovery.
        self.stress_cfg.combo_time_design.currentIndexChanged.connect(
            lambda *_: self._sync_phase_cycle_controls(self.stress_cfg, self.recovery_cfg)
        )
        self.recovery_cfg.combo_time_design.currentIndexChanged.connect(
            lambda *_: self._sync_phase_cycle_controls(self.recovery_cfg, self.stress_cfg)
        )
        self.stress_cfg.combo_log_ppd.currentIndexChanged.connect(
            lambda *_: self._sync_phase_cycle_controls(self.stress_cfg, self.recovery_cfg)
        )
        self.recovery_cfg.combo_log_ppd.currentIndexChanged.connect(
            lambda *_: self._sync_phase_cycle_controls(self.recovery_cfg, self.stress_cfg)
        )
        self.stress_cfg.spin_num_cycles.valueChanged.connect(
            lambda *_: self._sync_phase_cycle_controls(self.stress_cfg, self.recovery_cfg)
        )
        self.recovery_cfg.spin_num_cycles.valueChanged.connect(
            lambda *_: self._sync_phase_cycle_controls(self.recovery_cfg, self.stress_cfg)
        )
        self.stress_cfg.spin_duration_linear.valueChanged.connect(
            lambda *_: self._sync_phase_cycle_controls(self.stress_cfg, self.recovery_cfg)
        )
        self.recovery_cfg.spin_duration_linear.valueChanged.connect(
            lambda *_: self._sync_phase_cycle_controls(self.recovery_cfg, self.stress_cfg)
        )
        self.stress_cfg.spin_duration_log_t0.valueChanged.connect(
            lambda *_: self._sync_phase_cycle_controls(self.stress_cfg, self.recovery_cfg)
        )
        self.recovery_cfg.spin_duration_log_t0.valueChanged.connect(
            lambda *_: self._sync_phase_cycle_controls(self.recovery_cfg, self.stress_cfg)
        )

    def _sync_phase_cycle_controls(self, source: CycleConfigWidget,
                                   target: CycleConfigWidget):
        if self._syncing_phase_cycle_controls:
            return

        self._syncing_phase_cycle_controls = True
        try:
            target.combo_time_design.setCurrentIndex(source.combo_time_design.currentIndex())
            target.combo_log_ppd.setCurrentIndex(source.combo_log_ppd.currentIndex())
            target.spin_duration_linear.setValue(source.spin_duration_linear.value())
            target.spin_duration_log_t0.setValue(source.spin_duration_log_t0.value())
            target.set_cycle_count_for_design(
                TIME_DESIGN_LINEAR,
                source.cycle_count_for_design(TIME_DESIGN_LINEAR)
            )
            target.set_cycle_count_for_design(
                TIME_DESIGN_LOG,
                source.cycle_count_for_design(TIME_DESIGN_LOG)
            )
        finally:
            self._syncing_phase_cycle_controls = False

    def _make_connection_group(self) -> QGroupBox:
        grp = QGroupBox("Instrument Connection")
        lay = QGridLayout(grp)

        lay.addWidget(QLabel("B1500 (GPIB):"), 0, 0)
        self.combo_b1500 = QComboBox()
        self.combo_b1500.setMinimumWidth(180)
        lay.addWidget(self.combo_b1500, 0, 1)
        self.btn_connect_b1500 = QPushButton("Connect")
        self.btn_connect_b1500.clicked.connect(self.connect_b1500)
        lay.addWidget(self.btn_connect_b1500, 0, 2)
        self.lbl_b1500_status = QLabel("Not connected")
        self.lbl_b1500_status.setStyleSheet("color:red;")
        lay.addWidget(self.lbl_b1500_status, 1, 0, 1, 3)

        lay.addWidget(QLabel("Power Meter (USB):"), 2, 0)
        self.combo_pm = QComboBox()
        lay.addWidget(self.combo_pm, 2, 1)
        self.btn_connect_pm = QPushButton("Connect")
        self.btn_connect_pm.clicked.connect(self.connect_power_meter)
        lay.addWidget(self.btn_connect_pm, 2, 2)
        self.lbl_pm_status = QLabel("Not connected")
        self.lbl_pm_status.setStyleSheet("color:red;")
        lay.addWidget(self.lbl_pm_status, 3, 0, 1, 3)

        self.btn_refresh = QPushButton("Refresh Devices")
        self.btn_refresh.clicked.connect(self.refresh_resources)
        lay.addWidget(self.btn_refresh, 4, 0, 1, 3)
        return grp

    def _make_output_group(self) -> QGroupBox:
        grp = QGroupBox("Output")
        lay = QGridLayout(grp)

        lay.addWidget(QLabel("Folder:"), 0, 0)
        self.edit_folder = QLineEdit(str(Path(__file__).parent / "results"))
        lay.addWidget(self.edit_folder, 0, 1)
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self.browse_folder)
        lay.addWidget(btn_browse, 0, 2)

        lay.addWidget(QLabel("Device Name:"), 1, 0)
        self.edit_device = QLineEdit("Device_001")
        lay.addWidget(self.edit_device, 1, 1, 1, 2)

        self.check_autosave = QCheckBox("Autosave Data")
        self.check_autosave.setChecked(True)
        lay.addWidget(self.check_autosave, 2, 0, 1, 3)
        return grp

    @staticmethod
    def _make_summary_table() -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(6)
        t.setHorizontalHeaderLabels([
            "Cycle", "Timestamp", "Peak Current (A)", "Peak Power (W)",
            "Threshold V", "Series R (Ω)"
        ])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return t

    # ---------------------------------------------------------------- Slots --

    def log(self, message: str):
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum())

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.edit_folder.setText(folder)

    def refresh_resources(self):
        if self.resource_worker and self.resource_worker.isRunning():
            return
        self.combo_b1500.clear()
        self.combo_pm.clear()
        self.btn_refresh.setEnabled(False)
        self.status_bar.showMessage("Scanning VISA resources…")
        self.resource_worker = ResourceRefreshWorker(self.b1500.list_all_resources)
        self.resource_worker.resources_ready.connect(self._on_resources_ready)
        self.resource_worker.refresh_failed.connect(
            lambda e: self.status_bar.showMessage(f"Scan failed: {e}"))
        self.resource_worker.finished.connect(
            lambda: self.btn_refresh.setEnabled(True))
        self.resource_worker.start()

    def _on_resources_ready(self, resources: List[str]):
        gpib = [r for r in resources if "GPIB" in r.upper()]
        usb = [r for r in resources if "USB" in r.upper()]
        self.combo_b1500.addItems(gpib)
        self.combo_pm.addItems(usb)
        self.status_bar.showMessage(
            f"Found {len(gpib)} GPIB, {len(usb)} USB resources")
        self.log(f"Devices: {len(gpib)} GPIB, {len(usb)} USB")

    def connect_b1500(self):
        if self.b1500.connected:
            self.b1500.disconnect()
            self.lbl_b1500_status.setText("Not connected")
            self.lbl_b1500_status.setStyleSheet("color:red;")
            self.btn_connect_b1500.setText("Connect")
        else:
            res = self.combo_b1500.currentText()
            if not res:
                QMessageBox.warning(self, "Error", "No B1500 resource selected")
                return
            ok, msg = self.b1500.connect(res)
            if ok:
                self.lbl_b1500_status.setText(f"Connected: {self.b1500.idn[:45]}")
                self.lbl_b1500_status.setStyleSheet("color:green;")
                self.btn_connect_b1500.setText("Disconnect")
                self.log(f"B1500: {self.b1500.idn}")
            else:
                QMessageBox.warning(self, "Connection Failed", msg)

    def connect_power_meter(self):
        if self.power_meter.connected:
            self.power_meter.disconnect()
            self.lbl_pm_status.setText("Not connected")
            self.lbl_pm_status.setStyleSheet("color:red;")
            self.btn_connect_pm.setText("Connect")
        else:
            res = self.combo_pm.currentText()
            if not res:
                QMessageBox.warning(self, "Error", "No power meter resource selected")
                return
            ok, msg = self.power_meter.connect(res)
            if ok:
                self.lbl_pm_status.setText(f"Connected: {self.power_meter.idn[:45]}")
                self.lbl_pm_status.setStyleSheet("color:green;")
                self.btn_connect_pm.setText("Disconnect")
                self.log(f"Power meter: {self.power_meter.idn}")
            else:
                QMessageBox.warning(self, "Connection Failed", msg)

    # ---------------------------------------------------------------------- #
    # Test control
    # ---------------------------------------------------------------------- #

    def start_test(self):
        if not self.b1500.connected and not self.power_meter.connected:
            QMessageBox.warning(self, "Error", "No instruments connected")
            return

        device = self.edit_device.text().strip() or "Device"
        autosave = self.check_autosave.isChecked()

        # Create shared session folder with subfolders
        base = Path(self.edit_folder.text())
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_device = device.replace(" ", "_").replace("/", "_").replace("\\", "_")
        session_root = base / f"{safe_device}_stress_recovery_cycle_{ts}"
        stress_folder = session_root / "stress"
        recovery_folder = session_root / "recovery"
        session_root.mkdir(parents=True, exist_ok=True)
        stress_folder.mkdir(parents=True, exist_ok=True)
        recovery_folder.mkdir(parents=True, exist_ok=True)

        self.log(f"Session folder: {session_root}")
        self.log(f"  Stress data  → {stress_folder}")
        self.log(f"  Recovery data→ {recovery_folder}")

        # Build configs
        stress_config = self.stress_cfg.build_config(device, autosave)
        recovery_config = self.recovery_cfg.build_config(device, autosave)

        # Build engines with preset folders
        stress_engine = CyclingEngine(self.b1500, self.power_meter,
                                      stress_config, preset_folder=stress_folder)
        recovery_engine = CyclingEngine(self.b1500, self.power_meter,
                                        recovery_config, preset_folder=recovery_folder)

        # Reset plots and tables
        self.stress_plot.reset()
        self.recovery_plot.reset()
        self.stress_table.setRowCount(0)
        self.recovery_table.setRowCount(0)
        self.progress_bar.setValue(0)

        # Create and connect worker
        self.worker = TwoPhaseCycleWorker(stress_engine, recovery_engine)

        self.worker.stress_measurement_point.connect(
            self.stress_plot.add_measurement_point)
        self.worker.stress_bias_point.connect(
            lambda p: self._on_bias_point(p, "stress"))
        self.worker.stress_phase_change.connect(
            lambda p: self._on_phase_change(p, "stress"))
        self.worker.stress_cycle_complete.connect(
            lambda c: self._on_cycle_complete(c, "stress", stress_engine))
        self.worker.stress_progress.connect(
            lambda c, t: self._on_progress(c, t, "stress"))

        self.worker.recovery_measurement_point.connect(
            self.recovery_plot.add_measurement_point)
        self.worker.recovery_bias_point.connect(
            lambda p: self._on_bias_point(p, "recovery"))
        self.worker.recovery_phase_change.connect(
            lambda p: self._on_phase_change(p, "recovery"))
        self.worker.recovery_cycle_complete.connect(
            lambda c: self._on_cycle_complete(c, "recovery", recovery_engine))
        self.worker.recovery_progress.connect(
            lambda c, t: self._on_progress(c, t, "recovery"))

        self.worker.log_message.connect(self.log)
        self.worker.overall_phase.connect(self._on_overall_phase)
        self.worker.finished_signal.connect(self.on_test_complete)

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.worker.start()

    def stop_test(self):
        if self.worker:
            self.worker.stop()
            self.log("Stop requested…")

    def _on_overall_phase(self, phase: str):
        if phase == "stress":
            self.plot_tabs.setCurrentIndex(0)
            self._active_phase = "stress"
            self.lbl_phase.setText("PHASE 1: Stress Cycling")
            self.lbl_phase.setStyleSheet("font-weight:bold;font-size:12px;color:orange;")
        elif phase == "recovery":
            self.plot_tabs.setCurrentIndex(1)
            self._active_phase = "recovery"
            self.lbl_phase.setText("PHASE 2: Recovery Cycling")
            self.lbl_phase.setStyleSheet("font-weight:bold;font-size:12px;color:#1976D2;")
        elif phase == "done":
            self.lbl_phase.setText("COMPLETED")
            self.lbl_phase.setStyleSheet("font-weight:bold;font-size:12px;color:green;")

    def _on_phase_change(self, phase: TestPhase, which: str):
        colors = {
            TestPhase.IDLE: "gray",
            TestPhase.MEASUREMENT: "#1565C0",
            TestPhase.BIAS: "orange",
            TestPhase.COMPLETED: "green",
            TestPhase.STOPPED: "red"
        }
        if which == self._active_phase:
            color = colors.get(phase, "black")
            label = f"[{which.upper()}] {phase.value.upper()}"
            self.lbl_phase.setText(label)
            self.lbl_phase.setStyleSheet(
                f"font-weight:bold;font-size:12px;color:{color};")

        # Clear bias plot data when entering a new bias phase
        if phase == TestPhase.BIAS:
            if which == "stress":
                self.stress_plot.clear_bias_data()
            else:
                self.recovery_plot.clear_bias_data()

    def _on_bias_point(self, point: BiasPoint, which: str):
        if which == "stress":
            self.stress_plot.add_bias_point(point)
        else:
            self.recovery_plot.add_bias_point(point)

    def _on_cycle_complete(self, cycle: int, which: str, engine: CyclingEngine):
        if not engine.cycle_summaries:
            return
        summary = engine.cycle_summaries[-1]
        table = self.stress_table if which == "stress" else self.recovery_table
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(str(summary.cycle)))
        table.setItem(row, 1, QTableWidgetItem(summary.timestamp[:19]))
        table.setItem(row, 2, QTableWidgetItem(f"{summary.peak_current:.4e}"))
        table.setItem(row, 3, QTableWidgetItem(f"{summary.peak_power:.4e}"))
        table.setItem(row, 4, QTableWidgetItem(f"{summary.threshold_voltage:.4f}"))
        table.setItem(row, 5, QTableWidgetItem(f"{summary.series_resistance:.2f}"))

        plot = self.stress_plot if which == "stress" else self.recovery_plot
        plot.add_cycle_summary(summary)

    def _on_progress(self, current: int, total: int, which: str):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_bar.showMessage(
            f"[{which.upper()}] Cycle {current}/{total}")

    def on_test_complete(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.status_bar.showMessage("Test complete")
        self.log("=" * 55)
        self.log("TEST COMPLETED")

    # ---------------------------------------------------------------- Close --

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        self.stress_plot.stop_timer()
        self.recovery_plot.stop_timer()
        self.b1500.disconnect()
        self.power_meter.disconnect()
        event.accept()


# =============================================================================
# Entry point
# =============================================================================

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    font = QFont()
    font.setPointSize(10)
    app.setFont(font)
    window = StressRecoveryCycleGUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
