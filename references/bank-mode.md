# Bank Mode

Source: a **list of target words** (or a single word). For each word, find a natural example sentence inside Nate's locally-indexed subs2srs `.apkg` banks, reuse the bank's original audio + screenshot when present, and produce a draft card per word.

When this mode triggers:
- Nate pastes a list of Korean words and says anything like "make cards", "mine these", "find sentences for", "leech these"
- Nate says "what's left in <show>" or "pull i+1 from my <show> bank"
- Nate writes `/sentence-mining` with no URL and a list of words

If Nate gives a URL → that's [video-mode](video-mode.md), not bank mode.

## Why bank mode exists

Video mode is great when Nate has a *specific clip* he wants to mine. But often the trigger is the other direction: "I keep forgetting `동기`, give me a card." Bank mode answers that — it searches a corpus of Korean sentences Nate already has indexed and pulls a sentence that uses the word in context, with the original native audio when the bank shipped one.

Cards built this way are tagged `claude-sentence-bank` (vs. `claude-sentence-mining` for video-sourced cards).

## Prerequisites

1. **At least one `.apkg` indexed** — see [setup](#one-time-setup-indexing-banks) below.
2. **AnkiConnect running** (port 8765) — same as video mode.
3. **GEMINI_API_KEY** in the skill's `.env` — only needed when a bank lacks audio for a chosen sentence (we synthesize via Gemini TTS).

## One-time setup: indexing banks

Banks are the `.apkg` files in `config.banks.source_dir` (set at setup). Extract them into local JSON indexes:

```bash
# No args → indexes every .apkg in config.banks.source_dir into config.banks.index_dir:
python3 <skill-dir>/scripts/extract_bank.py
# Or point at a single file:
python3 <skill-dir>/scripts/extract_bank.py "/path/to/specific.apkg"
```

Output lands in `config.banks.index_dir` (default `~/Downloads/sentence-mining/banks/index/`):
- `<bank-id>.notes.json` — searchable index (1 record per note: sentence, audio refs, image refs, meaning)
- `<bank-id>.media/` — extracted audio+image with original filenames

The `<bank-id>` is derived from the source filename (NFC-normalized, alnum + Korean + dash + underscore preserved).

**Re-extraction is safe** — JSON is overwritten. Run it again when Nate adds new banks.

## Step 1: Intake — collect the word list

Words can come in as:
- Comma-separated in chat: `동기, 서력, 한력`
- A file path Nate pastes
- A leech export Nate pastes (one word per line, possibly with reading/meaning columns — keep just the first column)

Normalize to a plain list of strings. Deduplicate preserving order.

## Step 2: Search banks

```bash
python3 <skill-dir>/scripts/search_banks.py \
    --words "동기,서력,한력" \
    --top-per-word 3 \
    --output ~/Downloads/sentence-mining/banksearch.json
```

For each word, the script scans every `<bank-id>.notes.json`, finds notes whose sentence contains the word as a literal substring, and ranks hits by:

| signal | points |
|---|---|
| Bank has audio for this note | +6 |
| Bank has image for this note | +4 |
| target_word field exactly equals the search word | +5 |
| Sentence length 15–50 chars (sweet spot for review) | +3 |
| Sentence length 8–14 or 51–80 chars (OK) | +1 |
| Word appears at a clean boundary (punctuation or end) | +2 |

Ties break by shorter sentence.

The script also records `bank_meaning` (English translation if the bank ships one), useful for sanity-checking the sense matches the one Nate wanted.

### Known limitation: literal substring matching

Current search is substring-only. `먹다` won't match a sentence with `먹고` or `먹어`. To handle conjugated forms, tokenize the bank sentences with kiwipiepy at index time and store per-note lemma sets, then match lemma-to-lemma. Not done yet — this is the obvious next improvement.

## Step 3: Curate — pick which hits become cards

Inline, look at the top hits per word and pick. Heuristic:

- **Prefer the one with audio + image** even if the sentence is slightly less elegant.
- **Clean obvious noise.** If the sentence is `(line1) (line2)` (subs2srs concatenated two subtitle frames), drop the chunk that doesn't contain the target word.
- **Drop dupes vs. existing cards.** Query AnkiConnect for `<word-field>:<lemma>` across the configured mining decks and skip any word that already has a card.
- **For zero-hit words**, tell Nate and ask. He may want to (a) index more banks, (b) accept a synthetic Claude-written example sentence with Gemini TTS, or (c) skip.

For each kept candidate, set `deck` to `config.decks.main`.

## Step 4: Generate explanations

Same as video-mode Step 4 — see [explanation-prompt.md](explanation-prompt.md). Produce one `explanation` field per candidate inline in Korean, ~200–250 chars, no English.

## Step 5: Generate media (bank-mode)

```bash
python3 <skill-dir>/scripts/generate_media_bank.py \
    --candidates ~/Downloads/sentence-mining/banksearch.json \
    --output ~/Downloads/sentence-mining/bank.draft.json
```

For each candidate:
- If `existing_audio` resolves to a real file → copies it to `<anki-media>/sm_bank_<bank-id>_<note-id>_sentence.<ext>`.
- Else → calls Gemini TTS on the sentence text → `<anki-media>/sm_bank_<bank-id>_<note-id>_sentence_tts.mp3`.
- If `existing_image` resolves → copies it as `<anki-media>/sm_bank_<bank-id>_<note-id>_image.<ext>`. Else leaves picture field blank (push.py fills with `。`).
- Always runs Gemini TTS on the `explanation` text → `<anki-media>/sm_explain_bank_<bank-id>_<note-id>.mp3`.

## Step 6: Preview + approval

Print a summary and wait for Nate's confirmation:

```
Mined N bank cards (word list: <words>):

  1. 동기 [goblin_s1] 🔊🖼
     "그와 동기인 두 사람은 모두 강하고..."
  2. 서력 [signal_s1]
     "서력 2166년에는..." (sentence TTS synthesized; no image)

  Misses: 한력 (no hit across 10 indexed banks)
```

Always list misses explicitly so Nate can choose to add more banks or accept synthetic fallback.

## Step 7: Push

Same `push.py` as video mode — the script detects `source == "bank-search"` and applies the `claude-sentence-bank` permanent tag, plus the `i?` level tag.

Bank cards always land in `config.decks.main` (no Deferred routing).

## Gotchas specific to bank mode

- **Substring match misses conjugations.** Korean agglutination means `동기` won't match `동기가` if the particle is written together. In practice most subs2srs sentences keep the word followed by a space or particle, so this often works. A real fix needs kiwipiepy tokenization at index time.
- **No known-check yet.** Bank mode pushes a card even if Nate already knows the word. Wiring the known-set check into `search_banks.py` is the obvious next step.
