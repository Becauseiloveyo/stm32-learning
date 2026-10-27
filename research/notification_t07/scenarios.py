"""Repeatable continuous offline trace with saved raw reference-replay inputs."""
from dataclasses import replace
from pathlib import Path

from research.state_machine.machine import DEFAULT_CONFIG, Configuration, StateMachine
from research.notification_t07.contract import Event, SYNTHETIC
from research.notification_t07.device import Device
from research.notification_t07.fixtures import RawFixture, WALL_ORIGIN_S
from research.notification_t07.service import Service, Receiver
from research.notification_t07.storage import InjectedCrash


def check_replays(trace):
    assessed = 0
    raw_count = 0
    for stream in trace["raw_streams"]:
        machine = StateMachine(Configuration.from_snapshot(stream["initial_config"]))
        machine.handle(stream["boot_event"])
        actual = [machine.handle(event) for event in stream["raw_events"]]
        if actual != stream["expected_outputs"]:
            raise ValueError("saved raw input does not replay through accepted T04")
        if any(item["collection_authorized"] is not False for item in actual):
            raise ValueError("unexpected collection authorization")
        assessed += sum(item["assessment"] is not None for item in actual)
        raw_count += len(actual)
    if assessed < 3 or trace["cumulative_simulated_alerts"] < 1:
        raise ValueError("vacuous notification trace")
    return dict(raw_events=raw_count, assessed_cycles=assessed, streams=len(trace["raw_streams"]))


def run_trace(output):
    output = Path(output)
    device = Device.provision(output / "device.sqlite3", "demo", "demo_p", wall_s=WALL_ORIGIN_S)
    service = Service(output / "service.sqlite3", create=True)
    receiver = Receiver(output / "receiver.sqlite3", "owner_a", create=True)
    old_receiver = None
    raw = RawFixture(device)
    streams, steps = [], []
    token = "token_a"

    def upload(now, fail=None):
        ack = service.submit(*device.envelope(), token, now, fail=fail)
        device.acknowledge(ack)
        return ack

    def checkpoint(name, now, meaning):
        snapshots = service.inspect()
        cumulative = len(receiver.inspect()["notices"])
        if old_receiver is not None:
            cumulative += len(old_receiver.inspect()["notices"])
        steps.append(dict(name=name, now_s=now, meaning=meaning, device_head=device.head.payload(),
                          outbox_pending=len(device.envelope()[1]), jobs=snapshots["jobs"],
                          simulated_push_attempts=len(snapshots["push_attempts"]), cumulative_simulated_alerts=cumulative))

    try:
        service.bind("demo", "demo_p", "owner_a", token, DEFAULT_CONFIG, WALL_ORIGIN_S)
        first = raw.review()
        try:
            upload(raw.wall, fail="after_service_commit")
        except InjectedCrash:
            pass
        checkpoint("saved_ack_lost", raw.wall, "Actual raw weak cycles reached research review; service committed while device still has pending records.")
        ack = upload(raw.wall)
        device.acknowledge(ack)
        service.dispatch(receiver, raw.wall, reachable=False)
        checkpoint("phone_unreachable", raw.wall, "Simulated push accepted; receiver and read confirmations are absent.")
        service.close()
        service = Service(output / "service.sqlite3")
        receiver.close()
        receiver = Receiver(output / "receiver.sqlite3", "owner_a")
        service.dispatch(receiver, raw.wall + 60)
        checkpoint("service_receiver_reopened", raw.wall + 60, "Persisted retry succeeds; one research notice exists.")
        raw.emit("tick", raw.now + 61)
        upload(raw.wall)
        service.dispatch(receiver, raw.wall)
        receiver.read(device.head.notice_key, service, raw.wall)
        checkpoint("tick_duplicate_and_read", raw.wall, "Tick retains original evidence time and silently refreshes the same episode; read is explicit simulation.")
        raw.emit("clip")
        upload(raw.wall)
        receiver.sync(service, raw.wall)
        if receiver.receive(first, service, raw.wall) != "superseded":
            raise ValueError("late data hint was not rejected")
        checkpoint("clip_and_late_push", raw.wall, "Old saved card invalidates and late hint is rejected.")
        raw.review()
        raw.install("batch-b")
        upload(raw.wall)
        service.dispatch(receiver, raw.wall)
        checkpoint("offline_batch_change", raw.wall, "New batch snapshot fences accumulated research-review event before push.")
        raw.review()
        upload(raw.wall)
        now = raw.wall + 1
        streams.append(raw.record())
        device.close()
        device = Device(output / "device.sqlite3", wall_s=now)
        raw = RawFixture(device, wall_origin_s=now)
        upload(now)
        service.dispatch(receiver, now)
        checkpoint("device_restart", now, "New persistent boot and empty evidence cancel pre-restart pending review.")
        raw.review()
        upload(raw.wall)
        changed = replace(DEFAULT_CONFIG, policy=replace(DEFAULT_CONFIG.policy, stabilization_wait_s=31))
        service.require_config("demo", changed, raw.wall)
        checkpoint("required_not_applied", raw.wall, "Required config changed but device still reports old actually applied config.")
        raw.emit("configure", new_config=changed.snapshot())
        raw.review()
        upload(raw.wall)
        service.dispatch(receiver, raw.wall)
        checkpoint("new_config_new_evidence", raw.wall, "Actual configure, reinstallation and fresh raw cycles create a new research-domain notice.")
        now = raw.wall + 1
        epoch = service.bind("demo", "demo_p", "owner_b", "token_b", changed, now)
        streams.append(raw.record())
        device.rebind(epoch, wall_s=now)
        receiver.sync(service, now)
        old_receiver = receiver
        receiver = Receiver(output / "receiver-b.sqlite3", "owner_b", create=True)
        raw = RawFixture(device, wall_origin_s=now)
        token = "token_b"
        upload(now)
        raw.review()
        upload(raw.wall)
        service.dispatch(receiver, raw.wall)
        checkpoint("rebind_and_fresh_review", raw.wall, "New owner only receives a newly stabilized and measured research episode.")
        raw.cycle("strong")
        upload(raw.wall)
        receiver.sync(service, raw.wall)
        expired = raw.review()
        upload(raw.wall)
        service.dispatch(receiver, raw.wall, reachable=False)
        service.dispatch(receiver, expired.expires_at_s)
        checkpoint("unreachable_until_expiry", expired.expires_at_s, "Exact evidence-anchored expiry cancels pending review without an extra alert.")
        streams.append(raw.record())
        trace = dict(synthetic=SYNTHETIC, collection_authorized=False, raw_streams=streams, steps=steps,
                     cumulative_simulated_alerts=steps[-1]["cumulative_simulated_alerts"],
                     final_stores=dict(device=device.inspect(), service=service.inspect(), receiver_a=old_receiver.inspect(), receiver_b=receiver.inspect()))
        trace["reference_replay"] = check_replays(trace)
        trace["sqlite_integrity"] = {name: store.db.execute("PRAGMA integrity_check").fetchone()[0]
                                     for name, store in (("device", device), ("service", service), ("receiver_a", old_receiver), ("receiver_b", receiver))}
        if any(value != "ok" for value in trace["sqlite_integrity"].values()):
            raise ValueError("store integrity check failed")
        return trace
    finally:
        device.close()
        service.close()
        receiver.close()
        if old_receiver is not None:
            old_receiver.close()
