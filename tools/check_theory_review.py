"""Check the T08 requirements map and the local evidence behind its key examples."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'docs/PROJECT.md'
REVIEW = 'docs/THEORY-REVIEW.md'
PLAN = 'first-validation-plan.md'
RECEIPT = ROOT / 'docs/verification/T08-review.json'


def digest(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def read(path):
    return json.loads((ROOT / path).read_bytes())


def require(ok, message):
    if not ok:
        raise ValueError(message)


def inspect():
    subprocess.run(
        [sys.executable, '-B', '-X', 'utf8', 'tools/check_hanger_panel.py', '--check-receipt'],
        cwd=ROOT,
        check=True,
    )
    requirements = re.findall(r'^\| (R\d{2}) \|', (ROOT / PROJECT).read_text(encoding='utf8'), re.M)
    text = (ROOT / REVIEW).read_text(encoding='utf8')
    rows = [line for line in text.splitlines() if re.match(r'^\| R\d{2} ', line)]
    require(requirements == [f'R{i:02d}' for i in range(1, 22)], 'requirements baseline changed')
    require([r.split()[1] for r in rows] == requirements, 'missing or duplicated requirement')
    for row in rows:
        cells = row.strip('|').split('|')
        require(len(cells) == 3 and '](' in cells[1] and cells[2].strip(), row)

    inputs = {
        PROJECT,
        REVIEW,
        PLAN,
        'tools/check_theory_review.py',
        'docs/verification/T11-P3-tests.json',
    }
    for document in (PLAN, REVIEW):
        for link in re.findall(r'\[[^\]]*\]\(([^)\s]+)\)', (ROOT / document).read_text(encoding='utf8')):
            if '://' in link or link.startswith('#'):
                continue
            target = ((ROOT / document).parent / link.split('#')[0]).resolve()
            require(target.is_relative_to(ROOT) and target.is_file(), f'broken link {link}')
            # Status and team membership change on later handoffs.
            path = target.relative_to(ROOT).as_posix()
            if path not in ('docs/STATUS.json', 'docs/TEAM.md', 'docs/ROADMAP.md'):
                inputs.add(path)

    m6 = 'docs/verification/T10-M6-tests.json'
    pairs = 'research/probe/results-t02/pair-comparison.json'
    weather = 'research/design_extension/data/weather-source.json'
    raw = read(weather)['path']
    replay = 'research/integration_t11/p2/results/replay.json'
    inputs.update((m6, pairs, weather, raw, replay))
    source = read(weather)
    require(
        digest(raw) == source['sha256']
        and len(source['counts']) == 5
        and set(source['counts'].values()) == {8784},
        'Wuhan weather input mismatch',
    )
    measure = next(
        c['detail']['unobserved_water_difference_kg']
        for c in read(m6)['checks']
        if c['name'] == 'unobserved_zone_and_wrong_prior' and c['passed']
    )
    require(round(1000 * measure) == 60, 'unobserved-zone example mismatch')
    wall = [
        p['max_observed_gap_Pa'] for p in read(pairs) if p['other'] == 'equilibrium_with_matched_wall'
    ]
    require(len(wall) == 2 and all(0 <= gap < 2e-12 for gap in wall), 'wall-source example mismatch')
    delays = {}
    for case in read(replay)['cases']:
        if case['case'] in ('wet_connected', 'wet_reconnect'):
            w = case['wet_latency']
            require(
                w['phone_received_s'] is None and case['collection_authorized'] is False,
                'simulated delivery or dry authorization changed',
            )
            delays[case['case']] = w['first_simulated_receiver_s'] - w['local_observed_s']
    require(delays == {'wet_connected': 6, 'wet_reconnect': 86}, 'offline delay mismatch')
    return dict(
        task='T08',
        scope='document review and existing offline evidence only',
        requirements=requirements,
        simulated_delay_s=delays,
        unobserved_difference_kg=measure,
        matched_wall_max_gaps_pa=wall,
        real_garment_batches=0,
        collection_authorized=False,
        reviewed_sha256={p: digest(p) for p in sorted(inputs)},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-receipt', action='store_true')
    args = parser.parse_args()
    current = inspect()
    if args.write_receipt:
        RECEIPT.write_bytes((json.dumps(current, ensure_ascii=False, indent=2) + '\n').encode())
    elif current != read(RECEIPT):
        raise ValueError('T08 review receipt differs from the current evidence')
    print(
        f"T08 review checked: {len(current['requirements'])} requirements, "
        f"{len(current['reviewed_sha256'])} local sources"
    )


if __name__ == '__main__':
    main()
