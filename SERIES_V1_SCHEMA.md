# Veronica GaoZhan VCSEL Reliability Series V1 Schema

This file defines the shared metadata and output conventions used across the series.

## Required metadata fields

- project_id
- wafer_id
- device_id
- session_id
- parent_session_id
- protocol_name
- protocol_version
- schema_version
- operator
- timestamp_start_utc
- timestamp_end_utc

## Required units in column names

- Voltage: *_V
- Current: *_A
- Optical power: *_W
- Time: *_s or *_utc
- Wavelength: *_nm
- Temperature: *_C

## Minimum output set

- session_manifest.json
- measurements.csv
- summary.csv
- events.csv

## Protocol-specific optional outputs

- stress_monitor.csv
- spectrum.csv or spectrum_*.csv
- transient_waveform.csv
- pulse_train.csv

## Session chaining

Use parent_session_id to connect runs in chronological order for the same device_id.
Use project_id plus device_id plus session_id as globally unique series coordinates.
