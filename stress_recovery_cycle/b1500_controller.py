"""Keysight B1500 controller (FLEX commands over VISA)."""

import time
import threading
from typing import List, Optional, Tuple

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False
    print("Warning: pyvisa not installed. Install with: pip install pyvisa pyvisa-py")


class B1500Controller:
    """Controller for Keysight B1500 Semiconductor Parameter Analyzer."""

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

    def list_all_resources(self) -> List[str]:
        if not PYVISA_AVAILABLE:
            return []
        rm = self._resource_manager()
        try:
            return sorted(rm.list_resources())
        except Exception:
            return []
        finally:
            try:
                rm.close()
            except Exception:
                pass

    def connect(self, resource: str, timeout_ms: int = 15000) -> Tuple[bool, str]:
        self.disconnect()
        try:
            self.rm = self._resource_manager()
            self.inst = self.rm.open_resource(resource)
            self.inst.timeout = timeout_ms
            self.inst.write_termination = "\n"
            self.inst.read_termination = "\n"
            with self.lock:
                self.idn = self.inst.query("*IDN?").strip()
                self.inst.write("FMT 1,0")
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

    def _safe_read(self) -> str:
        if not self.inst:
            return ""
        try:
            raw = self.inst.read_raw()
            for enc in ['ascii', 'latin-1', 'utf-8']:
                try:
                    return raw.decode(enc).strip()
                except UnicodeDecodeError:
                    continue
            return raw.decode('ascii', errors='ignore').strip()
        except Exception:
            return ""

    def configure_for_sweep(self, smu: int, mode: str, compliance: float) -> None:
        with self.lock:
            if not self.inst:
                raise RuntimeError("Not connected")
            try:
                for _ in range(5):
                    err = self.inst.query("ERR?")
                    if err.strip().startswith("0"):
                        break
            except Exception:
                pass
            time.sleep(0.1)
            self.inst.write("FMT 1,0")
            time.sleep(0.05)
            self.inst.write(f"CN {smu}")
            time.sleep(0.1)
            self.inst.write(f"AAD {smu},1")
            time.sleep(0.05)
            self.inst.write("AV 1,0")
            time.sleep(0.05)
            if mode == "iv":
                self.inst.write(f"RI {smu},0")
            else:
                self.inst.write(f"RV {smu},0")
            time.sleep(0.05)
            self.inst.write(f"MM 1,{smu}")

    def set_bias_and_measure(self, smu: int, set_value: float, mode: str,
                             compliance: float, dwell_s: float = 0.1) -> Tuple[float, float]:
        with self.lock:
            if not self.inst:
                raise RuntimeError("Not connected")
            try:
                if mode in ["iv", "voltage"]:
                    self.inst.write(f"DV {smu},0,{set_value},{compliance}")
                else:
                    self.inst.write(f"DI {smu},0,{set_value},{compliance}")
                if dwell_s > 0:
                    time.sleep(dwell_s)
                self.inst.write("XE")
                old_to = self.inst.timeout
                self.inst.timeout = 5000
                try:
                    resp = self._safe_read()
                finally:
                    self.inst.timeout = old_to
            except Exception:
                return (set_value, 0.0) if mode in ["iv", "voltage"] else (0.0, set_value)

        try:
            if not resp:
                return (set_value, 0.0) if mode in ["iv", "voltage"] else (0.0, set_value)
            parts = resp.replace(";", ",").split(",")
            values = []
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                try:
                    num_start = 0
                    for i, c in enumerate(p):
                        if c in '+-0123456789.':
                            num_start = i
                            break
                    num_str = p[num_start:]
                    if num_str:
                        values.append(float(num_str))
                except Exception:
                    pass
            if values:
                measured = values[0]
                return (set_value, measured) if mode in ["iv", "voltage"] else (measured, set_value)
        except Exception:
            pass
        return (set_value, 0.0) if mode in ["iv", "voltage"] else (0.0, set_value)

    def output_off(self, smu: int) -> None:
        if not self.inst:
            return
        with self.lock:
            try:
                self.inst.write(f"DV {smu},0,0,0.01")
                time.sleep(0.05)
                self.inst.write(f"CL {smu}")
            except Exception:
                pass
