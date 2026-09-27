#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Grammar-pattern matcher -- treats Korean grammar patterns as mineable "words".

Loads grammar_patterns.json once and scans a flat kiwipiepy token list (the
same raw (surface, lemma, tag) list analyze.tokenize() returns, BEFORE
is_content_word() filtering strips E*/J* tags -- those are exactly the tags
grammar patterns are built from) for sequential pattern matches.

Match tuples mirror tokenize()'s shape so callers can splice them into the
existing unknown-lemma pipeline:
    {"surface": ..., "lemma": pattern_id, "tag": "GRAM", "priority": int}
"GRAM" is never a real kiwipiepy POS tag -- it flags a matched grammar pattern
so analyze.py can special-case it (frequency_rank source, curation exemption).

Self-test: `python grammar.py --selftest` tokenizes each pattern's `example`
sentence and asserts it matches that pattern's own id -- catches schema typos
(wrong tag, wrong form, missing allomorph) before they ship.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

_PATTERNS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grammar_patterns.json")
_patterns = None


def _load_patterns():
    global _patterns
    if _patterns is None:
        with open(_PATTERNS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _patterns = data["patterns"]
        # Longest-match-first so a multi-slot pattern is tried before a
        # shorter one could "steal" the starting token.
        _patterns.sort(key=lambda p: -len(p["match"]))
    return _patterns


def patterns_mtime():
    """mtime of the pattern dictionary, for cache-invalidation keys elsewhere."""
    return os.path.getmtime(_PATTERNS_PATH)


def _slot_matches(token, slot):
    surface, lemma, tag = token
    return tag in slot["tags"] and surface in slot["forms"]


def _try_match_at(tokens, start, pattern):
    """Try pattern['match'] starting at tokens[start].

    Returns the number of tokens consumed (>=1) on success, or None on
    failure. Optional slots may be skipped without consuming a token.
    """
    ti = start
    consumed_any = False
    for slot in pattern["match"]:
        if ti < len(tokens) and _slot_matches(tokens[ti], slot):
            ti += 1
            consumed_any = True
            continue
        if slot.get("optional"):
            continue
        return None
    return (ti - start) if consumed_any else None


def find_grammar_matches(tokens):
    """tokens: list of (surface, lemma, tag) as returned by analyze.tokenize().

    Returns a list of dicts, one per matched grammar pattern, in left-to-right
    order: {"surface", "lemma": pattern_id, "tag": "GRAM", "priority": int}.
    Greedy, longest-pattern-first, non-overlapping -- once a match consumes
    tokens[i:i+k], scanning resumes at i+k. Tokens that match no pattern are
    simply skipped (never surfaced as a standalone grammar candidate).
    """
    patterns = _load_patterns()
    matches = []
    i = 0
    n = len(tokens)
    while i < n:
        matched = False
        for pattern in patterns:
            k = _try_match_at(tokens, i, pattern)
            if k:
                span = tokens[i:i + k]
                matches.append({
                    "surface": "".join(t[0] for t in span),
                    "lemma": pattern["id"],
                    "tag": "GRAM",
                    "priority": pattern.get("priority", 10 ** 9),
                })
                i += k
                matched = True
                break
        if not matched:
            i += 1
    return matches


def _selftest():
    from kiwipiepy import Kiwi
    kiwi = Kiwi()
    patterns = _load_patterns()
    failures = []
    for pattern in patterns:
        example = pattern.get("example")
        if not example:
            failures.append((pattern["id"], "no example sentence"))
            continue
        tokens = [(t.form, t.form, str(t.tag)) for t in kiwi.tokenize(example)]
        matches = find_grammar_matches(tokens)
        ids = [m["lemma"] for m in matches]
        if pattern["id"] not in ids:
            failures.append((pattern["id"], f"did not match own example {example!r} -- got {ids}"))

    print(f"{len(patterns)} patterns, {len(failures)} failures")
    for pid, reason in failures:
        print(f"  FAIL {pid}: {reason}")
    if failures:
        sys.exit(1)
    print("all patterns matched their own example sentence")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__)
