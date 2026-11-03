"""Read-only, credential-free curfew gate backed by the computer's live policy."""
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
import runpy
import time
from urllib.parse import parse_qs, urlparse

SCHEMA = 'curfew/1'
POLL_SECONDS = 4.0
POLL_INTERVAL = 0.2


@lru_cache(maxsize=1)
def policy_status():
    library = Path(os.environ.get('CURFEW_POLICY_LIB', '~/opt/workenv/curfew/curfew_policy.py')).expanduser()
    return runpy.run_path(str(library))['status']


def snapshot():
    try:
        config = Path(os.environ.get('CURFEW_CONFIG', '~/opt/workenv/curfew/policy.json')).expanduser()
        state = Path(os.environ.get('CURFEW_STATE', '~/.local/state/curfew/state.json')).expanduser()
        current = policy_status()(json.loads(config.read_text()), json.loads(state.read_text()))
        active = current['active']
        if type(active) is not bool:
            raise ValueError('Invalid policy result')
        available = True
    except Exception:
        active, available = True, False
    revision = hashlib.sha256(f'{available}:{active}'.encode()).hexdigest()
    return dict(schema=SCHEMA, active=active, available=available,
                lease_ms=6000 if available else 0, revision=revision)


def handle(handler):
    parsed = urlparse(handler.path)
    if parsed.path.rstrip('/') not in ('/curfew/status', '/novel/curfew/status'):
        return False
    query = parse_qs(parsed.query)
    previous = query.get('revision', [''])[0][:64]
    nonce = query.get('nonce', [''])[0][:80]
    deadline = time.monotonic() + POLL_SECONDS
    while True:
        value = snapshot()
        if not value['available'] or value['revision'] != previous or time.monotonic() >= deadline:
            break
        time.sleep(POLL_INTERVAL)
    # No schedule, filesystem paths, credentials, or suspend permissions are exposed.
    try:
        handler._send({**value, 'nonce': nonce})
    except (BrokenPipeError, ConnectionResetError):
        pass
    return True
