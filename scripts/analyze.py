#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tokenize Korean transcript with kiwipiepy, diff against known words, rank by KoFREN frequency.

Input: Soniox transcript JSON (from transcribe.py, post Step 2.5 splitting).
Output: candidate cards JSON per source.

Single-file (stdout):
    analyze.py --transcript T.json --source-id ID --source-url URL > cands.json

Manifest (multi-video, batched — shares one kiwipiepy + AnkiConnect load):
    analyze.py --manifest manifest.json --output-dir DIR
    # manifest.json is [{"transcript": "path", "source_id": "id", "source_url": "url"}, ...]
    # writes DIR/<source-id>.candidates.json per entry
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import load_config, deck_main, deck_deferred
from grammar import find_grammar_matches, patterns_mtime

CAP = 50

# kiwipiepy POS tags that are content words worth mining.
# NNG=일반명사, NNP=고유명사, NNB=의존명사, VV=동사, VA=형용사, MAG=부사, XR=어근
CONTENT_POS = {"NNG", "NNP", "NNB", "VV", "VA", "VX", "MAG", "XR", "MM"}
# Tags to explicitly skip (particles, endings, affixes, symbols, foreign, numbers)
SKIP_POS_PREFIXES = ("J", "E", "XP", "XS", "SF", "SP", "SS", "SE", "SO", "SB", "SL", "SH", "SN", "W_")

PURE_DIGITS = re.compile(r"^[0-9]+$")
PURE_ASCII = re.compile(r"^[A-Za-z]+$")
PUNCT_ONLY = re.compile(r"^[!?！？。、,.…\-—~\s]+$")
SINGLE_JAMO = re.compile(r"^[ㄱ-ㅎㅏ-ㅣ]$")  # lone consonant or vowel jamo
SHORT_JAMO_ONLY = re.compile(r"^[ㄱ-ㅎㅏ-ㅣ]{1,2}$")
HAS_HANGUL = re.compile(r"[가-힣]")

HTML_TAG_RE = re.compile(r"<[^>]+>")

# Runtime config — populated by main() from config.json.
ANKICONNECT = "http://localhost:8765"
MAIN_DECK = ""
DEFERRED_DECK = ""
DEDUPE_DECKS_QUERY = ""
ANKI_NOTE_FIELD = "wordForm"
KNOWN_INTERVAL_THRESHOLD = 21

_kiwi = None


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    return _kiwi


def get_lemma(token):
    """Return the dictionary form of a kiwipiepy token.

    For verbs (VV, VA, VX, VCP, VCN) and some bound morphemes, append 다 to form
    the infinitive that would appear in a dictionary. Nouns and adverbs are returned
    as-is. This keeps lemmas consistent between known-set scanning and candidate
    mining (both use the same function via the same tokenizer).
    """
    tag = str(token.tag)
    form = token.form
    if tag.startswith("V") or tag in ("XR",):
        return form + "다"
    return form


def tokenize(text):
    """Tokenize Korean text with kiwipiepy.

    Returns list of (surface, lemma, tag) per token where:
      surface = token.form (the actual text span)
      lemma   = dictionary form (via get_lemma)
      tag     = POS tag string (e.g. "NNG", "VV", "JKS")
    """
    kiwi = _get_kiwi()
    result = kiwi.tokenize(text)
    out = []
    for token in result:
        tag = str(token.tag)
        surface = token.form
        lemma = get_lemma(token)
        out.append((surface, lemma, tag))
    return out


def is_content_word(tag):
    # "GRAM" is a synthetic tag from grammar.find_grammar_matches(), never a
    # real kiwipiepy tag -- special-cased rather than added to CONTENT_POS,
    # which documents actual Kiwi POS tags only.
    if tag == "GRAM":
        return True
    if any(tag.startswith(p) for p in SKIP_POS_PREFIXES):
        return False
    return tag in CONTENT_POS


def is_card_worthy(lemma):
    if not lemma:
        return False
    if lemma.startswith("-"):
        # Grammar-pattern canonical ids (from grammar_patterns.json) always
        # start with a dash and are always card-worthy.
        return True
    if PURE_DIGITS.match(lemma) or PURE_ASCII.match(lemma) or PUNCT_ONLY.match(lemma):
        return False
    if SINGLE_JAMO.match(lemma) or SHORT_JAMO_ONLY.match(lemma):
        return False
    # Must contain at least one Hangul syllable block.
    if not HAS_HANGUL.search(lemma):
        return False
    return True


def strip_html(s):
    return HTML_TAG_RE.sub("", s.replace("<br>", "\n").replace("<br/>", "\n")).strip()


def load_known_intervals(sources):
    """Compute the known-lemma map directly from AnkiConnect — no AnkiMorphs needed.

    For each configured source (an Anki search + a note field), pull the cards,
    tokenize the field with kiwipiepy, and record every content lemma's HIGHEST
    card interval. A lemma is "known" once that interval >= interval_threshold.

    Returns {lemma: highest_interval_in_days}.
    Using the same tokenizer here and on mined sentences keeps lemmas aligned.
    """
    intervals = {}
    for src in sources or []:
        query = (src.get("query") or "").strip()
        field = (src.get("field") or "").strip()
        if not query or not field:
            continue
        card_ids = anki_request("findCards", query=query)
        if not card_ids:
            print(f"  known-source matched 0 cards: {query!r}", file=sys.stderr)
            continue
        seen_field = False
        for i in range(0, len(card_ids), 500):
            chunk = anki_request("cardsInfo", cards=card_ids[i: i + 500])
            for c in chunk:
                ivl = c.get("interval", 0) or 0
                fobj = c.get("fields", {}).get(field)
                if fobj is None:
                    continue
                seen_field = True
                text = strip_html(fobj.get("value", ""))
                if not text:
                    continue
                tokens = tokenize(text)
                for surface, lemma, tag in tokens:
                    if not is_content_word(tag) or not is_card_worthy(lemma):
                        continue
                    if ivl > intervals.get(lemma, -(10 ** 9)):
                        intervals[lemma] = ivl
                # Grammar side-stream: a mature card whose sentence contains
                # e.g. -는데 marks that pattern known too. Only matters for
                # sentence-bearing sources -- a single-lemma `word` field will
                # never contain a full grammar-ending sequence.
                for gm in find_grammar_matches(tokens):
                    lemma = gm["lemma"]
                    if ivl > intervals.get(lemma, -(10 ** 9)):
                        intervals[lemma] = ivl
        if not seen_field:
            print(
                f"  WARNING: field {field!r} not found on cards for {query!r} "
                f"(check field name in config.known_words)",
                file=sys.stderr,
            )
    return intervals


def load_word_list_known(specs):
    """Load known-word lists that live entirely outside Anki (Kimchi Reader /
    AnkiMorphs exports). The Anki-interval approach can only ever see words
    the learner has drilled with spaced repetition -- it's structurally blind
    to vocabulary picked up from reading/immersion that never became a card.
    These files are the learner's actual record of that broader known set.

    spec: {"path": "...", "format": "plain" | "csv_lemma" | "csv_status",
           "min_status": 2}  # csv_status only

    Formats seen in practice:
      - "plain": one lemma per line, bare Kiwi-morpheme stem (no 다 suffix on
        verbs/adjectives), possibly mixed with other languages -- non-Hangul
        lines are dropped.
      - "csv_lemma": header row literally "lemma" or "Morph-Lemma[,...]",
        first column is the lemma, same bare-stem convention as "plain".
      - "csv_status": no header, "word,status" rows, word already in full
        dictionary form (다 attached); only rows with status >= min_status
        count as known.

    Returns a set of lemma strings. For bare-stem entries we add both the
    stem and stem+"다", since analyze.py's own get_lemma() appends 다 to
    verb/adjective forms and the CSV exports don't -- without this, every
    verb/adjective in these lists would silently fail to match.
    """
    known = set()
    for spec in specs or []:
        path = os.path.expanduser(spec["path"])
        fmt = spec.get("format", "plain")
        if not os.path.exists(path):
            print(f"  word-list known source not found: {path}", file=sys.stderr)
            continue
        with open(path, encoding="utf-8-sig") as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        if not lines:
            continue

        if fmt == "csv_status":
            min_status = spec.get("min_status", 2)
            entries = []
            for ln in lines:
                parts = ln.split(",")
                if len(parts) < 2:
                    continue
                word, status = parts[0].strip(), parts[1].strip()
                try:
                    if int(status) >= min_status:
                        entries.append(word)
                except ValueError:
                    continue
            add_da_variant = False
        else:
            body = lines[1:] if lines[0].strip() in ("lemma", "Morph-Lemma", "Morph-Lemma,Occurrence") else lines
            entries = [ln.split(",")[0].strip() for ln in body]
            add_da_variant = True

        count = 0
        for e in entries:
            if not e or not HAS_HANGUL.search(e):
                continue
            known.add(e)
            if add_da_variant and not e.endswith("다"):
                known.add(e + "다")
            count += 1
        print(f"  word-list known source {os.path.basename(path)}: {count} entries",
              file=sys.stderr)
    return known


def _sources_key(sources, word_lists):
    import hashlib
    # Fold in grammar_patterns.json's mtime plus each word-list file's own
    # mtime, so editing the pattern dictionary or re-exporting a known-word
    # list auto-invalidates the cache (no manual --refresh-known needed).
    wl_mtimes = {}
    for spec in word_lists or []:
        p = os.path.expanduser(spec.get("path", ""))
        try:
            wl_mtimes[p] = os.path.getmtime(p)
        except OSError:
            wl_mtimes[p] = None
    blob = json.dumps(
        {"sources": sources, "patterns_mtime": patterns_mtime(), "word_lists": wl_mtimes},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def get_known_intervals(cfg, force_refresh=False):
    """load_known_intervals with an optional on-disk cache (default 6h TTL)."""
    kw = cfg["known_words"]
    sources = kw.get("sources", [])
    word_lists = kw.get("word_lists", [])
    cache_hours = kw.get("cache_hours", 6)
    work_dir = os.path.expanduser(cfg.get("work_dir") or "~/Downloads/sentence-mining")
    cache_path = os.path.join(work_dir, ".known_cache.json")
    key = _sources_key(sources, word_lists)

    if not force_refresh and cache_hours and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            age_h = (time.time() - cached.get("ts", 0)) / 3600
            if (cached.get("sources_key") == key
                    and cached.get("threshold") == KNOWN_INTERVAL_THRESHOLD
                    and age_h < cache_hours):
                print(f"  known-set: cache hit ({age_h:.1f}h old, "
                      f"{len(cached['intervals'])} lemmas)", file=sys.stderr)
                return cached["intervals"]
        except Exception:
            pass

    intervals = load_known_intervals(sources)
    for lemma in load_word_list_known(word_lists):
        if KNOWN_INTERVAL_THRESHOLD > intervals.get(lemma, -(10 ** 9)):
            intervals[lemma] = KNOWN_INTERVAL_THRESHOLD
    if cache_hours:
        try:
            os.makedirs(work_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({
                    "sources_key": key,
                    "threshold": KNOWN_INTERVAL_THRESHOLD,
                    "ts": time.time(),
                    "intervals": intervals,
                }, f, ensure_ascii=False)
        except Exception:
            pass
    return intervals


def load_frequency_priority(path):
    """Optional KoFREN (or other) frequency list for ranking unknowns.
    Each line is a lemma; earlier line = more frequent = higher priority.
    Missing/empty path → empty map (ranking falls back to source order).
    """
    priority = {}
    if not path or not os.path.exists(path):
        if path:
            print(f"  Frequency CSV not found at {path} — ranking by source order.",
                  file=sys.stderr)
        return priority
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i == 0:
                continue  # skip header
            lemma = line.strip().split(",")[0].strip()
            if lemma and lemma not in priority:
                priority[lemma] = i
    return priority


def anki_request(action, **params):
    """AnkiConnect call with retry-with-backoff."""
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


def existing_wordforms_in_deck():
    if not DEDUPE_DECKS_QUERY:
        return set()
    note_ids = anki_request("findNotes", query=DEDUPE_DECKS_QUERY)
    if not note_ids:
        return set()
    forms = set()
    for i in range(0, len(note_ids), 1000):
        chunk = anki_request("notesInfo", notes=note_ids[i: i + 1000])
        for n in chunk:
            wf = n["fields"].get(ANKI_NOTE_FIELD, {}).get("value", "").strip()
            if wf:
                forms.add(wf)
    return forms


def _dominant_speaker(sent):
    if sent.get("speaker"):
        return sent["speaker"]
    counts = {}
    for w in sent.get("words", []):
        sp = w.get("speaker")
        if sp:
            counts[sp] = counts.get(sp, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def analyze_one(transcript, source_id, source_url, intervals, priority, existing):
    annotated_sentences = []
    for idx, sent in enumerate(transcript["sentences"]):
        tokens = tokenize(sent["text"])
        unknown = []
        for surface, lemma, tag in tokens:
            if not is_content_word(tag) or not is_card_worthy(lemma):
                continue
            if intervals.get(lemma, 0) >= KNOWN_INTERVAL_THRESHOLD:
                continue
            unknown.append({"surface": surface, "lemma": lemma, "tag": tag})

        # Grammar side-stream: same raw token list, scanned for registered
        # grammar patterns (-는데, -고 있다, ...). Grammar patterns are "just
        # words" -- they flow through the same best-sentence-per-lemma and
        # candidate-building logic below via lemma/tag alone.
        for gm in find_grammar_matches(tokens):
            lemma = gm["lemma"]
            if intervals.get(lemma, 0) >= KNOWN_INTERVAL_THRESHOLD:
                continue
            unknown.append({
                "surface": gm["surface"], "lemma": lemma, "tag": "GRAM",
                "grammar_priority": gm["priority"],
            })
        annotated_sentences.append({
            "idx": idx,
            "text": sent["text"],
            "start_ms": sent["start_ms"],
            "end_ms": sent["end_ms"],
            "speaker": _dominant_speaker(sent),
            "words": sent["words"],
            "unknown_lemmas": unknown,
        })

    best_sentence_for_lemma = {}
    for sent in annotated_sentences:
        for u in sent["unknown_lemmas"]:
            lemma = u["lemma"]
            current = best_sentence_for_lemma.get(lemma)
            score = (len(sent["unknown_lemmas"]), sent["idx"])
            if current is None or score < current["score"]:
                best_sentence_for_lemma[lemma] = {
                    "score": score,
                    "sentence": sent,
                    "unknown_info": u,
                }

    candidates = []
    skipped_dupes = 0
    for lemma, picked in best_sentence_for_lemma.items():
        if lemma in existing:
            skipped_dupes += 1
            continue
        sent = picked["sentence"]
        info = picked["unknown_info"]
        unknown_count = len(sent["unknown_lemmas"])
        deck = MAIN_DECK if unknown_count == 1 else DEFERRED_DECK
        i_level = f"i{min(unknown_count, 9)}"

        target_start_ms = sent["start_ms"]
        for w in sent["words"]:
            if info["surface"] in w["text"] or w["text"] in info["surface"]:
                target_start_ms = w["start_ms"]
                break

        candidates.append({
            "lemma": lemma,
            "surface": info["surface"],
            "pos": info["tag"],
            "sentence": sent["text"],
            "sentence_idx": sent["idx"],
            "sentence_start_ms": sent["start_ms"],
            "sentence_end_ms": sent["end_ms"],
            "speaker": sent["speaker"],
            "target_word_start_ms": target_start_ms,
            "unknown_count_in_sentence": unknown_count,
            "deck": deck,
            "i_level": i_level,
            # Grammar candidates carry their own curriculum-order priority
            # (low numeric band, same role as a KoFREN frequency rank) so
            # word and grammar candidates sort together in one ranked list.
            "frequency_rank": (
                info["grammar_priority"] if info["tag"] == "GRAM"
                else priority.get(lemma, 10 ** 9)
            ),
        })

    candidates.sort(key=lambda c: (c["frequency_rank"], c["sentence_idx"]))
    capped = candidates[:CAP]
    return {
        "source_id": source_id,
        "source_url": source_url,
        "stats": {
            "total_sentences": len(annotated_sentences),
            "unknown_lemmas_found": len(best_sentence_for_lemma),
            "skipped_duplicates": skipped_dupes,
            "candidates_after_cap": len(capped),
        },
        "candidates": capped,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript")
    ap.add_argument("--source-id")
    ap.add_argument("--source-url", default="")
    ap.add_argument("--manifest", help="JSON array of {transcript, source_id, source_url}.")
    ap.add_argument("--output-dir", help="Directory to write per-source candidate JSON when --manifest is used.")
    ap.add_argument("--refresh-known", action="store_true",
                    help="Force a rescan of the known-word set, ignoring the cache.")
    args = ap.parse_args()

    if not args.manifest and not args.transcript:
        ap.error("either --transcript or --manifest is required")

    global ANKICONNECT, MAIN_DECK, DEFERRED_DECK, DEDUPE_DECKS_QUERY
    global ANKI_NOTE_FIELD, KNOWN_INTERVAL_THRESHOLD
    cfg = load_config()
    ANKICONNECT = cfg["anki_connect_url"]
    MAIN_DECK = deck_main(cfg)
    DEFERRED_DECK = deck_deferred(cfg)
    ANKI_NOTE_FIELD = cfg["field_map"].get("word") or "wordForm"
    KNOWN_INTERVAL_THRESHOLD = cfg["known_words"].get("interval_threshold", 21)
    dedupe_decks = sorted({MAIN_DECK, DEFERRED_DECK} - {""})
    DEDUPE_DECKS_QUERY = (
        "(" + " OR ".join(f'deck:"{d}"' for d in dedupe_decks) + ")"
        if dedupe_decks else ""
    )

    intervals = get_known_intervals(cfg, force_refresh=args.refresh_known)
    print(f"  known lemmas (interval >= {KNOWN_INTERVAL_THRESHOLD}): "
          f"{sum(1 for v in intervals.values() if v >= KNOWN_INTERVAL_THRESHOLD)} "
          f"of {len(intervals)} seen", file=sys.stderr)
    priority = load_frequency_priority(cfg.get("frequency_csv") or cfg.get("jpdb_priority_csv"))
    existing = existing_wordforms_in_deck()

    if args.manifest:
        if not args.output_dir:
            ap.error("--output-dir is required with --manifest")
        os.makedirs(args.output_dir, exist_ok=True)
        with open(args.manifest, encoding="utf-8") as f:
            entries = json.load(f)
        summary = []
        for e in entries:
            with open(e["transcript"], encoding="utf-8") as tf:
                transcript = json.load(tf)
            out = analyze_one(transcript, e["source_id"], e.get("source_url", ""),
                              intervals, priority, existing)
            out_path = os.path.join(args.output_dir, f"{e['source_id']}.candidates.json")
            with open(out_path, "w", encoding="utf-8") as of:
                json.dump(out, of, ensure_ascii=False, indent=2)
            summary.append({
                "source_id": e["source_id"],
                "output": out_path,
                "candidates": out["stats"]["candidates_after_cap"],
                "duplicates_skipped": out["stats"]["skipped_duplicates"],
            })
        print(json.dumps({"results": summary}, ensure_ascii=False, indent=2))
    else:
        with open(args.transcript, encoding="utf-8") as f:
            transcript = json.load(f)
        out = analyze_one(transcript, args.source_id, args.source_url,
                          intervals, priority, existing)
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
