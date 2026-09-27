# Known words — the built-in i+1 diff (no AnkiMorphs)

The whole point of i+1 mining is to only make cards for words the learner does
**not** already know. This skill computes the "known" set itself — it does **not**
read `ankimorphs.db` and does **not** require the AnkiMorphs add-on.

## How "known" is computed

Configured in `config.json` under `known_words`:

```jsonc
"known_words": {
  "interval_threshold": 21,   // a lemma is "known" once its highest card interval >= this
  "cache_hours": 6,           // reuse the scanned set for N hours (0 = always rescan)
  "sources": [
    { "query": "note:\"Korean Sentence Mining\"", "field": "sentence" },
    { "query": "deck:\"Kimchi Reader\"", "field": "word" }
  ]
}
```

For every source (`analyze.py` → `load_known_intervals`):

1. `findCards(query)` — `query` is any Anki search (deck, note type, tag…).
2. `cardsInfo` in chunks of 500 → for each card, read the `field` value and the card's `interval`.
3. Strip HTML, then tokenize the field with **kiwipiepy**.
4. For each content lemma, record its **highest** card interval across all cards.

A lemma counts as **known** when that highest interval ≥ `interval_threshold`.

### Why kiwipiepy (and why same-tokenizer matters)

The miner tokenizes the *mined* sentences with the same `tokenize()`. The *known*
set is built with the **same tokenizer** and the same `get_lemma()` function, so a
lemma the learner knows maps to the same string in both places — no cross-tokenizer
leakage where a known word slips through as a false "unknown".

kiwipiepy handles Korean morphological analysis natively (no JVM, no mecab install):
- Splits eojeols into morphemes: `먹었다` → `먹/VV + 었/EP + 다/EF`
- `get_lemma(token)` reconstructs dictionary forms: verbs/adjectives get `다` appended (VV `먹` → `먹다`), nouns returned as-is
- Pure Python, fast enough for large collections

### How lemma matching works for Korean variants

Korean spelling variants are less common than Japanese, but occur (e.g. 돼 vs 되어, 예쁘다 vs 이쁘다). kiwipiepy's normalization handles most of these at the morpheme level — both sides use the same tokenizer so variants that tokenize the same way are matched automatically. If a known variant doesn't match, it will simply be re-mined (low risk: the card will be rejected as a duplicate when pushed).

### Grammar patterns are known-diffed the same way

Grammar patterns (see [`scripts/grammar_patterns.json`](../scripts/grammar_patterns.json)
/ [`scripts/grammar.py`](../scripts/grammar.py)) are treated as words for known-diffing
too — "grammar is just words." `load_known_intervals` runs the grammar matcher over
every known-source card's tokenized field text, alongside the existing content-word
loop, and records each matched pattern's highest card interval into the exact same
`intervals` map, keyed by the pattern's canonical id (e.g. `-는데`). A pattern counts
as known once that interval crosses the same `interval_threshold` — no separate
threshold or config.

**Sentence-bearing sources matter more here.** A known-word source whose field is a
single vocabulary lemma (e.g. a `word` field on a vocab deck) will almost never
contain a full grammar-ending sequence, so it contributes nothing to grammar
known-diffing. Sources whose field is a **full sentence** (e.g. the sentence-mining
deck's own `sentence` field) are what actually let a mature `-는데` card suppress
future `-는데` mining. Prefer configuring at least one sentence-bearing source if you
want grammar known-diffing to work at all.

## Choosing sources

Mirror whatever fields actually contain Korean the learner has studied:
- the **sentence-mining deck's** sentence field (captures every word seen in context);
- any **vocab decks** (the word field); tokenizing a single-word field just yields that word.

Also include any Kimchi Reader / AnkiMorphs known-word lists as additional sources.

## Threshold

`interval_threshold` default is **21** — the universal AnkiMorphs convention for
"mature/known". Raising it makes the skill mine more aggressively (treats fewer
words as known); lowering it mines less.

## Caching

Scanning a large collection takes a while — dominated by the AnkiConnect pull of card
data. The result is cached at `<work_dir>/.known_cache.json`, keyed on the exact
`sources` list (so editing config auto-invalidates) and the threshold, with a TTL of
`cache_hours`. Force a rescan with `analyze.py --refresh-known`. In manifest
(multi-video) mode the known set is loaded once and shared across all videos.

Editing `scripts/grammar_patterns.json` (adding/changing patterns) also
auto-invalidates this cache — the cache key incorporates the pattern file's mtime
alongside the `sources` list, so no manual `--refresh-known` is needed after a
pattern-dictionary edit.
