"""Thorlabs PM100D/PM400 power meter controller (SCPI over VISA)."""

import time
import threading
from typing import List, Optional, Tuple

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False
    print("Warning: pyvisa not installed. Install with: pip install pyvisa pyvisa-py")


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
