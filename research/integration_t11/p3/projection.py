"""Read-only, causal projection of synthetic P2 runs for the offline UI."""

import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from research.integration_t11.p2.replay import CASES, scenario
from research.integration_t11.p2.runner import Rig

EXTRA = (
    "forecast_boundary",
    "forecast_wet",
    "contact_missing",
    "queue_full",
    "model_mismatch",
    "fault_revoke",
    "probe_obstructed",
)
TITLES = dict(
    forecast_boundary="远端预报生效与过期",
    forecast_wet="无雨预报与本地受水",
    contact_missing="中央接触丢失",
    queue_full="离线队列满",
    model_mismatch="覆盖或模型失配",
    fault_revoke="探头故障撤销",
    probe_obstructed="雨头被遮挡",
)


def describe_conditions(result):
    if not result:
        return None
    descriptions = []
    for condition in result["future_conditions"]:
        segments = []
        for segment in condition["segments"]:
            drive = segment["drive"]
            segments.append(
                f"气温 {drive['air_c']} °C，相对湿度 {drive['rh'] * 100:g}%，"
                f"风速 {drive['wind_m_s']} m/s，日照 {drive['solar_w_m2']} W/m²，"
                f"降雨 {drive['rain_kg_s']} kg/s，至 {segment['end_s']} 秒"
            )
        descriptions.append(f"情景 {condition['id']}：" + "；".join(segments))
    return descriptions


def view(s, t):
    if s is None:
        return None
    obs = s["observation"]

    def state(e, default="unknown"):
        return e["state"] if e and t < e["expires_s"] else default

    contact = state(obs["contact"])
    heat = state(obs["charging_heat"])
    wet_e = s["local_wetting"]["evidence"]
    wet = state(wet_e, "unavailable")
    if wet_e and (wet_e["exposure"] != "exposed" or wet == "unknown"):
        wet = "unavailable"
    forecast = s["forecast"]["evidence"]
    if not forecast or t >= min(forecast["expires_s"], forecast["valid_until_s"]):
        f = "unavailable"
    elif t < forecast["valid_from_s"]:
        f = "upcoming_" + forecast["risk"]
    else:
        f = forecast["risk"]
    p = s["prediction"]
    value = p["value"]
    eligible = (
        contact == "confirmed" and heat == "clear" and wet == "dry" and not obs["fault"]
    )
    valid = value is not None and t < value["expires_s"] and eligible
    reason = p["invalidation_reason"]
    if value and not valid:
        reason = "prediction_expired" if t >= value["expires_s"] else "evidence_expired"
    result = value["result"] if valid else None
    return dict(
        boot=s["domain"]["boot"],
        batch=s["domain"]["batch"],
        installation=s["domain"]["installation"],
        contact=contact,
        heat=heat,
        wet=wet,
        forecast=f,
        prediction=p["state"] if valid else "unavailable",
        reason=reason,
        analysis_s=value["analysis_s"] if valid else None,
        remaining_s=(
            result["upper_remaining_s"]
            if result and p["state"] == "conditional"
            else None
        ),
        expires_s=value["expires_s"] if valid else None,
        conditions=describe_conditions(result),
    )


def run_case(name):
    base = (
        name
        if name in CASES
        else ("wet_connected" if name == "forecast_wet" else "normal_cycle")
    )
    initial, inputs = scenario(base)
    if name in ("forecast_boundary", "forecast_wet"):
        if name == "forecast_boundary":
            inputs[27] = []  # keep the radio available for the forecast
            inputs[38].append(("link", False))
        risk = "no_risk" if name == "forecast_wet" else "risk"
        inputs[30].append(
            (
                "forecast",
                dict(
                    issued_s=30,
                    available_s=30,
                    valid_from_s=60,
                    valid_until_s=90,
                    expires_s=100,
                    risk=risk,
                    source="synthetic_forecast",
                ),
            )
        )
    if name == "model_mismatch":
        config = (
            Path(__file__).resolve().parents[3]
            / "research/garment_prediction/prediction_m6/config.json"
        )
        request = json.loads(config.read_text(encoding="utf-8"))["request"]
        request["target_x"] = 5
        request["candidates"] = request["candidates"][2:]
        for t in list(inputs):
            inputs[t] = [
                event for event in inputs[t] if event[0] not in ("observe", "predict")
            ]
        for packet in request["observations"]:
            inputs[packet["now_s"]].append(("observe", (0, packet)))
        inputs[20].append(("predict", dict(origin_s=0, expires_s=100, request=request)))
    if name == "fault_revoke":
        inputs[35].append(("fault", dict(state="active")))
        inputs[36].append(("tick", {}))
    if name == "probe_obstructed":
        inputs[35].append(("local", dict(state="dry", exposure="obstructed",
                                         source="synthetic_external", expires_s=120)))
        inputs[36].append(("tick", {}))
    if name == "contact_missing":
        inputs[35].append(
            (
                "contact",
                dict(state="missing", source="synthetic_external", expires_s=120),
            )
        )
    if name == "queue_full":
        inputs[1].append(("link", False))
        for t in range(31, 37):
            inputs[t].append(
                (
                    "heat",
                    dict(state="clear", source="synthetic_external", expires_s=t + 120),
                )
            )
    with TemporaryDirectory(prefix="hanger-p3-") as tmp:
        rig = Rig(
            Path(tmp), initial_j=initial, queue_size=2 if name == "queue_full" else 32
        )
        frames = []
        saved_s = received_s = None
        prev = None
        try:
            for t in range(190):
                start = len(rig.trace)
                rig.step(t, inputs.get(t, ()))
                trace = rig.trace[start:]
                actions = [x["action"] for x in trace]
                if "server_saved" in actions:
                    saved_s = t
                if "simulated_receiver" in actions:
                    received_s = t
                env = rig.device.envelope()
                local = env["head"] if env else None
                service = rig.service._head()
                remote = rig.receiver._head()
                queue = rig.device.store.db.execute(
                    "SELECT count(*) FROM records"
                ).fetchone()[0]
                frame = dict(
                    t=t,
                    local=view(local["snapshot"], t) if local else None,
                    remote=view(remote["snapshot"], t) if remote else None,
                    saved_s=saved_s,
                    received_s=received_s,
                    link=rig.online,
                    queue=queue,
                    drops=rig.device.dropped_history_count,
                    energy_j=rig.battery.stored_j,
                    capacity_j=rig.battery.capacity_j,
                    actions=actions,
                    newly_notified=sum(
                        len(x.get("newly_notified", []))
                        for x in trace
                        if x["action"] == "simulated_receiver"
                    ),
                    local_seq=local["revision"] if local else None,
                    saved_seq=service["revision"] if service else None,
                    remote_seq=remote["revision"] if remote else None,
                    local_intents=(
                        [
                            i["kind"]
                            for i in local["snapshot"]["intents"]
                            if t < i["expires_s"]
                        ]
                        if local
                        else []
                    ),
                    saved_intents=(
                        [
                            i["kind"]
                            for i in service["snapshot"]["intents"]
                            if t < i["expires_s"]
                        ]
                        if service
                        else []
                    ),
                    remote_intents=(
                        [
                            i["kind"]
                            for i in remote["snapshot"]["intents"]
                            if t < i["expires_s"]
                        ]
                        if remote
                        else []
                    ),
                )
                # Energy changes continuously; only semantic changes, actual trace, inputs and endpoints select frames.
                signature = {
                    k: v
                    for k, v in frame.items()
                    if k not in ("t", "energy_j", "actions")
                }
                if t in (0, 189) or actions or inputs.get(t) or signature != prev:
                    frames.append(frame)
                prev = deepcopy(signature)
        finally:
            rig.close()
    return dict(id=name, title=TITLES.get(name, name), frames=frames)


def build():
    return dict(
        label="SYNTHETIC_UNCALIBRATED",
        time_unit="s",
        energy_unit="J",
        seed=None,
        collection_authorized=False,
        phone_delivery="unknown",
        cases=[run_case(n) for n in (*CASES, *EXTRA)],
    )
