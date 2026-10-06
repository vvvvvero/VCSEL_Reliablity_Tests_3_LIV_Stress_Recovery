"""QThread workers: the two-phase session runner and the VISA resource scan."""

from typing import Callable, List

from PyQt5.QtCore import QThread, pyqtSignal

from .session import StressRecoverySession


class TwoPhaseCycleWorker(QThread):
    """Runs a StressRecoverySession (stress phase, then recovery phase) off the GUI thread."""

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

    def __init__(self, session: StressRecoverySession):
        super().__init__()
        self.session = session
        self._se = session.stress_engine
        self._re = session.recovery_engine
        self._bind_stress()
        self._bind_recovery()
        session.on_log = lambda m: self.log_message.emit(m)
        session.on_overall_phase = lambda p: self.overall_phase.emit(p)

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
        self.session.stop()

    def run(self):
        try:
            self.session.run()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.log_message.emit(f"Worker error: {e}")
        finally:
            self.finished_signal.emit()


class ResourceRefreshWorker(QThread):
    """Enumerates VISA resources without blocking the GUI."""
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
