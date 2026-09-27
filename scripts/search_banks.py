#!/usr/bin/env python3
"""Find sentences across pre-indexed banks for a list of target words.

Inputs:
  --words "同期,西暦,和暦"          (comma-separated, or one per line via --words-file)
  --index-dir <path>                (default ~/Downloads/sentence-mining/banks/index)
  --top-per-word N                  (default 3)
  --output <path>                   (writes candidates JSON; also prints summary to stderr)

Ranking per (word, candidate sentence):
  1. Has audio file        +6
  2. Has image file        +4
  3. target_word field exactly == word   +5
  4. Sentence length in sweet spot (15-50 chars)  +3
  5. Sentence length 8-14 or 51-80         +1
  6. Word appears as whole-segment (preceded/followed by punctuation or boundary)  +2
  7. Bank with more total sentence-bearing notes  small bonus

Output JSON: same shape as analyze.py's candidates list, so downstream pipeline can reuse:
  {
    "source_id": "banksearch-<timestamp>",
    "source": "bank-search",
    "candidates": [
      {
        "lemma": "同期",
        "reading": "",
        "sentence": "...",
        "i_level": "i1" | "in" | "i?",
        "deck": "<config.decks.main>",
        "wordForm": "同期",
        "existing_audio":  "<abs path to extracted media>",
        "existing_image":  "<abs path or empty>",
        "bank_id": "...",
        "note_id": ...,
        "bank_meaning": "...",         # English translation if available, for context
        "jpdb_rank": ...,
        "unknown_count_in_sentence": null,
        "speaker": null,
      },
      ...
    ]
  }
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import load_config, deck_main

DEFAULT_INDEX = Path.home() / "Downloads/sentence-mining/banks/index"
# Set from config in main(); the deck new bank cards land in.
MAIN_DECK = ""

# Sentence-length sweet spot for review cards.
LEN_SWEET = (15, 50)
LEN_OK = (8, 80)


def load_indexes(index_dir: Path) -> list[dict]:
    files = sorted(index_dir.glob("*.notes.json"))
    if not files:
        raise SystemExit(f"No *.notes.json in {index_dir} — run extract_bank.py first.")
    return [json.loads(f.read_text()) for f in files]


def score_candidate(word: str, note: dict, bank: dict) -> tuple[int, float]:
    s = note["sentence"]
    L = len(s)
    score = 0
    if note["audio_files"]:
        score += 6
    if note["image_files"]:
        score += 4
    if note.get("target_word") == word:
        score += 5
    if LEN_SWEET[0] <= L <= LEN_SWEET[1]:
        score += 3
    elif LEN_OK[0] <= L <= LEN_OK[1]:
        score += 1
    # Whole-segment-ish: word followed/preceded by punctuation or boundary
    idx = s.find(word)
    if idx >= 0:
        before = s[idx - 1] if idx > 0 else ""
        after = s[idx + len(word)] if idx + len(word) < L else ""
        boundary_chars = "。、！？!?・「」『』（）()「」 　\n\t…—"
        if before in boundary_chars or before == "" or after in boundary_chars or after == "":
            score += 2
    # Tiebreak: longer = lower priority (we want concise)
    return score, -L


def search_word(word: str, banks: list[dict], top: int) -> list[dict]:
    hits: list[tuple[int, float, dict, dict]] = []
    for bank in banks:
        for note in bank["notes"]:
            s = note["sentence"]
            if not s or word not in s:
                continue
            sc, tiebreak = score_candidate(word, note, bank)
            hits.append((sc, tiebreak, note, bank))
    hits.sort(key=lambda x: (-x[0], -x[1]))
    out = []
    for sc, _tb, note, bank in hits[:top]:
        media_dir = bank["media_dir"]
        audio = note["audio_files"][0] if note["audio_files"] else ""
        image = note["image_files"][0] if note["image_files"] else ""
        audio_path = ""
        image_path = ""
        if audio:
            p = Path(media_dir) / audio
            if p.exists():
                audio_path = str(p)
        if image:
            p = Path(media_dir) / image
            if p.exists():
                image_path = str(p)
        out.append({
            "lemma": word,
            "wordForm": word,
            "reading": note.get("reading", ""),
            "sentence": note["sentence"],
            "i_level": "i?",                        # bank mode skips morph diff
            "deck": MAIN_DECK,
            "existing_audio": audio_path,
            "existing_image": image_path,
            "bank_id": bank["bank_id"],
            "note_id": note["note_id"],
            "bank_meaning": note.get("meaning", "")[:200],
            "jpdb_rank": None,
            "unknown_count_in_sentence": None,
            "speaker": None,
            "score": sc,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", help="Comma-separated target words")
    ap.add_argument("--words-file", help="File with one word per line")
    ap.add_argument("--index-dir", default=None,
                    help="Override config.banks.index_dir")
    ap.add_argument("--top-per-word", type=int, default=3)
    ap.add_argument("--output", help="Write candidates JSON here", default=None)
    args = ap.parse_args()

    global MAIN_DECK
    cfg = load_config()
    MAIN_DECK = deck_main(cfg)
    index_dir = args.index_dir or cfg["banks"].get("index_dir") or str(DEFAULT_INDEX)

    words: list[str] = []
    if args.words:
        words += [w.strip() for w in args.words.split(",") if w.strip()]
    if args.words_file:
        words += [
            line.strip()
            for line in Path(args.words_file).expanduser().read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    # Dedupe preserving order
    seen = set()
    words = [w for w in words if not (w in seen or seen.add(w))]

    if not words:
        sys.exit("No words. Pass --words 'a,b,c' or --words-file path")

    banks = load_indexes(Path(index_dir).expanduser())

    bank_summary = ", ".join(f"{b['bank_id']}({len(b['notes'])})" for b in banks)
    print(f"Loaded {len(banks)} banks: {bank_summary}", file=sys.stderr)
    print(f"Searching for {len(words)} words: {words}", file=sys.stderr)

    all_candidates = []
    misses = []
    for w in words:
        hits = search_word(w, banks, args.top_per_word)
        if not hits:
            misses.append(w)
            print(f"  ✗ {w} — no match", file=sys.stderr)
            continue
        print(f"  ✓ {w} — {len(hits)} hits", file=sys.stderr)
        for h in hits:
            audio = "🔊" if h["existing_audio"] else " "
            image = "🖼" if h["existing_image"] else " "
            print(
                f"      [{audio}{image} score={h['score']:2d}] {h['bank_id'][:25]:25s}  "
                f"{h['sentence'][:60]}",
                file=sys.stderr,
            )
        all_candidates.extend(hits)

    payload = {
        "source": "bank-search",
        "source_id": f"banksearch-{int(time.time())}",
        "words_requested": words,
        "words_missed": misses,
        "candidates": all_candidates,
    }
    out_str = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).expanduser().write_text(out_str)
        print(f"\nWrote {len(all_candidates)} candidates to {args.output}", file=sys.stderr)
    else:
        print(out_str)


if __name__ == "__main__":
    main()
