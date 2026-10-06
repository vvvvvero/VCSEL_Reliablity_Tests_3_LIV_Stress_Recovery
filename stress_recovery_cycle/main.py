"""Main entry point for the stress-recovery cycling GUI."""

import sys

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from .gui import StressRecoveryCycleGUI


def _suppress_windows_error_dialogs():
    # Keep VISA/driver faults from opening blocking Windows error dialogs.
    try:
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
    except (AttributeError, OSError, TypeError):
        pass


def main():
    _suppress_windows_error_dialogs()
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
