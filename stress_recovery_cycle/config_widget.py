"""Per-phase configuration panel (sweep, bias, cycle timing, power meter)."""

from typing import List

from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QLabel,
    QSpinBox, QVBoxLayout, QWidget,
)

from .models import (
    LOG_POINTS_PER_DECADE_OPTIONS,
    TIME_DESIGN_LINEAR,
    TIME_DESIGN_LOG,
    BiasConfig,
    CycleConfig,
    SweepConfig,
    build_cycle_timing_plan,
)


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

    def _update_cycle_mode_hint(self):
        active_label = "LOG" if self._active_time_design == TIME_DESIGN_LOG else "LINEAR"
        linear_n = int(self._cycle_counts_by_design.get(TIME_DESIGN_LINEAR, self.spin_num_cycles.value()))
        log_n = int(self._cycle_counts_by_design.get(TIME_DESIGN_LOG, self.spin_num_cycles.value()))
        log_density = int(self.combo_log_ppd.currentData() or LOG_POINTS_PER_DECADE_OPTIONS[0])
        active_n = log_n if self._active_time_design == TIME_DESIGN_LOG else linear_n
        linear_dt = float(self.spin_duration_linear.value())
        log_t0 = float(self.spin_duration_log_t0.value())
        linear_bias_total = linear_dt * linear_n
        log_bias_total = sum(build_cycle_timing_plan(
            log_n, log_t0, TIME_DESIGN_LOG, log_density
        )[0])
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
