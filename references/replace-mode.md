# Replace mode

Fix **existing** cards whose example sentence is bad — too short, a mid-conversation
fragment, a tokenizer/proper-noun false positive, or just incomprehensible standalone.
Replace mode swaps in a better, more comprehensible sentence **in place** and archives
the old one. It does NOT create new cards (that's bank/video mode).

## Canonical source order: local sentence bank

The skill currently uses **local indexed banks** as the only sentence source for replace
mode. A Korean corpus API tier (analogous to Immersion Kit / Nadeshiko for Japanese) is
planned but not yet integrated. When one is added, it will go before the local bank.

`replace_search.py` re-ranks every candidate by how few OTHER words are unknown for Nate
(true i+1 for Nate, not generic frequency). A candidate identical to the card's current
sentence is dropped (no improvement).

## Steps

### Step 1 — find the candidates (read-only)

**Flag convention:** Nate marks struggling cards with **flag:1** (input queue). Replace
mode reads flag:1 and, after he confirms, **clears the flag** on each redone card so it
just rejoins the study queue.

```bash
python3 <skill-dir>/scripts/replace_search.py \
  --flag 1 \                                  # OR --note-ids a,b,c  OR --words 동기,서력
  --output ~/Downloads/sentence-mining/replace.json
```

- `--flag N` operates on every card flagged N in the mining decks (default input: flag:1).
- `--words` resolves each word to its existing card in the mining decks.
- Tier 3 (local bank) needs indexed banks (`config.banks.index_dir`).
- Filters applied per candidate: target word is present as a bare token (not glued to adjacent content), complete sentence, length 10–60 chars.
- Ranks by (fewest other-unknowns, closest to ~25 chars). Keeps runner-ups.
- Words with no usable hit go to `misses`. These are **retired** at apply time: tagged `not-worth-learning`, suspended, dropped off flag:1.

Read `replace.json`. **Curate (Step 1.5):** spot-check the top pick per word. Is the target in its normal sense? Complete sentence? If the top pick is weak, look at `runner_ups[]` and swap it in, or drop the entry entirely.

### Step 2 — write explanations (Claude, inline)

For each entry, write the `explanation` field using the **exact same prompt** as Step 4 of
the main flow (see [explanation-prompt.md](explanation-prompt.md)) over the entry's
`word` + `new_sentence`. Keep under ~250 Korean chars. Drop any entry you can't explain.

### Step 3 — review gate (ALWAYS confirm — every run)

Replace overwrites cards Nate has already studied, so it **never** auto-applies. **Every
run**, show the old→new table and wait for explicit OK before applying:

```bash
python3 <skill-dir>/scripts/replace_apply.py --draft ~/Downloads/sentence-mining/replace.json --dry-run
```

Present the table; let Nate drop/swap any. Only proceed to Step 4 once he confirms.

### Step 4 — apply in place

```bash
python3 <skill-dir>/scripts/replace_apply.py --draft ~/Downloads/sentence-mining/replace.json
```

Per card this:
- Copies local bank audio/image into Anki via `storeMediaFile`
- Gemini-TTS the new explanation. **Best-effort:** if the GEMINI key is missing/expired, the card still updates with the explanation TEXT; only `explanation_audio` is left empty.
- **Archives** the current sentence + its old audio/image refs into `previous_versions` (newest block first, dated) — so every prior version is recoverable.
- Overwrites `sentence`, `sentence_audio`, `picture`, `explanation`, `explanation_audio`
- Retags the i-level (`i1`/`i2`/…) to reflect the NEW sentence's complexity
- **Rehabilitates** the card: removes `leech` tag, unsuspends, `forgetCards` to reset scheduling → card becomes due
- **Clears the flag**: input `flag:1` → no flag (default `--done-flag 0`). Genuine misses stay on flag:1.

Also **retires the unfixable misses**: every word in `misses` is tagged `not-worth-learning`, suspended, and cleared off flag:1 (unless `--keep-misses` is passed).

Re-running the same draft is safe: an idempotency guard skips any card whose live sentence already equals the draft's new one.

### Rehabilitate a whole flagged batch (no field changes)

```bash
python3 <skill-dir>/scripts/replace_apply.py --rehab-flag 3
```

De-leeches + unsuspends + resets-to-due every card flagged N without changing any fields.

## The `previous_versions` field

Holds an append-only, newest-first stack of archived sentence blocks:

```html
<div class="sm-prev" data-archived="2026-06-16">그는 우산이 없어서 비를 피하고 있습니다 [sound:OLD.mp3]<img src="OLD.jpg"></div>
```

To revert a card, copy the archived sentence/audio/image back into the live fields by hand.
