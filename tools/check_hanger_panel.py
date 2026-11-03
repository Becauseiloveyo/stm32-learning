"""Rebuild T11's offline UI and exercise its controls in a real browser."""

import argparse
from datetime import datetime, timezone
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import uuid

ROOT = Path(__file__).resolve().parents[1]
P3 = 'research/integration_t11/p3/'
PAGE = ROOT / 'docs/demo/hanger.html'
BASE = 'docs/team/baseline-t11-p3/snapshot.json'
P2 = 'docs/verification/T11-P2-tests.json'
RECEIPT = ROOT / 'docs/verification/T11-P3-tests.json'


def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def read(path):
    return json.loads((ROOT / path).read_bytes())


def run(command, cwd=ROOT):
    p = subprocess.run(
        command, cwd=cwd, capture_output=True, encoding='utf-8', errors='replace', timeout=180
    )
    if p.returncode != 0 or '### Error' in p.stdout:
        raise RuntimeError((p.stderr + '\n' + p.stdout)[-2500:])
    return p.stdout + p.stderr


def python(*args):
    return run([sys.executable, '-B', '-X', 'utf8', *args])


def inventory():
    python('tools/check_integration_power_transport.py', '--check-receipt')
    for name, expected in read(BASE).items():
        if sha(name) != expected:
            raise ValueError('frozen input changed: ' + name)
    paths = set(read(P2)['reviewed_sha256']) | set(read(BASE))
    paths.update(
        (
            BASE,
            P2,
            'tools/check_hanger_panel.py',
            'docs/demo/hanger.html',
            'docs/T11-RESOURCE-REVIEW.md',
            'docs/T11-ACCEPTANCE-MATRIX.md',
            'docs/team/assignments/integration-t11-p3.md',
            'docs/T11-REPLAY-UI.md',
            'docs/team/reports/projection-t11-p3.md',
            'docs/team/reports/resource-t11-p3.md',
            'output/playwright/t11-p3/desktop.png',
            'output/playwright/t11-p3/mobile.png',
        )
    )
    paths.update(
        P3 + name
        for name in (
            'build_panel.py',
            'projection.py',
            'panel.html',
            'panel.css',
            'panel.js',
            'browser_review.js',
            'manifest.json',
            'test_projection.py',
        )
    )
    return {name: sha(name) for name in sorted(paths)}


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def browser_check():
    cli = shutil.which('playwright-cli')
    command = (
        [cli]
        if cli
        else [
            shutil.which('npx.cmd' if os.name == 'nt' else 'npx') or 'npx',
            '--offline',
            '--yes',
            '--package',
            '@playwright/cli',
            'playwright-cli',
        ]
    )
    session = '-s=hanger-' + uuid.uuid4().hex[:10]
    handler = partial(QuietHandler, directory=str(PAGE.parent))
    # Windows may briefly retain the CLI working directory after the session closes.
    with tempfile.TemporaryDirectory(prefix='hanger-ui-', ignore_cleanup_errors=True) as tmp:
        with ThreadingHTTPServer(('127.0.0.1', 0), handler) as server:
            Thread(target=server.serve_forever, daemon=True).start()
            try:
                channel = 'msedge' if os.name == 'nt' else 'chrome'
                run(
                    [
                        *command,
                        session,
                        'open',
                        f'http://127.0.0.1:{server.server_port}/hanger.html',
                        '--browser=' + channel,
                    ],
                    tmp,
                )
                output = run(
                    [
                        *command,
                        session,
                        'run-code',
                        '--filename=' + str(ROOT / P3 / 'browser_review.js'),
                    ],
                    tmp,
                )
                m = re.search(r'^### Result\s*\n(\{[^\n]+\})', output, re.MULTILINE)
                if not m:
                    raise ValueError('browser matrix returned no result')
                result = json.loads(m[1])
            finally:
                server.shutdown()
                run([*command, session, 'close'], tmp)
    return dict(channel=channel, **result)


def direct_file():
    if os.name == 'nt':
        candidates = [
            Path(os.environ.get('PROGRAMFILES', 'C:/Program Files'))
            / 'Google/Chrome/Application/chrome.exe',
            Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)'))
            / 'Microsoft/Edge/Application/msedge.exe',
        ]
        exe = next((str(p) for p in candidates if p.is_file()), None)
    else:
        exe = shutil.which('google-chrome') or shutil.which('chromium')
    if not exe:
        raise ValueError('installed Chrome or Edge is required for direct file verification')
    with tempfile.TemporaryDirectory(prefix='hanger-file-', ignore_cleanup_errors=True) as profile:
        output = run(
            [
                exe,
                '--headless',
                '--disable-gpu',
                '--no-first-run',
                '--disable-extensions',
                '--disable-background-networking',
                '--user-data-dir=' + profile,
                '--dump-dom',
                PAGE.as_uri(),
            ]
        )
    if not (
        re.search(r'id="headline">探头检测到受水</h3>', output)
        and re.search(r'id="received">00:26</dd>', output)
    ):
        raise ValueError('direct file page did not render the default frame')
    return dict(executable=Path(exe).name, rendered=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--write-receipt', action='store_true')
    mode.add_argument('--check-receipt', action='store_true')
    args = parser.parse_args()
    sources = inventory()
    if args.check_receipt:
        r = read(RECEIPT)
        if (
            r.get('passed') is not True
            or r.get('milestone') != 'P3'
            or r.get('collection_authorized') is not False
            or r.get('reviewed_sha256') != sources
        ):
            raise ValueError('stale P3 receipt')
        print(f'T11 P3 receipt current: {len(sources)} files')
        return
    python('-m', 'research.integration_t11.p3.build_panel')
    suites = python('-m', 'unittest', 'research.integration_t11.p3.test_projection')
    tests = int(re.search(r'Ran (\d+) tests', suites)[1])
    browser = browser_check()
    opened = direct_file()
    frames = read(P3 + 'manifest.json')['frames']
    if (
        not tests
        or browser['cases'] != len(frames)
        or browser['frames'] != sum(frames.values())
        or not browser['semanticChecks']
        or inventory() != sources
    ):
        raise ValueError('incomplete browser coverage or inputs changed during review')
    r = dict(
        task='T11',
        milestone='P3',
        passed=True,
        verified_at=datetime.now(timezone.utc).isoformat(),
        label='SYNTHETIC_UNCALIBRATED',
        collection_authorized=False,
        projection_tests=tests,
        page_reproduced=True,
        browser=browser,
        direct_file=opened,
        reviewed_sha256=sources,
    )
    if args.write_receipt:
        RECEIPT.write_bytes((json.dumps(r, ensure_ascii=False, indent=2) + '\n').encode())
    print(json.dumps({k: v for k, v in r.items() if k != 'reviewed_sha256'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
