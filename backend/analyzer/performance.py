"""Process-local Windows QoS for deadline-bound inference, never a global plan."""
import ctypes
import logging
import sys


def request_inference_performance():
    """Opt this spawned worker out of automatic execution-speed throttling.

    Windows can classify descendants of hidden windows as low QoS. This request
    does not change priority, affinity, thermal limits or the system power plan.
    Unsupported platforms retain their existing behavior.
    """
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes

        class PowerThrottling(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                        ("StateMask", wintypes.ULONG)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.DWORD]
        kernel.SetProcessInformation.restype = wintypes.BOOL
        # ProcessPowerThrottling=4, version=1, EXECUTION_SPEED=1; StateMask=0
        # explicitly disables execution-speed throttling (Microsoft HighQoS).
        state = PowerThrottling(1, 1, 0)
        if not kernel.SetProcessInformation(kernel.GetCurrentProcess(), 4,
                                            ctypes.byref(state), ctypes.sizeof(state)):
            raise ctypes.WinError(ctypes.get_last_error())
        return True
    except (OSError, AttributeError):
        logging.getLogger(__name__).warning("Process-local inference QoS request unavailable")
        return False
