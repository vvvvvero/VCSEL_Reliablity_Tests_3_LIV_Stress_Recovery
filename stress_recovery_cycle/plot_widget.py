"""Live-updating plot panel for one cycling phase."""

from typing import List

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QVBoxLayout, QWidget

import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from .models import BiasPoint, CycleSummary, MeasurementPoint


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
