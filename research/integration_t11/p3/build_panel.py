"""Build or verify the self-contained UTF-8 replay page with its actual host data."""
import argparse
import hashlib
import json
from pathlib import Path

from .projection import build

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAGE = ROOT / 'docs/demo/hanger.html'
MANIFEST = HERE / 'manifest.json'
FROZEN = ROOT / 'docs/team/baseline-t11-p3/snapshot.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def contents():
    dataset = build()
    js_data = json.dumps(dataset, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    # A literal closing script tag must never end the JSON data island.
    js_data = js_data.replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026')
    html = (HERE/'panel.html').read_text(encoding='utf-8')
    for token, value in [('@@STYLE@@', (HERE/'panel.css').read_text(encoding='utf-8')),
                         ('@@SCRIPT@@', (HERE/'panel.js').read_text(encoding='utf-8')),
                         ('@@DATA@@', js_data)]:
        if html.count(token) != 1:
            raise ValueError(f'expected one {token} token')
        html = html.replace(token, value)
    page = html.encode()
    sources = [HERE/name for name in
               ('panel.html', 'panel.css', 'panel.js', 'projection.py', 'build_panel.py')]
    manifest = dict(version='t11-p3-v1', label='SYNTHETIC_UNCALIBRATED', seed=None,
                    page=PAGE.relative_to(ROOT).as_posix(), page_sha256=sha(page),
                    collection_authorized=False, real_phone_delivery=None,
                    frames={c['id']:len(c['frames']) for c in dataset['cases']},
                    frozen_sha256=json.loads(FROZEN.read_bytes()),
                    sources={p.relative_to(ROOT).as_posix():sha(p.read_bytes()) for p in sources})
    return page, (json.dumps(manifest, ensure_ascii=False, indent=2)+'\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    write = parser.parse_args().write
    for p, expected in zip((PAGE, MANIFEST), contents()):
        if write:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(expected)
        elif expected != p.read_bytes():
            raise SystemExit(f'Stale {p.name}')
    print('P3 offline HTML and manifest ' + ('written' if write else 'reproduced'))


if __name__ == '__main__':
    main()
