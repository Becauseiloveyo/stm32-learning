"""Test-only process: exit abruptly at an explicit persistence boundary."""

import os
from pathlib import Path
import sys

import protocol


def main():
    root = Path(sys.argv[1])
    stage = sys.argv[2]

    def terminate(requested, point):
        if requested == point:
            os._exit(73)  # Intentionally bypass context managers and connection cleanup.

    protocol.fail_at = terminate
    device = protocol.Device(root / "device.sqlite3")
    service = protocol.Service(root / "service.sqlite3")
    receiver = protocol.Receiver(root / "receiver.sqlite3", "owner_a")
    if stage in {"after_counter", "before_device_commit"}:
        device.emit("RESEARCH_WET", 1010, fail=stage)
    elif stage in {"after_simulated_push", "after_receiver_commit"}:
        service.dispatch(receiver, 1010, fail=stage)
    else:
        service.submit(*device.envelope(), "fixture_token_a", 1010, fail=stage)
    raise RuntimeError("requested crash boundary was not reached")


if __name__ == "__main__":
    main()
