#!/usr/bin/env python3
"""Replace mode — find a BETTER, more comprehensible example sentence for an
existing card, sourced from the Immersion Kit hosted corpus and re-ranked by
Ray's own i+1 (known-word diff), then write a replace-draft for review.

Why Immersion Kit and not the local banks: a head-to-head test (June 2026) on 12
flagged leech words found IK won 9, tied 1, with 0 misses vs the local index's 3
misses — IK's corpus is far broader and its `sort=sentence_length:asc` + a length
floor skips the `(speaker) word word` subtitle junk that local subs2srs banks leak.
The one thing IK lacks is knowledge of what *Ray* knows, so we graft that back on:
every IK candidate is tokenized with the same SudachiPy + known-word machinery the
miner already uses (analyze.py) and ranked by how few OTHER words are unknown.

Input  — which cards to fix (one of):
    --flag N            every card flagged N in the mining decks
    --note-ids a,b,c    explicit Anki note ids
    --words 同期,西暦   resolve the existing card for each word in the mining decks

Output — replace-draft JSON (proposals, no media downloaded, no explanation yet):
    [{ note_id, word, reading, old_sentence, old_fields_snapshot,
       new_sentence, translation, image_url, sound_url, title, ik_id,
       i_level, extra_unknowns, bare_token }]
Claude then fills `explanation` per entry and runs replace_apply.py.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze  # reuse tokenizer + known-word diff (does NOT run its main())
from _config import load_config, deck_main, deck_deferred
from _anki import anki_request
from _env import load_skill_env

IK_SEARCH = "https://apiv2.immersionkit.com/search"
NADESHIKO_SEARCH = "https://api.nadeshiko.co/v1/search"  # fallback corpus
LEN_MIN = 10          # below this, IK hits are usually fragments
LEN_MAX = 45          # above this, the clip becomes a wall of text
LEN_SWEET = 22        # ideal length; ranking prefers candidates near this
FETCH_LIMIT = 30      # candidates to pull per word before filtering
REQUEST_DELAY = 1.2   # seconds between IK requests — apiv2 429s on bursts


def _norm(s):
    """Collapse to comparable form: strip html, spaces, and trailing ellipsis/punct."""
    return re.sub(r"[\s　。、！？…\.\!\?]+", "", analyze.strip_html(s))


def ik_search(word, limit=FETCH_LIMIT):
    """Query Immersion Kit /search. Returns examples[] (with full media URLs).

    apiv2 throttles bursts aggressively (HTTP 429 / 'No route to host'), so retry
    with exponential backoff. The caller also spaces requests via REQUEST_DELAY."""
    qs = urllib.parse.urlencode({
        "q": word,
        "exactMatch": "true",
        "sort": "sentence_length:asc",
        "showUrlInMedia": "true",
        "limit": str(limit),
    })
    url = f"{IK_SEARCH}?{qs}"
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ray-sentence-mining/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read()).get("examples", [])
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(5 * (attempt + 1))  # back off on rate limit
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(3 * (attempt + 1))       # transient (throttle drops the route)
    raise RuntimeError(f"IK unreachable after retries: {last}")


def nadeshiko_search(word, key, limit=FETCH_LIMIT):
    """Fallback source. POST /v1/search, normalize to the same `ex` shape ik_search
    returns (sentence/word_list/translation/image/sound/title/id) so the scorer and
    filters work unchanged. Nadeshiko has a different (drama-heavy) corpus, which
    covers some words IK's anime-skewed corpus misses."""
    body = json.dumps({
        "query": {"search": word, "exactMatch": True},
        "take": limit,
        "sort": {"mode": "ASC"},                       # shortest first
        "filters": {"category": ["ANIME", "JDRAMA"]},
    }).encode()
    url, last = NADESHIKO_SEARCH, None
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json",
                         "User-Agent": "ray-sentence-mining/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(5 * (attempt + 1)); continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
    else:
        raise RuntimeError(f"Nadeshiko unreachable after retries: {last}")

    out = []
    for s in data.get("segments", []):
        tj = s.get("textJa") or {}
        urls = s.get("urls") or {}
        toks = tj.get("tokens") or []
        wl = [t.get("surface") or t.get("text") or "" for t in toks] if isinstance(toks, list) else []
        out.append({
            "sentence": tj.get("content", ""),
            "word_list": wl,
            "translation": (s.get("textEn") or {}).get("content", ""),
            "image": urls.get("imageUrl", ""),
            "sound": urls.get("audioUrl", ""),
            "title": s.get("mediaPublicId", "nadeshiko"),
            "id": s.get("publicId", ""),
            "sentence_with_furigana": "",
        })
    return out


def jp_len(s):
    return len(analyze.strip_html(s))


def _is_kanji(c):
    return "一" <= c <= "鿿" or c == "々"


# Honorifics that turn a noun into a proper name (紅葉くん = "Momiji", not "autumn leaves").
NAME_MARKERS = ("くん", "ちゃん", "さん", "様", "氏", "先輩", "君")
# Idioms where the target carries a non-literal meaning that mis-teaches the word.
IDIOM_BLOCKLIST = {"呆れる": ("聞いて呆れる",)}


def looks_misleading(sentence, target_word):
    """True if every occurrence of the target is glued into a kanji compound
    (攻撃抑制 for 抑制, 戦略級 for 戦略), reads as a name (紅葉くん), or sits inside a
    blocklisted idiom — i.e. the sentence would mis-teach the word."""
    s = sentence.replace(" ", "").replace("　", "")
    for idiom in IDIOM_BLOCKLIST.get(target_word, ()):  # idiom check on raw text
        if idiom.replace(" ", "") in s:
            return True
    clean_occurrence = False
    i = s.find(target_word)
    while i != -1:
        after_pos = i + len(target_word)
        before = s[i - 1] if i > 0 else ""
        after = s[after_pos] if after_pos < len(s) else ""
        # Compound-glue only counts when the kanji of the TARGET butts against another
        # kanji: 攻撃|抑制 (kanji before a kanji-initial target) or 戦略|級 (kanji-final
        # target before a kanji). A kana-ending verb followed by a kanji word —
        # 和らげる|為, 整える|事 — is normal grammar, NOT a compound.
        prefix_glue = _is_kanji(before) and _is_kanji(target_word[0])
        suffix_glue = _is_kanji(target_word[-1]) and _is_kanji(after)
        named = any(s[after_pos:].startswith(m) for m in NAME_MARKERS)
        if not prefix_glue and not suffix_glue and not named:
            clean_occurrence = True
            break
        i = s.find(target_word, i + 1)
    return not clean_occurrence


def score_candidate(ex, target_word, known):
    """Return (rank_tuple, extra_unknowns, bare_token, i_level) for an IK example,
    or None if the candidate should be dropped outright.

    `known` is (intervals, norm_intervals, mature_stems, threshold)."""
    intervals, norm_intervals, mature_stems, threshold = known
    sentence = ex.get("sentence", "")
    n = jp_len(sentence)
    if not (LEN_MIN <= n <= LEN_MAX):
        return None
    if not (ex.get("image") and ex.get("sound")):
        return None  # replace mode wants a card with media
    if target_word not in sentence:
        return None
    if looks_misleading(sentence, target_word):
        return None  # compound-glued, a proper name, or a mis-teaching idiom

    word_list = ex.get("word_list", [])
    bare_token = target_word in word_list  # standalone, not glued into a compound

    # Count content words OTHER than the target that Ray doesn't yet know.
    extra_unknowns = []
    for surface, lemma, normalized, reading, pos in analyze.tokenize(sentence):
        if not analyze.is_content_word(pos):
            continue
        if not analyze.is_card_worthy(lemma):
            continue
        if target_word in (lemma, surface, normalized):
            continue
        if intervals.get(lemma, 0) >= threshold:
            continue
        if normalized and norm_intervals.get(normalized, 0) >= threshold:
            continue
        stem = analyze.extract_kanji_stem(lemma)
        if stem and stem in mature_stems:
            continue
        extra_unknowns.append(lemma)

    n_extra = len(extra_unknowns)
    i_level = f"i{min(n_extra + 1, 9)}"  # target is the +1 by definition
    # Rank: fewest other-unknowns first, then bare token, then closest to sweet length.
    rank = (n_extra, 0 if bare_token else 1, abs(n - LEN_SWEET))
    return rank, extra_unknowns, bare_token, i_level


def _score_examples(examples, word, known, old_norm, source):
    scored = []
    for ex in examples:
        if old_norm and _norm(ex.get("sentence", "")) == old_norm:
            continue  # identical to the card's current sentence → no improvement
        res = score_candidate(ex, word, known)
        if res is None:
            continue
        rank, extra, bare, i_level = res
        scored.append({
            "rank": rank,
            "new_sentence": ex["sentence"],
            "sentence_with_furigana": ex.get("sentence_with_furigana", ""),
            "translation": ex.get("translation", ""),
            "image_url": ex.get("image", ""),
            "sound_url": ex.get("sound", ""),
            "title": ex.get("title", ""),
            "ik_id": ex.get("id", ""),
            "source": source,
            "i_level": i_level,
            "extra_unknowns": extra,
            "bare_token": bare,
        })
    scored.sort(key=lambda c: c["rank"])
    return scored


def bank_examples(word, banks):
    """Normalize local sentence-bank hits into the same `ex` shape the scorer uses.
    Media are local file paths (not URLs); replace_apply stores them directly."""
    from search_banks import search_word
    out = []
    for h in search_word(word, banks, FETCH_LIMIT):
        if not (h.get("existing_audio") and h.get("existing_image")):
            continue  # replace mode wants a card with media
        out.append({
            "sentence": h["sentence"],
            "word_list": [],                      # banks don't tokenize → not bare-flagged
            "translation": h.get("bank_meaning", ""),
            "image": h["existing_image"],         # local path
            "sound": h["existing_audio"],         # local path
            "title": h.get("bank_id", "bank"),
            "id": str(h.get("note_id", "")),
            "sentence_with_furigana": "",
        })
    return out


def best_for_word(word, known, old_sentence="", top=3, nadeshiko_key=None, banks=None):
    """Canonical source order: Immersion Kit → Nadeshiko → local sentence bank.
    Each tier is tried only if the previous one yields nothing usable for Ray."""
    old_norm = _norm(old_sentence)
    try:
        scored = _score_examples(ik_search(word), word, known, old_norm, "immersionkit")
    except Exception as e:  # noqa: BLE001
        print(f"  · {word} — IK error: {e}", file=sys.stderr)
        scored = []
    if not scored and nadeshiko_key:
        try:
            scored = _score_examples(nadeshiko_search(word, nadeshiko_key), word,
                                     known, old_norm, "nadeshiko")
        except Exception as e:  # noqa: BLE001
            print(f"  · {word} — Nadeshiko error: {e}", file=sys.stderr)
    if not scored and banks:
        try:
            scored = _score_examples(bank_examples(word, banks), word, known, old_norm, "bank")
        except Exception as e:  # noqa: BLE001
            print(f"  · {word} — bank error: {e}", file=sys.stderr)
    return scored[:top]


def resolve_notes(args, cfg):
    """Return [{note_id, word, reading, old_sentence, old_fields_snapshot}]."""
    fm = cfg["field_map"]
    wf, rf, sf = fm["word"], fm.get("reading", "reading"), fm["sentence"]
    main, deferred = deck_main(cfg), deck_deferred(cfg)
    deck_clause = f'(deck:"{main}" OR deck:"{deferred}")'

    note_ids = []
    if args.note_ids:
        note_ids = [int(x) for x in args.note_ids.split(",") if x.strip()]
    elif args.flag is not None:
        note_ids = anki_request("findNotes", query=f"flag:{args.flag} {deck_clause}")
    elif args.words:
        for w in [x.strip() for x in args.words.split(",") if x.strip()]:
            hits = anki_request("findNotes", query=f'"{wf}:{w}" {deck_clause}')
            if hits:
                note_ids.append(hits[0])
            else:
                print(f"  ✗ {w} — no existing card in mining decks", file=sys.stderr)
    if not note_ids:
        sys.exit("No target notes. Pass --flag N, --note-ids, or --words.")

    info = anki_request("notesInfo", notes=note_ids)
    notes = []
    for n in info:
        f = n["fields"]
        notes.append({
            "note_id": n["noteId"],
            "word": f.get(wf, {}).get("value", ""),
            "reading": analyze.strip_html(f.get(rf, {}).get("value", "")),
            "old_sentence": analyze.strip_html(f.get(sf, {}).get("value", "")),
            "old_fields_snapshot": {k: v.get("value", "") for k, v in f.items()},
        })
    return notes


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--flag", type=int, help="Operate on every card flagged N in mining decks")
    g.add_argument("--note-ids", help="Comma-separated Anki note ids")
    g.add_argument("--words", help="Comma-separated words (resolve their existing card)")
    ap.add_argument("--output", required=True, help="Write replace-draft JSON here")
    ap.add_argument("--refresh-known", action="store_true",
                    help="Rescan the known-word set instead of using the cache")
    ap.add_argument("--top", type=int, default=3, help="Runner-ups to keep per word")
    args = ap.parse_args()

    cfg = load_config()
    analyze.KNOWN_INTERVAL_THRESHOLD = cfg["known_words"].get("interval_threshold", 21)
    load_skill_env()
    nadeshiko_key = os.environ.get("NADESHIKO_API_KEY") or ""
    if nadeshiko_key:
        print("Nadeshiko fallback: enabled", file=sys.stderr)
    else:
        print("Nadeshiko fallback: off (no NADESHIKO_API_KEY)", file=sys.stderr)
    notes = resolve_notes(args, cfg)
    print(f"Resolved {len(notes)} target notes", file=sys.stderr)

    print("Loading known-word set (cached)…", file=sys.stderr)
    intervals, norm_intervals = analyze.get_known_intervals(cfg, force_refresh=args.refresh_known)
    mature_stems = analyze.load_mature_kanji_stems(intervals)
    known = (intervals, norm_intervals, mature_stems, analyze.KNOWN_INTERVAL_THRESHOLD)

    # 3rd-tier fallback: local sentence banks (if indexed).
    banks = None
    idx = os.path.expanduser((cfg.get("banks") or {}).get("index_dir") or "")
    if idx and os.path.isdir(idx):
        try:
            from search_banks import load_indexes
            from pathlib import Path
            banks = load_indexes(Path(idx))
            print(f"Bank fallback: {len(banks)} indexed bank(s)", file=sys.stderr)
        except (Exception, SystemExit) as e:  # noqa: BLE001
            # load_indexes raises SystemExit (not Exception) when the index dir
            # exists but holds no *.notes.json — tier-3 banks are optional in
            # replace mode, so treat that as "no banks" rather than aborting.
            banks = None
            print(f"Bank fallback: unavailable ({e})", file=sys.stderr)
    else:
        print("Bank fallback: off (no indexed banks)", file=sys.stderr)

    out = []
    misses = []
    for i, note in enumerate(notes):
        if i:
            time.sleep(REQUEST_DELAY)  # space requests so apiv2 doesn't 429
        word = note["word"]
        picks = best_for_word(word, known, old_sentence=note["old_sentence"],
                              top=args.top, nadeshiko_key=nadeshiko_key, banks=banks)
        if not picks:
            # Carry the note_id so replace_apply can retire the card (no usable
            # replacement in any corpus → tag not-worth-learning + suspend).
            misses.append({"word": word, "note_id": note["note_id"]})
            print(f"  ✗ {word} — no usable replacement (IK + fallback)", file=sys.stderr)
            continue
        best = picks[0]
        flag = "·bare" if best["bare_token"] else "·partial"
        src = "" if best["source"] == "immersionkit" else f" «{best['source']}»"
        print(f"  ✓ {word} [{best['i_level']}{flag}]{src} {best['new_sentence'][:48]}",
              file=sys.stderr)
        entry = dict(note)
        entry.update({
            "new_sentence": best["new_sentence"],
            "sentence_with_furigana": best["sentence_with_furigana"],
            "translation": best["translation"],
            "image_url": best["image_url"],
            "sound_url": best["sound_url"],
            "title": best["title"],
            "ik_id": best["ik_id"],
            "source": best["source"],
            "i_level": best["i_level"],
            "extra_unknowns": best["extra_unknowns"],
            "bare_token": best["bare_token"],
            "explanation": "",            # Claude fills this inline
            "runner_ups": picks[1:],      # kept for "try a different sentence"
        })
        out.append(entry)

    payload = {"source": "replace-immersionkit", "misses": misses, "entries": out}
    os.makedirs(os.path.dirname(os.path.expanduser(args.output)) or ".", exist_ok=True)
    with open(os.path.expanduser(args.output), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {len(out)} proposals ({len(misses)} misses) to {args.output}",
          file=sys.stderr)
    if misses:
        print(f"  misses: {', '.join(m['word'] for m in misses)}", file=sys.stderr)


if __name__ == "__main__":
    main()
