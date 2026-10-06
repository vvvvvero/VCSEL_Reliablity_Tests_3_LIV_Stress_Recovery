"""Basic programmatic usage: a two-phase stress-recovery run without the GUI."""

from stress_recovery_cycle import (
    B1500Controller,
    ThorlabsPowerMeterController,
    StressRecoverySession,
    StressRecoveryConfig,
    CycleConfig,
    SweepConfig,
    BiasConfig,
    SeriesMetadata,
)


b1500 = B1500Controller()
pm = ThorlabsPowerMeterController()

sweep = SweepConfig(smu=1, mode="iv", start=0.0, stop=2.0, steps=21)

cfg = StressRecoveryConfig(
    # Phase 1: 10 cycles at 2.0 V, log-spaced durations 60, 120, 180, 300, 480, 600, ... s
    stress=CycleConfig(
        sweep=sweep,
        bias=BiasConfig(mode="voltage", value=2.0, duration_s=60.0,
                        time_design="log", log_points_per_decade=5),
        num_cycles=10,
    ),
    # Phase 2: 10 cycles at 0 V, 120 s each
    recovery=CycleConfig(
        sweep=sweep,
        bias=BiasConfig(mode="voltage", value=0.0, duration_s=120.0),
        num_cycles=10,
    ),
    device_name="Device_001",
    output_folder="results",
    metadata=SeriesMetadata(wafer_id="W01", operator="VGZ"),
)

# Connect instruments before running.
# b1500.connect("GPIB0::17::INSTR")
# pm.connect("USB0::0x1313::0x8078::...")
# session = StressRecoverySession(b1500, pm, cfg)
# session.run()
# print(session.session_root)
