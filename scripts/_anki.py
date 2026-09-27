"""Tiny AnkiConnect helpers shared across the skill's scripts.

Why route media writes through AnkiConnect's `storeMediaFile` instead of
shutil.copy directly into `collection.media`: add-ons that hook media events
(notably AJT Media Converter) treat externally-written files as "stale" and
may remove them. Going through Anki's media manager registers the file so
hooks see it as a legitimate add.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from _config import load_config
except ImportError:  # when imported as a package
    from ._config import load_config


def _connect_url() -> str:
    cfg = load_config(required=False)
    if cfg and cfg.get("anki_connect_url"):
        return cfg["anki_connect_url"]
    return "http://localhost:8765"


def anki_request(action: str, **params):
    """Call AnkiConnect. Retries up to 3 times with backoff on transient errors."""
    body = json.dumps({"action": action, "version": 6, "params": params}).encode()
    url = _connect_url()
    last_exc = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
            if resp.get("error"):
                raise RuntimeError(f"AnkiConnect: {resp['error']}")
            return resp["result"]
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"AnkiConnect failed after 3 attempts: {last_exc}")


def store_media(local_path: Path | str, target_filename: str) -> str:
    """Register `local_path` in Anki's media folder as `target_filename`.

    Returns the filename (same as input) so callers can drop it into a field.
    """
    path = str(Path(local_path).expanduser().resolve())
    anki_request("storeMediaFile", filename=target_filename, path=path)
    return target_filename
