"""Build deterministic host traces. Run from the project root with python -m."""

import argparse
import hashlib
import json
import tempfile
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from .runner import RADIO, Rig

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'research/integration_t11/p1/results/events.json'
RESULT = ROOT / 'research/integration_t11/p2/results/replay.json'
MANIFEST = RESULT.with_name('manifest.json')
BASELINE = ROOT / 'docs/team/baseline-t11-p2/snapshot.json'
CASES = (
    'wet_connected',
    'wet_reconnect',
    'change_offline',
    'boot_offline',
    'ack_lost',
    'manual_charge',
    'no_charge',
    'missing_packet',
    'normal_cycle',
)


def scenario(name):
    events = defaultdict(list)

    def add(t, kind, payload):
        events[t].append((kind, payload))

    source = json.loads(SOURCE.read_text(encoding='utf-8'))['events']
    for item in source[:3]:
        e = item['event']
        add(0, e['kind'], e['payload'])
    prediction = deepcopy(source[4]['event']['payload'])
    for packet in prediction['request']['observations']:
        if name != 'missing_packet' or packet['now_s'] != 10:
            add(packet['now_s'], 'observe', (0, packet))
    add(20, 'predict', prediction)
    add(27, 'measure', None)
    if name in ('wet_connected', 'wet_reconnect', 'change_offline', 'boot_offline', 'ack_lost'):
        add(
            62,
            'local',
            dict(state='wet', source='synthetic_external', expires_s=182, exposure='exposed'),
        )
        channel = 'ack_path' if name == 'ack_lost' else 'link'
        if name != 'wet_connected':
            add(62, channel, False)
            add(88, channel, True)
        add(182, 'receiver_query', None)
        if name in ('change_offline', 'boot_offline'):
            if name == 'boot_offline':
                add(73, 'cold_boot', 'boot2')
            else:
                add(73, 'identity', dict(boot='boot', batch='new', installation='new_hook'))
            add(74, 'receiver_query', None)
            add(101, 'receiver_query', None)
    if name == 'normal_cycle':
        for item in source[:3]:
            e = item['event']
            add(95, e['kind'], dict(e['payload'], expires_s=215))
    if name == 'manual_charge':
        for t, watts in [(4, 1), (14, 0), (22, 1), (47, 0)]:
            add(t, 'charge', (watts, 0.75))
        add(50, 'heat', dict(state='clear', source='operator', expires_s=170))
    if name in ('manual_charge', 'no_charge'):
        # Reserve 5% of (0.25 Ah * 3.7 V * 0.8 * 3600 J/Wh), plus 1 J.
        initial = 134.2
    else:
        initial = None
    return initial, events


def run_case(name):
    if name not in CASES:
        raise ValueError('unknown scenario')
    initial, inputs = scenario(name)
    with tempfile.TemporaryDirectory(prefix='hanger-p2-case-') as tmp:
        rig = Rig(Path(tmp), initial_j=initial)
        try:
            for t in range(190):
                rig.step(t, inputs.get(t, ()))
            wet_case = any(
                kind == 'local' and p['state'] == 'wet'
                for items in inputs.values()
                for kind, p in items
            )

            # Scenario truth used only after running the consumer, to score staged latency.
            def first(action, condition):
                return next(
                    (r['t'] for r in rig.trace if r['action'] == action and condition(r)), None
                )

            latency = None
            if wet_case:
                observed = first('local_sample', lambda r: r['state'] == 'wet')
                latency = dict(
                    synthetic_water_onset_s=61,
                    local_observed_s=observed,
                    first_queued_s=first('state_saved', lambda r: 'local_wet' in r['intents']),
                    first_radio_start_s=first(
                        'radio_start', lambda r: observed is not None and r['t'] >= observed
                    ),
                    first_server_saved_s=first(
                        'server_saved', lambda r: 'local_wet' in r['intents']
                    ),
                    first_simulated_receiver_s=first(
                        'simulated_receiver',
                        lambda r: any(i['kind'] == 'local_wet' for i in r['result']),
                    ),
                    phone_received_s=None,
                )
            return dict(
                case=name,
                trace=rig.trace,
                energy=rig.battery.ledger(),
                wet_latency=latency,
                prediction=rig.controller.snapshot()['prediction']['state'],
                service_attempt_limit=RADIO.wifi_attempt_limit,
                collection_authorized=False,
                phone_delivery_s=None,
            )
        finally:
            rig.close()


def build():
    return dict(
        version='t11-p2-v1',
        label='SYNTHETIC_UNCALIBRATED',
        seed=None,
        time_unit='s',
        energy_unit='J',
        horizon_s=189,
        cases=[run_case(name) for name in CASES],
    )


def render(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def build_manifest(replay_bytes):
    sources = [
        BASELINE,
        *(
            Path(__file__).with_name(name)
            for name in ('power.py', 'transport.py', 'runner.py', 'replay.py')
        ),
    ]
    return dict(
        version='t11-p2-v1',
        label='SYNTHETIC_UNCALIBRATED',
        seed=None,
        time_grid_s=1,
        horizon_s=189,
        cases=CASES,
        frozen_inputs=json.loads(BASELINE.read_bytes()),
        source_sha256={
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sources
        },
        replay_sha256=hashlib.sha256(replay_bytes).hexdigest(),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    result = build()
    replay_bytes = render(result)
    outputs = [(RESULT, replay_bytes), (MANIFEST, render(build_manifest(replay_bytes)))]
    for path, expected in outputs:
        if args.write:
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(expected)
        elif path.read_bytes() != expected:
            raise SystemExit(f'{path.name} differs from current replay or source fingerprints')
    print(
        f'{len(result["cases"])} deterministic scenarios {"written" if args.write else "verified"}'
    )


if __name__ == '__main__':
    main()
