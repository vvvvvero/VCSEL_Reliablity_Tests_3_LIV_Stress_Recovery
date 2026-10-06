# Stress-Recovery Cycle Library

## Series Context

This repository is part of the Veronica GaoZhan VCSEL Reliability Test Series.

- Series ID: VGZ-VRLS
- Track: Single-device reliability progression
- Position: 3 (stress-recovery cycle)
- Protocol name: stress_recovery_cycle
- Author: Veronica GaoZhan

### Related Repositories

- Stress cycle protocol: https://github.com/vvvvvero/VCSEL_Reliablity_Tests_2_LIV_Constant_Stress
- LIV rollover baseline: https://github.com/vvvvvero/b1500_powermeter_LIV_rollover
- Step stress protocol: https://github.com/vvvvvero/Laser_Optical_Reliablity_Tests_1_Step_Stress
- Wafer mapping automation: https://github.com/vvvvvero/Cascade_Summit12k_Keysight_B1500_Thorlabs_Powermeter_TestAutomation
- B1500 + Avantes synchronized spectra: https://github.com/vvvvvero/Keysight-B1500-Avantes-Spectrometer-Synchronized-Measurement

### Standard Session Fields (Series V1)

Runs include these common identifiers in metadata and outputs:

- project_id
- wafer_id
- device_id
- session_id
- parent_session_id
- protocol_name
- protocol_version
- schema_version

Series data contract: [SERIES_V1_SCHEMA.md](SERIES_V1_SCHEMA.md)

A modular Python library for two-phase stress-recovery cycling tests using a
Keysight B1500 and a Thorlabs power meter.

## Test Protocol

Two cycling phases run back to back on the same instruments, each with the structure

```
Measurement -> Bias -> Measurement -> Bias -> ...   (N cycles)
```

| Phase | Purpose | Typical bias |
|---|---|---|
| 1 Stress | drive degradation | high voltage or current |
| 2 Recovery | observe relaxation | low or zero bias |

Each phase has fully independent parameters: IV/VI sweep range, bias mode and
value, compliance, number of cycles, and bias timing. If the test is stopped
during the stress phase, the recovery phase is skipped.

### Bias timing design

- **Linear**: every cycle is biased for the same duration `dt`.
- **Log**: durations follow `t0 * m * 10^decade`, with the density D
  (cycles per decade) choosing the mantissas `m`:

| D | mantissas |
|---|---|
| 1 | 1 |
| 2 | 1, 3 |
| 3 | 1, 2, 5 |
| 5 | 1, 2, 3, 5, 8 |
| 10 | 1, 1.3, 1.6, 2, 2.5, 3.2, 4, 5, 6.3, 8 |

D sets the spacing only; the total number of cycles is N. For example, `t0 = 60 s`
with D = 5 gives 60, 120, 180, 300, 480, 600, 1200, ... s.

## Features

- Two-phase flow: stress cycling, then recovery cycling
- Constant-voltage or constant-current bias, set per phase
- Linear or logarithmic bias timing
- Real-time bias monitoring of current and optical power
- Cycle-by-cycle summaries (peak current, peak power, threshold voltage, series resistance)
- Session manifest aligned to the Series V1 schema
- PyQt5 GUI with live plots for each phase
- Runs without the GUI through `StressRecoverySession`

## Project Structure

```
stress_recovery_cycle_lib/
├── stress_recovery_cycle/
│   ├── __init__.py
│   ├── models.py                 # configs, data points, timing design
│   ├── b1500_controller.py
│   ├── thorlabs_power_meter.py
│   ├── cycling_engine.py         # one phase: measurement -> bias x N
│   ├── session.py                # both phases, folder layout, manifest (no Qt)
│   ├── worker_thread.py
│   ├── config_widget.py          # per-phase settings panel
│   ├── plot_widget.py            # per-phase live plots
│   ├── gui.py
│   ├── app.py                    # legacy names of the single-file script
│   ├── main.py
│   └── __main__.py
├── examples/
│   └── basic_usage.py
├── tests/
├── main.py
├── setup.py
├── requirements.txt
├── SERIES_V1_SCHEMA.md
└── README.md
```

## Installation

```bash
git clone https://github.com/vvvvvero/VCSEL_Reliablity_Tests_3_LIV_Stress_Recovery.git
cd VCSEL_Reliablity_Tests_3_LIV_Stress_Recovery
pip install -r requirements.txt
```

## Quick Start

```bash
python main.py
```

Or after installation:

```bash
stress-recovery-cycle
```

Without the GUI, see [examples/basic_usage.py](examples/basic_usage.py).

## Output Files

Each run writes one timestamped session folder:

```
<output_folder>/<device>_stress_recovery_cycle_<YYYYMMDD_HHMMSS>/
├── session_manifest.json
├── stress/
│   ├── measurement_cycle_000.csv     # baseline, if enabled
│   ├── measurement_cycle_NNN.csv
│   ├── bias_cycle_NNN.csv
│   └── cycle_summary.csv
└── recovery/
    └── (same files)
```

| File | Columns |
|---|---|
| `measurement_cycle_NNN.csv` | Point, Timestamp, Setpoint, Voltage_V, Current_A, Optical_Power_W, Status |
| `bias_cycle_NNN.csv` | Timestamp, Elapsed_s, Voltage_V, Current_A, Optical_Power_W, Status |
| `cycle_summary.csv` | Cycle, Timestamp, Peak_Current_A, Peak_Power_W, Threshold_V, Series_Resistance_Ohm |

`session_manifest.json` holds the Series V1 fields, start and end times (UTC),
instrument IDs, whether the run completed or the phase it was stopped in, and
each phase's full parameters, including the bias duration of every cycle.

Results go to `./results` in the folder the GUI is started from, unless another
folder is chosen.

## Running the tests

```bash
pip install -e .
pip install pytest
pytest tests/
```

The tests run without instruments: disconnected controllers take the engine's
simulation branches.

## Citation

If you use this library in research, please cite:

```text
GaoZhan, V. (2026). Stress-Recovery Cycle Library.
Retrieved from https://github.com/vvvvvero/VCSEL_Reliablity_Tests_3_LIV_Stress_Recovery
```

## Support

For issues, questions, or suggestions, please open an issue on GitHub.

## License

MIT License
