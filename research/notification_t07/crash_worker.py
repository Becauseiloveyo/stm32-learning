"""Test-only abrupt exit at durable boundaries, never a device/phone test."""
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from research.notification_t07 import device as device_module, service as service_module
from research.notification_t07.contract import Event
from research.notification_t07.device import Device
from research.notification_t07.service import Receiver, Service


def main():
    root, stage = Path(sys.argv[1]), sys.argv[2]
    request = json.loads((root / "crash-request.json").read_text(encoding="utf-8"))

    def terminate(requested, point):
        if requested == point:
            os._exit(73)

    device_module.fail_at = service_module.fail_at = terminate
    if stage in {"after_device_counter", "before_device_commit", "after_device_commit"}:
        Device(root / "device.sqlite3", wall_s=request["now_s"], fail=stage)
    else:
        service = Service(root / "service.sqlite3")
        if stage in {"after_simulated_push", "after_receiver_commit"}:
            receiver = Receiver(root / "receiver.sqlite3", "owner_a")
            service.dispatch(receiver, request["now_s"], fail=stage)
        else:
            service.submit(Event.decode(request["snapshot"]), [Event.decode(p) for p in request["pending"]],
                           "token_a", request["now_s"], fail=stage)
    raise RuntimeError("crash point was not reached")


if __name__ == "__main__":
    main()
