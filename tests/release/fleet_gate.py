"""Run the app release suite against the image built by fleet integration CI.

Only the disposable daemon's loopback registry receives the candidate. This
is a test gate, not a publisher. Keep the previous public release digest
explicit so updating the supported upgrade baseline is reviewable.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess  # nosec B404
import sys
import time
import uuid
from defusedxml import ElementTree as ET

from tests.release.release_contract import validate_release_inputs
from tests.release.release_ops import d, verify_dind_session

PREVIOUS = 'dub19/gbrain-aio@sha256:1e95618ebb6216513121a90b84a1b9a88752ef46e4cbfb663d4acf6f14659240'
REGISTRY = 'registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373'


def verify_release_daemon() -> None:
    # No host socket fallback. The current fleet integration build is amd64.
    verify_dind_session(validate_release_inputs(dict(os.environ, AIO_RELEASE_ARCH='amd64',
                                                    AIO_PREVIOUS_IMAGE=PREVIOUS, AIO_CANDIDATE_IMAGE=PREVIOUS)))


def qualify(image: str, output: Path) -> None:
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'DOCKER_HOST', 'DOCKER_TLS_VERIFY',
                                       'DOCKER_CERT_PATH', 'GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT') if k in os.environ}
    env.update(AIO_RELEASE_ARCH='amd64', AIO_PREVIOUS_IMAGE=PREVIOUS)
    image_id = d('image', 'inspect', '--format', '{{.Id}}', image)
    d('pull', '--platform=linux/amd64', PREVIOUS, timeout=600)
    registry = d('run', '-d', '-p', '127.0.0.1::5000', REGISTRY)
    assert registry
    tag = None
    try:
        address = d('port', registry, '5000/tcp')
        assert address and address.startswith('127.0.0.1:') and address.split(':')[1].isdigit()
        for _ in range(30):
            if d('exec', registry, 'wget', '-qO-', 'http://127.0.0.1:5000/v2/', check=False) is not None:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Loopback registry did not become ready')
        repo = address + '/gbrain-' + uuid.uuid4().hex
        tag = repo + ':test'
        d('tag', image, tag)
        d('push', tag, timeout=900)
        refs = d('image', 'inspect', '--format', '{{range .RepoDigests}}{{println .}}{{end}}', tag) or ''
        candidate, = [r for r in refs.splitlines() if r.startswith(repo + '@sha256:')]
        assert d('image', 'inspect', '--format', '{{.Id}}', candidate) == image_id
        env.update(AIO_CANDIDATE_IMAGE=candidate, AIO_RELEASE_RECEIPTS_DIR=str(output))
        junit = output / 'release-junit.xml'
        subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests/release', '--junitxml', str(junit)],
                       check=True, env=env, timeout=1500)  # nosec B603
        cases = ET.parse(junit).findall('.//testcase')
        required = {'test_saved_reservation_upgrade_writes_after_replace_and_restart',
                    'test_missing_reservation_fails_closed_not_a_qualified_upgrade'}
        assert required <= {c.get('name') for c in cases}, 'Missing required upgrade test'
        assert all(c.find('skipped') is None for c in cases), 'Skipped release test'
        print(f'GBrain release qualification: {len(cases)} passed; both ownership cases executed; '
              f'previous={PREVIOUS} candidate={candidate}', flush=True)
    finally:
        if tag:
            d('image', 'rm', tag, check=False)
        d('rm', '-fv', registry)
