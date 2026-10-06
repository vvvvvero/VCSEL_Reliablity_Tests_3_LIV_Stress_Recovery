"""Main window for the two-phase stress-recovery cycling test."""

from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSplitter, QStatusBar, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .b1500_controller import B1500Controller
from .config_widget import CycleConfigWidget
from .cycling_engine import CyclingEngine
from .models import (
    TIME_DESIGN_LINEAR,
    TIME_DESIGN_LOG,
    BiasPoint,
    SeriesMetadata,
    StressRecoveryConfig,
    TestPhase,
)
from .plot_widget import PhaseMonitorWidget
from .session import StressRecoverySession
from .thorlabs_power_meter import ThorlabsPowerMeterController
from .worker_thread import ResourceRefreshWorker, TwoPhaseCycleWorker


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
        self.edit_folder = QLineEdit(str(Path.cwd() / "results"))
        lay.addWidget(self.edit_folder, 0, 1)
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self.browse_folder)
        lay.addWidget(btn_browse, 0, 2)

        lay.addWidget(QLabel("Device Name:"), 1, 0)
        self.edit_device = QLineEdit("Device_001")
        lay.addWidget(self.edit_device, 1, 1, 1, 2)

        lay.addWidget(QLabel("Wafer ID:"), 2, 0)
        self.edit_wafer = QLineEdit("")
        lay.addWidget(self.edit_wafer, 2, 1, 1, 2)

        lay.addWidget(QLabel("Operator:"), 3, 0)
        self.edit_operator = QLineEdit("")
        lay.addWidget(self.edit_operator, 3, 1, 1, 2)

        lay.addWidget(QLabel("Parent Session:"), 4, 0)
        self.edit_parent_session = QLineEdit("")
        self.edit_parent_session.setToolTip(
            "session_id of the previous run on this device (Series V1 chaining); "
            "leave empty for the first run.")
        lay.addWidget(self.edit_parent_session, 4, 1, 1, 2)

        self.check_autosave = QCheckBox("Autosave Data")
        self.check_autosave.setChecked(True)
        lay.addWidget(self.check_autosave, 5, 0, 1, 3)
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

        config = StressRecoveryConfig(
            stress=self.stress_cfg.build_config(device, autosave),
            recovery=self.recovery_cfg.build_config(device, autosave),
            device_name=device,
            output_folder=self.edit_folder.text(),
            autosave=autosave,
            metadata=SeriesMetadata(
                wafer_id=self.edit_wafer.text().strip(),
                operator=self.edit_operator.text().strip(),
                parent_session_id=self.edit_parent_session.text().strip(),
            ),
        )
        session = StressRecoverySession(self.b1500, self.power_meter, config)
        stress_engine = session.stress_engine
        recovery_engine = session.recovery_engine

        self.log(f"Session folder: {session.session_root}")
        self.log(f"  Stress data  → {session.stress_folder}")
        self.log(f"  Recovery data→ {session.recovery_folder}")

        # Reset plots and tables
        self.stress_plot.reset()
        self.recovery_plot.reset()
        self.stress_table.setRowCount(0)
        self.recovery_table.setRowCount(0)
        self.progress_bar.setValue(0)

        # Create and connect worker
        self.worker = TwoPhaseCycleWorker(session)

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
