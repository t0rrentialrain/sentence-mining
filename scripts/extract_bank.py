#!/usr/bin/env python3
"""Extract a .apkg into a normalized JSON index.

Output per bank: ~/Downloads/sentence-mining/banks/index/<bank-id>.notes.json

Each note record:
  {
    "bank_id":     "tokyo_ghoul_season_1",
    "note_id":     12345,
    "model":       "subs2srs",
    "sentence":    "(あの… 味覚が…変なんです)",
    "reading":     "",                                # furigana-annotated form, if available
    "meaning":     "the (sense of) taste; ...",       # english/translation, if available
    "target_word": "",                                # explicit vocab field, if available (often empty for sentence decks)
    "audio_files": ["foo.mp3"],
    "image_files": ["foo.jpg"],
    "media_paths": {"foo.mp3": "/abs/path/to/extracted/24", ...}
  }

The bank's media files stay extracted in ~/Downloads/sentence-mining/banks/index/<bank-id>.media/
(with their original filenames, NOT the numeric ZIP names).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

FIELD_SEP = "\x1f"

SOUND_RE = re.compile(r"\[sound:([^\]]+)\]")
IMG_RE = re.compile(r"<img[^>]+src=\"([^\"]+)\"")
HTML_TAG_RE = re.compile(r"<[^>]+>")
FURIGANA_RE = re.compile(r"([一-龯々]+)\[([ぁ-ゖァ-ヺー]+)\]")
JP_CHAR_RE = re.compile(r"[一-龯ぁ-ゖァ-ヺー]")


def bank_id_from_path(apkg: Path) -> str:
    stem = unicodedata.normalize("NFC", apkg.stem)
    # Keep ASCII alnum, dash, and Japanese chars; collapse runs to underscore.
    cleaned = re.sub(r"[^a-zA-Z0-9一-龯々ぁ-ゖァ-ヺー-]+", "_", stem).strip("_").lower()
    if not cleaned:
        cleaned = "bank"
    return cleaned


def strip_html(s: str) -> str:
    s = HTML_TAG_RE.sub("", s)
    s = s.replace("　", " ").replace("\xa0", " ")
    return s.strip()


def strip_furigana(s: str) -> str:
    return FURIGANA_RE.sub(r"\1", s)


def jp_char_count(s: str) -> int:
    return len(JP_CHAR_RE.findall(s))


def detect_field_roles(field_names: list[str], sample_rows: list[list[str]]) -> dict:
    """Return {role_name: field_index_or_None} based on field-name hints and content sampling.

    Roles: sentence, reading, meaning, target_word, audio, image
    """
    n = len(field_names)
    roles: dict[str, int | None] = {
        "sentence": None,
        "reading": None,
        "meaning": None,
        "target_word": None,
        "audio": None,
        "image": None,
    }

    # Per-field summary stats over sample
    stats = []
    for idx in range(n):
        vals = [(row[idx] if idx < len(row) else "") for row in sample_rows]
        stats.append({
            "idx": idx,
            "name": field_names[idx],
            "has_sound": any(SOUND_RE.search(v) for v in vals),
            "has_img": any(IMG_RE.search(v) for v in vals),
            "has_furigana": any(FURIGANA_RE.search(v) for v in vals),
            "avg_jp": sum(jp_char_count(strip_html(v)) for v in vals) / max(1, len(vals)),
            "avg_len": sum(len(strip_html(v)) for v in vals) / max(1, len(vals)),
            "mostly_ascii": all(
                (sum(1 for c in strip_html(v) if c.isascii()) / max(1, len(strip_html(v)))) > 0.8
                for v in vals
                if strip_html(v)
            ),
            "non_empty": sum(1 for v in vals if v.strip()),
        })

    # Audio: first field whose values contain [sound:...]
    for s in stats:
        if s["has_sound"]:
            roles["audio"] = s["idx"]
            break

    # Image: first field with <img src=
    for s in stats:
        if s["has_img"]:
            roles["image"] = s["idx"]
            break

    # Sentence: prefer fields with names that scream "sentence", then fall back to JP-char heuristic.
    media_idxs = {roles["audio"], roles["image"]}
    sentence_name_hints = (
        "expression", "sentence", "sentkanji", "sentjp", "japanese",
        "context", "text", "front",
    )
    notes_name_hints = ("note", "notes", "definition", "dict", "extra", "comment")

    def is_named(s, hints):
        nm = s["name"].lower().replace(" ", "").replace("_", "")
        return any(h in nm for h in hints)

    named_candidates = [
        s for s in stats
        if s["idx"] not in media_idxs and s["avg_jp"] > 0 and is_named(s, sentence_name_hints)
        and not is_named(s, notes_name_hints)
    ]
    if named_candidates:
        # Prefer the one with the LEAST furigana (clean form over annotated form).
        named_candidates.sort(key=lambda s: (s["has_furigana"], -s["avg_jp"], s["avg_len"]))
        roles["sentence"] = named_candidates[0]["idx"]
    else:
        candidates = [
            s for s in stats
            if s["idx"] not in media_idxs and s["avg_jp"] > 0
            and not is_named(s, notes_name_hints)
        ]
        if candidates:
            candidates.sort(key=lambda s: (s["has_furigana"], -s["avg_jp"], s["avg_len"]))
            roles["sentence"] = candidates[0]["idx"]

    # Reading: field with furigana brackets (separate from sentence if possible)
    for s in stats:
        if s["has_furigana"] and s["idx"] != roles["sentence"]:
            roles["reading"] = s["idx"]
            break
    if roles["reading"] is None and roles["sentence"] is not None:
        # Maybe sentence itself has furigana
        if stats[roles["sentence"]]["has_furigana"]:
            roles["reading"] = roles["sentence"]

    # Meaning: mostly-ascii text field
    for s in stats:
        if s["mostly_ascii"] and s["non_empty"] > 0 and s["idx"] not in (
            roles["audio"], roles["image"], roles["sentence"]
        ):
            roles["meaning"] = s["idx"]
            break

    # Target word: short Japanese field that's a substring of sentence, OR a field literally named Vocab/Word/Target.
    name_hints = {"vocab", "word", "target", "vocabkanji", "morph", "focusmorph", "expression"}
    sentence_idx = roles["sentence"]
    for s in stats:
        if s["idx"] in (roles["audio"], roles["image"]):
            continue
        nm = s["name"].lower().replace(" ", "").replace("_", "")
        if any(h in nm for h in ("vocab", "word", "target", "morph")):
            if s["avg_jp"] > 0 and s["avg_len"] < 20:
                roles["target_word"] = s["idx"]
                break
    # Fallback: a separate short Japanese field that's not sentence.
    if roles["target_word"] is None and sentence_idx is not None:
        for s in stats:
            if s["idx"] in (sentence_idx, roles["audio"], roles["image"]):
                continue
            if s["avg_jp"] > 0 and s["avg_len"] < 15:
                roles["target_word"] = s["idx"]
                break

    return roles


def extract_bank(apkg_path: Path, out_dir: Path, sample_size: int = 30) -> dict:
    bank_id = bank_id_from_path(apkg_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    media_out = out_dir / f"{bank_id}.media"
    media_out.mkdir(exist_ok=True)
    notes_out = out_dir / f"{bank_id}.notes.json"

    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        with zipfile.ZipFile(apkg_path) as zf:
            zf.extractall(tmp)

        db_path = tmp / "collection.anki21"
        if not db_path.exists():
            db_path = tmp / "collection.anki2"
        if not db_path.exists():
            raise RuntimeError(f"No collection.anki21/.anki2 in {apkg_path}")

        media_map = json.loads((tmp / "media").read_text())  # {"0": "foo.mp3", ...}

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        models = json.loads(cur.execute("SELECT models FROM col").fetchone()[0])

        # Sample-detect roles per model.
        model_roles: dict[int, dict] = {}
        model_info: dict[int, dict] = {}
        for mid, model in models.items():
            mid_i = int(mid)
            field_names = [f["name"] for f in model["flds"]]
            sample = [
                row[0].split(FIELD_SEP)
                for row in cur.execute(
                    "SELECT flds FROM notes WHERE mid = ? LIMIT ?",
                    (mid_i, sample_size),
                ).fetchall()
            ]
            roles = detect_field_roles(field_names, sample)
            model_roles[mid_i] = roles
            model_info[mid_i] = {"name": model["name"], "fields": field_names, "roles": roles}

        # Resolve which media files we need (those referenced in notes).
        needed_media: set[str] = set()
        records: list[dict] = []
        total = cur.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        for nid, mid, flds in cur.execute("SELECT id, mid, flds FROM notes"):
            roles = model_roles[mid]
            fields = flds.split(FIELD_SEP)

            def f(role: str) -> str:
                i = roles.get(role)
                if i is None or i >= len(fields):
                    return ""
                return fields[i]

            sentence_raw = f("sentence")
            sentence = strip_furigana(strip_html(sentence_raw)).strip()
            reading = strip_html(f("reading")).strip()
            meaning = strip_html(f("meaning")).strip()
            target_word = strip_furigana(strip_html(f("target_word"))).strip()

            audio_files = SOUND_RE.findall(f("audio"))
            image_files = IMG_RE.findall(f("image"))

            for fn in audio_files + image_files:
                needed_media.add(fn)

            if not sentence and not target_word:
                continue  # skip empty notes

            records.append({
                "bank_id": bank_id,
                "note_id": nid,
                "model": models[str(mid)]["name"],
                "sentence": sentence,
                "reading": reading,
                "meaning": meaning,
                "target_word": target_word,
                "audio_files": audio_files,
                "image_files": image_files,
            })

        # Reverse media_map (filename -> numeric zip name)
        rev_map = {v: k for k, v in media_map.items()}
        copied = 0
        missing = []
        for fn in needed_media:
            num = rev_map.get(fn)
            if num is None:
                missing.append(fn)
                continue
            src = tmp / num
            if not src.exists():
                missing.append(fn)
                continue
            dst = media_out / fn
            if not dst.exists():
                shutil.copy2(src, dst)
                copied += 1

    payload = {
        "bank_id": bank_id,
        "source_path": str(apkg_path),
        "models": model_info,
        "total_notes_in_db": total,
        "notes": records,
        "media_dir": str(media_out),
        "media_copied": copied,
        "media_missing": missing[:20],  # truncate
    }
    notes_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _config import load_config

    cfg = load_config(required=False) or {}
    banks_cfg = cfg.get("banks", {}) if cfg else {}
    default_out = banks_cfg.get("index_dir") or str(
        Path.home() / "Downloads/sentence-mining/banks/index"
    )
    default_src = banks_cfg.get("source_dir") or ""

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "apkg",
        nargs="?",
        default=default_src,
        help=".apkg file or directory of .apkg files (defaults to config.banks.source_dir)",
    )
    ap.add_argument("--out-dir", default=default_out)
    args = ap.parse_args()

    if not args.apkg:
        sys.exit(
            "No .apkg path given and config.banks.source_dir is empty.\n"
            "Pass a path or set it via `/sentence-mining setup`."
        )

    apkg_arg = Path(args.apkg).expanduser()
    out_dir = Path(args.out_dir).expanduser()

    if apkg_arg.is_dir():
        files = sorted(apkg_arg.glob("*.apkg"))
    else:
        files = [apkg_arg]

    if not files:
        print(f"No .apkg files found in {apkg_arg}", file=sys.stderr)
        sys.exit(1)

    for fp in files:
        print(f"=== {fp.name} ===", file=sys.stderr)
        try:
            res = extract_bank(fp, out_dir)
        except Exception as e:
            print(f"  FAIL: {e}", file=sys.stderr)
            continue
        print(
            f"  bank_id={res['bank_id']}  notes={len(res['notes'])}/{res['total_notes_in_db']}  "
            f"media_copied={res['media_copied']}  media_missing={len(res['media_missing'])}",
            file=sys.stderr,
        )
        for mid, info in res["models"].items():
            r = info["roles"]
            print(f"  model {info['name']}: roles={r}", file=sys.stderr)


if __name__ == "__main__":
    main()
