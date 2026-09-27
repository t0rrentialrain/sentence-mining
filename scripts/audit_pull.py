#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pull every card in a deck (word + sentence + note ID) for audit mode.

The actual review — flagging non-words, misattributed sentences, and picking
the best of duplicate cards — needs real linguistic judgment, so it's done by
Claude inline (see references/audit-mode.md), not here. This script just does
the deterministic part: fetch, strip HTML, and hand back clean JSON.

Usage:
    audit_pull.py                       # audits config.decks.main
    audit_pull.py --deck "Some::Deck"   # audits a specific deck instead
    audit_pull.py --deck "A" --deck "B" # audits multiple decks at once
"""
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import load_config, deck_main
from _anki import anki_request

HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    return HTML_TAG_RE.sub("", s or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", action="append", default=None,
                     help="Deck to audit; repeatable. Defaults to config.decks.main.")
    args = ap.parse_args()

    cfg = load_config()
    word_field = cfg["field_map"].get("word")
    sentence_field = cfg["field_map"].get("sentence")
    if not word_field or not sentence_field:
        sys.exit("config.field_map needs both `word` and `sentence` mapped — run setup.")

    decks = args.deck or [deck_main(cfg)]
    decks = [d for d in decks if d]
    if not decks:
        sys.exit("No deck to audit — pass --deck or set config.decks.main via setup.")

    query = " OR ".join(f'deck:"{d}"' for d in decks)
    note_ids = anki_request("findNotes", query=query)
    if not note_ids:
        print(json.dumps({"deck": decks, "cards": []}, ensure_ascii=False))
        return

    cards = []
    for i in range(0, len(note_ids), 1000):
        chunk = anki_request("notesInfo", notes=note_ids[i:i + 1000])
        for n in chunk:
            fields = n.get("fields", {})
            word = strip_html(fields.get(word_field, {}).get("value", ""))
            sentence = strip_html(fields.get(sentence_field, {}).get("value", ""))
            if not word:
                continue
            cards.append({"noteId": n["noteId"], "word": word, "sentence": sentence})

    print(json.dumps({"deck": decks, "cards": cards}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
