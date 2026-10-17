"""Install only inside inference child processes, never in the API host."""
import sys

from .errors import InferenceError


def install_network_guard():
    def audit(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            raise InferenceError("Network access is prohibited during local inference")
    sys.addaudithook(audit)
