"""Verify T11 P2 or check the saved, source-bound offline verification receipt."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
P2 = 'research/integration_t11/p2/'
RECEIPT = ROOT / 'docs/verification/T11-P2-tests.json'
FROZEN = 'docs/team/baseline-t11-p2/snapshot.json'
P1_RECEIPT = 'docs/verification/T11-P1-tests.json'
SUITES = ('test_power', 'test_transport', 'test_integration')
GROUPS = ['frozen_inputs', 'replay', *SUITES]


def read(path):
    return json.loads((ROOT / path).read_bytes())


def digest(path):
    file = (ROOT / path).resolve()
    if not file.is_relative_to(ROOT):
        raise ValueError('path outside checkout')
    return hashlib.sha256(file.read_bytes()).hexdigest()


def run(*args):
    p = subprocess.run(
        [sys.executable, '-B', '-X', 'utf8', *args],
        cwd=ROOT,
        timeout=180,
        capture_output=True,
        encoding='utf-8',
    )
    if p.returncode:
        raise ValueError(p.stderr + p.stdout)
    return p.stdout + p.stderr


def inventory():
    frozen = read(FROZEN)
    prior = read(P1_RECEIPT)['reviewed_sha256']
    for source in (frozen, prior):
        for name, expected in source.items():
            if digest(name) != expected:
                raise ValueError(f'frozen input changed: {name}')
    paths = (
        set(prior)
        | set(frozen)
        | {
            FROZEN,
            P1_RECEIPT,
            'docs/T11-P2-TRANSPORT.md',
            'docs/T11-P3-HANDOFF.md',
            'docs/team/reports/power-t11-p2.md',
            'docs/team/reports/transport-t11-p2.md',
            'docs/team/assignments/integration-t11-p2.md',
            'tools/check_integration_power_transport.py',
            P2 + 'results/replay.json',
            P2 + 'results/manifest.json',
        }
    )
    paths.update(P2 + p for p in ('power.py', 'transport.py', 'runner.py', 'replay.py'))
    paths.update(P2 + name + '.py' for name in SUITES)
    return {p: digest(p) for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-receipt', action='store_true')
    mode.add_argument('--write-receipt', action='store_true')
    args = parser.parse_args()
    sourced = inventory()
    run('tools/check_integration_events.py', '--check-receipt')
    if args.check_receipt:
        r = read(RECEIPT)
        if (
            r.get('milestone') != 'P2'
            or r.get('passed') is not True
            or r.get('collection_authorized') is not False
            or r.get('reviewed_sha256') != sourced
            or [c['name'] for c in r['checks'] if c['passed']] != GROUPS
        ):
            raise ValueError('T11 P2 receipt is stale or incomplete')
        print(f'T11 P2 receipt current: {len(sourced)} files')
        return
    checks = [
        dict(
            name='frozen_inputs',
            passed=True,
            baseline=len(read(FROZEN)),
            accepted_p1_sources=len(read(P1_RECEIPT)['reviewed_sha256']),
        )
    ]
    jobs = [('replay', ['-m', 'research.integration_t11.p2.replay'])]
    jobs.extend(
        (name, ['-m', 'unittest', f'research.integration_t11.p2.{name}']) for name in SUITES
    )
    for name, command in jobs:
        try:
            output = run(*command)
            if name == 'replay':
                detail = dict(
                    cases=len(read(P2 + 'results/replay.json')['cases']), byte_identical=True
                )
            else:
                count = re.search(r'Ran ([1-9][0-9]*) tests? in', output)
                if count is None:
                    raise ValueError('no executed tests')
                detail = dict(tests=int(count[1]))
            checks.append(dict(name=name, passed=True, **detail))
        except (ValueError, subprocess.SubprocessError) as exc:
            checks.append(dict(name=name, passed=False, error=str(exc)))
    if inventory() != sourced:
        raise ValueError('inputs changed during verification')
    record = dict(
        schema_version=1,
        task='T11',
        milestone='P2',
        verified_at=datetime.now(timezone.utc).isoformat(),
        python=sys.version,
        passed=all(c['passed'] for c in checks),
        checks=checks,
        label='SYNTHETIC_UNCALIBRATED',
        collection_authorized=False,
        reviewed_sha256=sourced,
    )
    if args.write_receipt:
        RECEIPT.write_bytes((json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode())
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    if not record['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
