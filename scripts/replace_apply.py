#!/usr/bin/env python3
"""Replace mode — apply a reviewed replace-draft to Anki, in place.

For each entry (with `explanation` filled by Claude):
  - download the Immersion Kit image + audio (Linode CDN) and register them in
    Anki's media via storeMediaFile
  - Gemini-TTS the new explanation
  - ARCHIVE the current sentence (+ its old audio/image refs) into the
    `previous_versions` field, newest block first, so every prior version is
    recoverable and the old media never becomes "unused"
  - overwrite sentence / sentence_audio / picture / explanation / explanation_audio
  - retag the i-level (i1/i2/… reflecting how many other words are new for Ray)
  - rehabilitate the card (de-leech, unsuspend, reset scheduling so it's due)
  - clear the input flag:1 (default) so the redone card just rejoins the study
    queue — it's already reset to due. --done-flag N flags it instead.

Unfixable misses (no usable replacement in any corpus) are retired instead: tagged
`not-worth-learning`, suspended, and cleared off flag:1 (--keep-misses to skip).

`--dry-run` prints the old→new table and touches nothing (no downloads, no TTS,
no Anki writes) — use it for the review gate / summary.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze  # strip_html
import generate_media_bank as gmb  # reuse gemini_tts + GEMINI_* config
from _env import load_skill_env, warn_if_ephemeral_gemini_key
from _anki import anki_request, store_media
from _config import load_config, deck_main, deck_deferred


def _norm(s):
    """Comparable form: strip html + spaces + trailing punctuation (for dedupe checks)."""
    return re.sub(r"[\s　。、！？…\.\!\?]+", "", analyze.strip_html(s))

I_TAGS = " ".join(f"i{n}" for n in range(0, 10)) + " i?"  # i0..i9 + i? — clear all, add one
RETIRE_TAG = "not-worth-learning"  # disposition for unfixable misses


def _ext(url, default):
    tail = url.split("?")[0].rsplit(".", 1)
    return tail[1].lower() if len(tail) == 2 and len(tail[1]) <= 4 else default


def _download(url, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "ray-sentence-mining/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        f.write(r.read())


def _stage(src, dest: Path):
    """Put a remote URL or a local file path at `dest` so it can be stored in Anki."""
    if src.startswith("http://") or src.startswith("https://"):
        _download(src, dest)
    else:  # local sentence-bank media — copy in
        shutil.copyfile(src, dest)


async def process_one(entry, workdir: Path, sem):
    nid = entry["note_id"]
    base = f"sm_replace_{nid}"

    # An EMPTY picture field makes the note type's Back template re-render the
    # sentence audio (its {{^picture}} branch), so on mobile the sentence audio
    # autoplays on the back too. When the source ships no image, write "。" instead
    # of leaving it blank so {{#picture}} stays truthy and the back stays silent.
    if entry.get("image_url"):
        img_ext = _ext(entry["image_url"], "jpg")
        img_name = f"{base}_img.{img_ext}"
        _stage(entry["image_url"], workdir / img_name)
        store_media(workdir / img_name, img_name)
        entry["picture_field"] = f'<img src="{img_name}">'
    else:
        entry["picture_field"] = "。"

    snd_ext = _ext(entry["sound_url"], "mp3")
    snd_name = f"{base}_audio.{snd_ext}"
    _stage(entry["sound_url"], workdir / snd_name)
    store_media(workdir / snd_name, snd_name)
    entry["sentence_audio_field"] = f"[sound:{snd_name}]"

    # Explanation TTS is best-effort: a dead/expired GEMINI key must NOT block the
    # card's sentence/audio/image update. On failure leave explanation_audio empty
    # (the explanation TEXT still lands; audio can be regenerated later).
    exp_name = f"{base}_explain.mp3"
    try:
        await gmb.gemini_tts(entry["explanation"], workdir / exp_name, sem)
        store_media(workdir / exp_name, exp_name)
        entry["explanation_audio_field"] = f"[sound:{exp_name}]"
    except Exception as e:  # noqa: BLE001
        entry["explanation_audio_field"] = ""
        entry["tts_failed"] = True
        print(f"    · {entry['word']}: explanation TTS skipped ({str(e)[:60]})", file=sys.stderr)
    return entry


def archive_block(fm, old_snapshot, today):
    """Build the previous_versions block from the card's CURRENT sentence — TEXT ONLY
    (no audio/image; those stay on the live card and would just bloat the archive)."""
    old_text = analyze.strip_html(old_snapshot.get(fm["sentence"], "")).replace("\n", " ").strip()
    return f'<div class="sm-prev" data-archived="{today}">{old_text}</div>'


def set_flag(nid, flag):
    """Set the card's flag: 0 = remove the flag (default — a redone card just rejoins
    the study queue), 1-7 = a colored flag, None = leave the flag untouched. Anki flags
    are mutually exclusive, so setting one clears whatever the card was on (e.g. flag:1)."""
    if flag is None:
        return
    for c in anki_request("findCards", query=f"nid:{nid}"):
        anki_request("setSpecificValueOfCard", card=c, keys=["flags"],
                     newValues=[flag], warning_check=True)


def _miss_items(misses, fm):
    """Normalize the draft's `misses` to [(word, note_id)]. New drafts carry
    {word, note_id}; older drafts stored bare words — resolve those by the word field."""
    items = []
    for m in misses:
        if isinstance(m, dict):
            items.append((m.get("word", ""), m.get("note_id")))
        else:  # legacy: bare word string — look its card up
            hits = anki_request("findNotes", query=f'"{fm["word"]}:{m}" {deck_clause(load_config())}')
            items.append((m, hits[0] if hits else None))
    return items


def retire_note(nid):
    """An unfixable miss (no usable replacement in IK / Nadeshiko / bank) is retired:
    tag it `not-worth-learning`, suspend its cards, and clear the input flag so it
    leaves the fix queue and stops being re-picked by `replace_search --flag`."""
    if not nid:
        return []
    cards = anki_request("findCards", query=f"nid:{nid}")
    anki_request("addTags", notes=[nid], tags=RETIRE_TAG)
    if cards:
        try:
            anki_request("suspend", cards=cards)
        except Exception:  # noqa: BLE001
            pass
        for c in cards:
            anki_request("setSpecificValueOfCard", card=c, keys=["flags"],
                         newValues=[0], warning_check=True)
    return cards


def rehabilitate(nid):
    """A struggled-with card getting a fresh sentence should be re-learned cleanly:
    drop the leech tag, unsuspend (leeches are often auto-suspended), and reset the
    scheduling to new so it comes due again. Returns the card ids touched."""
    cards = anki_request("findCards", query=f"nid:{nid}")
    if not cards:
        return []
    anki_request("removeTags", notes=[nid], tags="leech")
    try:
        anki_request("unsuspend", cards=cards)        # no-op if not suspended
    except Exception:  # noqa: BLE001
        pass
    anki_request("forgetCards", cards=cards)          # reset scheduling → new → due
    # forgetCards resets scheduling but leaves the rep/lapse COUNTERS intact, so a
    # "rehabilitated" card keeps its lapse history and re-leeches far too soon. Zero
    # them too so the fresh sentence really starts from scratch. setSpecificValueOfCard
    # needs warning_check + integer values, and is per-card.
    for cid in cards:
        try:
            anki_request("setSpecificValueOfCard", card=cid,
                         keys=["reps", "lapses"], newValues=[0, 0], warning_check=True)
        except Exception:  # noqa: BLE001 — counter reset is best-effort
            pass
    return cards


def apply_entry(entry, fm, today, done_flag=0):
    nid = entry["note_id"]
    old = entry["old_fields_snapshot"]
    existing_prev = old.get(fm.get("previous_versions", ""), "")
    new_prev = archive_block(fm, old, today) + existing_prev  # newest first

    fields = {
        fm["sentence"]: entry["new_sentence"],
        fm["sentence_audio"]: entry["sentence_audio_field"],
        fm["picture"]: entry["picture_field"],
        fm["explanation"]: entry["explanation"],
        fm["explanation_audio"]: entry["explanation_audio_field"],
    }
    # Archive only if the note type has a previous_versions field (optional, replace-only).
    if fm.get("previous_versions"):
        fields[fm["previous_versions"]] = new_prev
    anki_request("updateNoteFields", note={"id": nid, "fields": fields})
    # Retag i-level; leave the flag and the kind tag (claude-sentence-*) untouched.
    anki_request("removeTags", notes=[nid], tags=I_TAGS)
    anki_request("addTags", notes=[nid], tags=entry["i_level"])
    # Fresh sentence on a struggled-with card → de-leech + reset scheduling so it's due.
    rehabilitate(nid)
    # Clear the input flag (default) so the redone card just rejoins the study queue —
    # the reset-to-due above already queues it up next. (--done-flag N to flag instead.)
    set_flag(nid, done_flag)


def print_table(entries, misses):
    print(f"\n{'WORD':<10} {'iLvl':<4}  OLD  →  NEW")
    print("─" * 90)
    for e in entries:
        print(f"{e['word']:<10} {e['i_level']:<4}  {analyze.strip_html(e['old_sentence'])[:32]}")
        print(f"{'':<16}→ {e['new_sentence']}   « {e.get('translation','')[:40]} » [{e.get('title','')}]")
    if misses:
        words = [m["word"] if isinstance(m, dict) else m for m in misses]
        print(f"\nMISSES (unfixable → will be tagged '{RETIRE_TAG}' + suspended): "
              f"{', '.join(words)}")


def deck_clause(cfg):
    return f'(deck:"{deck_main(cfg)}" OR deck:"{deck_deferred(cfg)}")'


async def main_async(args):
    cfg = load_config()
    fm = cfg["field_map"]

    if args.rehab_flag is not None:  # de-leech + reset-to-due a whole flagged set
        nids = anki_request("findNotes", query=f"flag:{args.rehab_flag} {deck_clause(cfg)}")
        for nid in nids:
            rehabilitate(nid)
        print(f"Rehabilitated {len(nids)} card(s) flagged {args.rehab_flag} "
              f"(leech tag removed, unsuspended, scheduling reset to due).")
        return

    data = json.loads(Path(args.draft).expanduser().read_text())
    entries = data.get("entries", [])
    misses = data.get("misses", [])

    ready = [e for e in entries if e.get("explanation", "").strip()]
    no_exp = [e["word"] for e in entries if not e.get("explanation", "").strip()]
    if no_exp:
        print(f"Skipping {len(no_exp)} entr(y/ies) with no explanation: {', '.join(no_exp)}",
              file=sys.stderr)

    if args.dry_run:
        print_table(ready, misses)
        retire_note_n = 0 if args.keep_misses else len(misses)
        print(f"\n[dry-run] {len(ready)} card(s) would be replaced, "
              f"{retire_note_n} unfixable miss(es) would be retired. Nothing written.")
        return

    # Idempotency guard: skip cards whose live sentence already equals the draft's new
    # one (re-running the same draft must NOT re-archive / double-apply).
    if ready:
        live = anki_request("notesInfo", notes=[e["note_id"] for e in ready])
        cur = {n["noteId"]: _norm(n["fields"].get(fm["sentence"], {}).get("value", "")) for n in live}
        already = [e for e in ready if cur.get(e["note_id"]) == _norm(e["new_sentence"])]
        if already:
            print(f"Skipping {len(already)} already-applied card(s): "
                  f"{', '.join(e['word'] for e in already)}", file=sys.stderr)
        ready = [e for e in ready if cur.get(e["note_id"]) != _norm(e["new_sentence"])]

    load_skill_env()
    warn_if_ephemeral_gemini_key()  # TTS is best-effort, so don't exit if missing/temporary

    today = datetime.date.today().isoformat()
    sem = asyncio.Semaphore(gmb.TTS_CONCURRENCY)
    with tempfile.TemporaryDirectory(prefix="sm_replace_") as tmp:
        workdir = Path(tmp)
        # Media download + TTS run concurrently (TTS gated to TTS_CONCURRENCY for the
        # Gemini RPM cap); Anki writes are serialized afterward to avoid write races.
        results = await asyncio.gather(
            *(process_one(e, workdir, sem) for e in ready), return_exceptions=True)
        done = []
        for e, r in zip(ready, results):
            if isinstance(r, Exception):
                print(f"  ✗ {e['word']} — {r}", file=sys.stderr)
                continue
            df = None if args.done_flag < 0 else args.done_flag
            apply_entry(e, fm, today, done_flag=df)
            done.append(e)
            print(f"  ✓ {e['word']} → {e['new_sentence'][:48]}", file=sys.stderr)

    if args.done_flag < 0:
        flag_note = "input flag left untouched"
    elif args.done_flag == 0:
        flag_note = "flag cleared, reset to due — queued up next"
    else:
        flag_note = f"moved to flag {args.done_flag}"
    print(f"\nReplaced {len(done)}/{len(ready)} cards in place ({flag_note}).")

    # Retire unfixable misses: tag not-worth-learning, suspend, clear the input flag so
    # they leave the fix queue. --keep-misses leaves them on the flag for a later pass.
    if misses:
        if args.keep_misses:
            words = [m["word"] if isinstance(m, dict) else m for m in misses]
            print(f"Misses (left on input flag): {', '.join(words)}")
        else:
            retired = []
            for word, nid in _miss_items(misses, fm):
                if retire_note(nid):
                    retired.append(word)
                else:
                    print(f"  · {word} — no card found to retire", file=sys.stderr)
            print(f"Retired {len(retired)} unfixable miss(es) "
                  f"(tagged '{RETIRE_TAG}', suspended): {', '.join(retired)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", help="Replace-draft JSON with explanations filled")
    ap.add_argument("--dry-run", action="store_true", help="Print old→new table; write nothing")
    ap.add_argument("--rehab-flag", type=int, default=None,
                    help="De-leech + unsuspend + reset-to-due every card flagged N "
                         "(no field changes). Use to rehabilitate an already-replaced batch.")
    ap.add_argument("--done-flag", type=int, default=0,
                    help="What to do with each redone card's flag. Default 0 = clear the "
                         "flag so it just rejoins the study queue (it's already reset to "
                         "due). 1-7 = set that colored flag instead. Negative = leave the "
                         "input flag untouched. Input is flag:1.")
    ap.add_argument("--keep-misses", action="store_true",
                    help="Leave unfixable misses on the input flag. Default retires them: "
                         f"tag '{RETIRE_TAG}', suspend, clear the flag.")
    args = ap.parse_args()
    if not args.draft and args.rehab_flag is None:
        ap.error("pass --draft <file> (optionally --dry-run) or --rehab-flag N")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
