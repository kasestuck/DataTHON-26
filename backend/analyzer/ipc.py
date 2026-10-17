"""One-shot delivery to a supervisor that may already have stopped waiting."""
import logging


def send_result(connection, message):
    """Do not retry an error over the same closed pipe during cancellation."""
    try:
        connection.send(message)
    except (BrokenPipeError, EOFError):
        logging.getLogger(__name__).warning("Supervisor IPC channel closed; result not delivered")
        return False
    except OSError as exc:
        if getattr(exc, "winerror", None) not in {109, 232, 233}:
            raise
        logging.getLogger(__name__).warning("Supervisor IPC channel closed; result not delivered")
        return False
    return True
