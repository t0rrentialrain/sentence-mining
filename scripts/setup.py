#!/usr/bin/env python3
"""Setup helper for the sentence-mining skill.

Two jobs:

  --probe   Gather everything the `/sentence-mining setup` interview needs and
            print it as JSON: AnkiConnect reachability + note types and their
            fields + deck list, which CLI tools and Python packages are present,
            which API keys are set, and the current config (if any). Claude reads
            this, asks the user a few questions, and writes config.json directly.

  --validate <path>   Sanity-check an already-written config.json (note type
            exists, fields exist on it, decks resolvable). Prints warnings.

The interview itself (field mapping, deck choice, known-word sources, banks) is
driven by Claude in SKILL.md — this script only supplies machine-readable facts
and validation, which Claude can't reliably get any other way.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import load_config, CONFIG_PATH


def anki_request(url, action, **params):
    body = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        resp = json.loads(r.read())
    if resp.get("error"):
        raise RuntimeError(resp["error"])
    return resp["result"]


def probe(url):
    out = {
        "config_path": str(CONFIG_PATH),
        "config_exists": CONFIG_PATH.exists(),
        "current_config": load_config(required=False),
        "ankiconnect": {"url": url, "reachable": False, "version": None, "error": None},
        "note_types": {},   # name -> [field names]
        "decks": [],
        "tools": {},
        "python_packages": {},
        "api_keys": {},
    }

    # AnkiConnect + note types + decks
    try:
        out["ankiconnect"]["version"] = anki_request(url, "version")
        out["ankiconnect"]["reachable"] = True
        models = anki_request(url, "modelNames")
        for m in models:
            try:
                out["note_types"][m] = anki_request(url, "modelFieldNames", modelName=m)
            except Exception as e:  # noqa: BLE001
                out["note_types"][m] = {"error": str(e)}
        out["decks"] = anki_request(url, "deckNames")
    except (urllib.error.URLError, TimeoutError, ConnectionError, RuntimeError) as e:
        out["ankiconnect"]["error"] = str(e)

    # CLI tools
    for tool in ("yt-dlp", "ffmpeg"):
        out["tools"][tool] = bool(shutil.which(tool))

    # Python packages
    import importlib.util
    for pkg in ("google.genai", "kiwipiepy"):
        try:
            out["python_packages"][pkg] = importlib.util.find_spec(pkg) is not None
        except ModuleNotFoundError:
            # find_spec raises (rather than returning None) for a dotted path
            # whose parent package isn't installed at all, e.g. "google.genai"
            # when the "google" namespace package is entirely absent.
            out["python_packages"][pkg] = False

    # API keys (env or .env via _env)
    try:
        from _env import load_skill_env
        load_skill_env()
    except Exception:  # noqa: BLE001
        pass
    for key in ("GEMINI_API_KEY", "SONIOX_API_KEY"):
        out["api_keys"][key] = bool(os.environ.get(key))

    return out


def validate(url):
    cfg = load_config(required=False)
    problems = []
    if cfg is None:
        return {"ok": False, "problems": ["config.json does not exist"]}

    fm = cfg.get("field_map", {})
    if not fm.get("word"):
        problems.append("field_map.word is empty (required)")
    if not fm.get("sentence"):
        problems.append("field_map.sentence is empty (required)")
    if not cfg.get("note_type"):
        problems.append("note_type is empty (required)")

    try:
        models = anki_request(url, "modelNames")
        if cfg.get("note_type") and cfg["note_type"] not in models:
            problems.append(f"note_type {cfg['note_type']!r} not found in Anki")
        else:
            fields = anki_request(url, "modelFieldNames", modelName=cfg["note_type"])
            for role, fname in fm.items():
                if fname and fname not in fields:
                    problems.append(
                        f"field_map.{role} -> {fname!r} not a field of {cfg['note_type']!r}"
                    )
        decks = set(anki_request(url, "deckNames"))
        for which in ("main", "deferred"):
            d = cfg.get("decks", {}).get(which)
            if d and d not in decks:
                problems.append(f"decks.{which} {d!r} does not exist yet (will be created on push)")
    except (urllib.error.URLError, TimeoutError, ConnectionError, RuntimeError) as e:
        problems.append(f"could not reach AnkiConnect to validate: {e}")

    return {"ok": not problems, "problems": problems}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--url", default=None, help="Override AnkiConnect URL")
    args = ap.parse_args()

    cfg = load_config(required=False)
    url = args.url or (cfg or {}).get("anki_connect_url") or "http://localhost:8765"

    if args.validate:
        print(json.dumps(validate(url), ensure_ascii=False, indent=2))
    else:  # default to probe
        print(json.dumps(probe(url), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
