"""Synthetic IPC cancellation tests, not AI quality tests."""
import multiprocessing

import pytest

from backend.analyzer.ipc import send_result
from backend.api.worker import _analyze_child


class ClosedChannel:
    calls = 0
    closed = False

    def send(self, message):
        self.calls += 1
        raise BrokenPipeError(109, "closed")

    def close(self):
        self.closed = True


def test_child_does_not_retry_closed_error_channel():
    channel = ClosedChannel()
    _analyze_child("missing_module_for_ipc_test:analyze", "unused", channel)
    assert channel.calls == 1
    assert channel.closed


def _send_to_closed_parent(connection):
    try:
        assert send_result(connection, (False, "AnalysisTimeoutError", "deadline")) is False
    finally:
        connection.close()


def test_spawned_child_exits_cleanly_after_parent_closes_pipe():
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    receive.close()
    child = context.Process(target=_send_to_closed_parent, args=(send,))
    child.start()
    send.close()
    try:
        child.join(timeout=10)
        assert child.exitcode == 0
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=2)
        child.close()


def test_unrelated_ipc_failure_is_not_suppressed():
    class InvalidChannel:
        def send(self, message):
            raise OSError("unexpected transport failure")

    with pytest.raises(OSError, match="unexpected"):
        send_result(InvalidChannel(), (True, []))
