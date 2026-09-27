#!/usr/bin/env python3
"""Push staged draft cards to Anki via AnkiConnect.

Reports per-card success/failure but never aborts the batch — a late-detected
duplicate shouldn't stop the other 19 cards from landing.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# Katakana incl. long-vowel ー and middle dot ・ — used to detect loanwords whose
# reading is just the word itself.
ALL_KATAKANA = re.compile(r"^[゠-ヿ]+$")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import load_config

ANKICONNECT = "http://localhost:8765"
MODEL = ""
FIELD_MAP = {}


def anki_request(action, **params):
    """Retry up to 3 times with 1s/2s/4s backoff on transient socket errors."""
    body = json.dumps({"action": action, "version": 6, "params": params}).encode()
    last_exc = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                ANKICONNECT,
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


PERMANENT_TAG_VIDEO = "claude-sentence-mining"
PERMANENT_TAG_BANK = "claude-sentence-bank"


def build_note(candidate, source_url, permanent_tag, source_id=None):
    speaker = candidate.get("speaker")
    # Highlight the target word's surface form in the sentence, matching the
    # existing `<span c-orange="">` convention already used on Nate's manually
    # imported Kimchi Reader cards. Also keeps the Sentence field (the note
    # type's first/dupe-check field) textually distinct when two different
    # lemmas are mined from the same source sentence.
    sentence_text = candidate["sentence"]
    surface = candidate.get("surface")
    if surface and surface in sentence_text:
        sentence_text = sentence_text.replace(
            surface, f'<span c-orange="">{surface}</span>', 1
        )
    # Prepend a small speaker prefix to the sentence so the card shows who's
    # talking. Skip if no diarization data — keep older drafts working.
    if speaker:
        sentence_text = f"<b>{speaker}:</b> {sentence_text}"

    # Tags policy: claude-* permanent tag plus the i-level (i1, i2, i3, i?…) so Ray
    # can filter cards by complexity in Anki. Other contextual data (speaker, bank_id,
    # source_id, source_url, auto-mined date) lives in the draft JSON for debugging
    # only — those don't ride along into Anki.
    tags = [permanent_tag]
    i_level = candidate.get("i_level", "").strip()
    if i_level:
        tags.append(i_level)

    sentence_audio_file = candidate.get("sentenceAudio_file", "")
    picture_file = candidate.get("picture_file", "")
    explanation_audio_file = candidate.get("explanationAudio_file", "")

    # Map the skill's internal field roles onto the user's actual note-type field
    # names. Only roles that are mapped (non-empty in config.field_map) get
    # written — so any note type with at least `word` + `sentence` works, and we
    # never send a field name the model doesn't have (AnkiConnect would error).
    # For all-katakana loanwords (アバウト, アメリカーノ…) the reading is just the
    # word itself, so mirror it rather than leaving the field blank.
    word = candidate["lemma"]
    reading = candidate.get("reading", "")
    if ALL_KATAKANA.match(word):
        reading = word

    role_values = {
        "word": word,
        "reading": reading,
        "sentence": sentence_text,
        "sentence_audio": f"[sound:{sentence_audio_file}]" if sentence_audio_file else "",
        # An EMPTY picture field makes the note type's Back template take its
        # {{^picture}} branch, which re-renders sentence_audio and forces .audio to
        # display — so on AnkiMobile/AnkiDroid the sentence audio autoplays on the
        # BACK too (double audio). Any non-empty value flips {{#picture}} truthy and
        # silences the back replay. "。" is the minimal harmless filler.
        "picture": f'<img src="{picture_file}">' if picture_file else "。",
        "explanation": candidate.get("explanation", ""),
        "explanation_audio": f"[sound:{explanation_audio_file}]" if explanation_audio_file else "",
        "source_url": source_url,
    }
    fields = {}
    for role, value in role_values.items():
        field_name = FIELD_MAP.get(role)
        if field_name:
            fields[field_name] = value

    return {
        "deckName": candidate["deck"],
        "modelName": MODEL,
        "fields": fields,
        "tags": tags,
        "options": {"allowDuplicate": False},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True)
    args = ap.parse_args()

    global ANKICONNECT, MODEL, FIELD_MAP
    cfg = load_config()
    ANKICONNECT = cfg["anki_connect_url"]
    MODEL = cfg["note_type"]
    FIELD_MAP = cfg["field_map"]
    if not MODEL:
        sys.exit("config.note_type is empty — run `/sentence-mining setup`.")
    if not FIELD_MAP.get("word") or not FIELD_MAP.get("sentence"):
        sys.exit("config.field_map needs at least `word` and `sentence` — run setup.")

    with open(args.draft, encoding="utf-8") as f:
        data = json.load(f)

    permanent_tag = PERMANENT_TAG_BANK if data.get("source") == "bank-search" else PERMANENT_TAG_VIDEO

    # Make sure both decks exist (createDeck is idempotent).
    decks_needed = {c["deck"] for c in data["candidates"]}
    for d in decks_needed:
        anki_request("createDeck", deck=d)

    notes = [
        build_note(c, data.get("source_url", ""), permanent_tag, data.get("source_id"))
        for c in data["candidates"]
    ]

    # addNotes returns a list of new note IDs (or None for failures, in order).
    note_ids = anki_request("addNotes", notes=notes)

    added = 0
    failed = []
    for candidate, note_id in zip(data["candidates"], note_ids):
        if note_id is None:
            failed.append(candidate["lemma"])
            continue
        added += 1

    print(json.dumps({
        "added": added,
        "failed": failed,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
