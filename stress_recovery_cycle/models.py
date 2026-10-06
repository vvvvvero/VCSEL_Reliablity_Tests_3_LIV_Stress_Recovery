"""Data models, enums and timing design for stress-recovery cycling."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple


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
    # Output settings (the phase subfolder is set by the session before run)
    device_name: str = "Device_001"
    autosave: bool = True
    output_folder: str = "results"     # only used when an engine runs standalone


@dataclass
class SeriesMetadata:
    """Session identifiers shared across the VGZ-VRLS series (Series V1 schema)."""
    project_id: str = "VGZ-VRLS"
    wafer_id: str = ""
    device_id: str = ""                # defaults to the device name when empty
    session_id: str = ""               # defaults to the session folder name when empty
    parent_session_id: str = ""
    operator: str = ""
    protocol_name: str = "stress_recovery_cycle"
    protocol_version: str = "1.0.0"
    schema_version: str = "series-v1"


@dataclass
class StressRecoveryConfig:
    """Configuration for a complete two-phase run: stress cycling, then recovery cycling."""
    stress: CycleConfig = field(default_factory=CycleConfig)
    recovery: CycleConfig = field(default_factory=lambda: CycleConfig(
        bias=BiasConfig(value=0.0, duration_s=120.0)))
    device_name: str = "Device_001"
    output_folder: str = "results"
    autosave: bool = True
    metadata: SeriesMetadata = field(default_factory=SeriesMetadata)


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


def build_cycle_timing_plan(total_cycles: int, base_duration_s: float,
                            time_design: str,
                            log_points_per_decade: int) -> Tuple[List[float], List[float]]:
    """Return per-cycle bias durations and cumulative elapsed times for one phase.

    Linear design: every cycle lasts ``base_duration_s``.
    Log design: durations follow ``t0 * m * 10**decade``, with the mantissas
    ``m`` set by the density (cycles per decade).
    """
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
