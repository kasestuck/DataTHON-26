import multiprocessing
import sys

import pytest

from backend.analyzer import performance


def test_non_windows_is_noop(monkeypatch):
    monkeypatch.setattr(performance.sys, "platform", "linux")
    assert performance.request_inference_performance() is False


def test_unavailable_api_keeps_existing_behavior(monkeypatch):
    monkeypatch.setattr(performance.sys, "platform", "win32")

    def unavailable(*args, **kwargs):
        raise OSError("synthetic unsupported API")

    monkeypatch.setattr(performance.ctypes, "WinDLL", unavailable, raising=False)
    assert performance.request_inference_performance() is False


def _check_worker_qos(connection):
    import ctypes
    from ctypes import wintypes

    assert performance.request_inference_performance()
    state = (wintypes.ULONG * 3)(1, 0, 0)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD]
    assert kernel.GetProcessInformation(kernel.GetCurrentProcess(), 4,
                                        ctypes.byref(state), ctypes.sizeof(state))
    connection.send(tuple(state))
    connection.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-local QoS")
def test_qos_applied_in_spawned_worker():
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_check_worker_qos, args=(child,))
    process.start()
    child.close()
    try:
        assert parent.poll(10)
        version, control, state = parent.recv()
        assert version == 1 and control & 1 and not state & 1
        process.join(5)
        assert process.exitcode == 0
    finally:
        parent.close()
        if process.is_alive():
            process.kill()
            process.join(2)
        process.close()
