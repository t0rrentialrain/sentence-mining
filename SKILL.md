---
name: sentence-mining
description: Build and maintain Korean sentence-mining cards for Anki, fully self-contained (no AnkiMorphs install required) and configurable per user via a one-time `/sentence-mining setup`. (1) Video mode — paste any Instagram reel, YouTube video/Short, TikTok, Twitter video, or local file → yt-dlp + Soniox + kiwipiepy + a built-in i+1 known-word diff produces draft cards. (2) Bank mode — give a list of target words → search across your locally-indexed subs2srs .apkg banks for natural example sentences, reusing the bank's original audio + screenshot when available. (3) Replace mode — fix existing cards whose example sentence is bad (too short, a fragment, incomprehensible): pull a better, more comprehensible sentence (local bank, re-ranked by your own i+1), edit the card in place (archiving the old sentence to a previous_versions field), and rehabilitate the card (de-leech, unsuspend, reset-to-due) so you re-learn it fresh. (4) Audit mode — review an existing deck for quality problems: words that aren't real standalone words, cards whose sentence got misattributed (ASR mis-transcription, or a wrong sense/wrong lemma), and duplicate cards for the same word — outputs copy-pasteable Anki `nid:` search queries so you can delete/clean up yourself (Claude never deletes notes directly). All modes push/audit via AnkiConnect onto a note type and decks you choose at setup. Use proactively whenever input is (a) a Korean-language video URL, (b) a list of Korean words, (c) a request to improve/replace sentences on existing cards, or (d) a request to review/clean up/audit an existing deck. Trigger phrases include "mine this video", "make sentence cards from <url>", "turn this reel into cards", "mine these words", "find sentences for [w1, w2, …]", "i keep forgetting <word>", "pull cards from my <show> bank", "leech these", "search the banks for X", "replace the sentence for <word>", "find a better sentence for X", "fix my flag:1 cards", "these sentences are too short/confusing", "audit my deck", "check my cards", "clean up my deck", "are any of my cards not real words", "do I have duplicate cards", "set up sentence mining", `/sentence-mining`, `/sentence-mining setup`, or any video URL paired with a mention of Anki / cards / morphs / i+1.
---

# Sentence Mining

**Four modes** (plus a one-time `setup`). Two of them *create* new cards and share one
post-processing pipeline; the third *fixes existing* cards in place; the fourth *reviews*
an existing deck and hands back cleanup queries — it never creates or edits cards itself.

```
        ── CREATE NEW CARDS ──                      ── FIX EXISTING CARDS ──
┌────────────────────┐  ┌─────────────────────┐    ┌──────────────────────────┐
│  Video URL / file  │  │  List of words      │    │  flag:1 / "fix" / a word │
│  "mine this reel"  │  │  "card for 동기"    │    │  "better sentence for X" │
└─────────┬──────────┘  └──────────┬──────────┘    └────────────┬─────────────┘
          ▼                        ▼                             ▼
   ┌─────────────┐         ┌──────────────┐           ┌───────────────────────┐
   │ VIDEO MODE  │         │  BANK MODE   │           │     REPLACE MODE      │
   │ yt-dlp →    │         │ search local │           │ local bank, re-ranked │
   │ Soniox →    │         │ .apkg banks  │           │ by your i+1           │
   │ i+1 diff    │         │              │           │                       │
   └──────┬──────┘         └──────┬───────┘           └───────────┬───────────┘
          └───────────┬───────────┘                               ▼
                      ▼                              ┌───────────────────────────┐
       ┌─────────────────────────┐                  │  edit card IN PLACE:       │
       │  Shared post-process    │                  │  archive old → new + media │
       │  curate → explain →     │                  │  → rehab → reflag / retire │
       │  media → draft → push   │                  └───────────────────────────┘
       └─────────────────────────┘
```

Video and bank mode share the **Steps 1–8** below: the mode reference covers the
mode-specific Steps 1–3 (and the Step 5 specifics), then you come back here for the
shared post-processing. Replace mode runs its own pipeline end-to-end — see
[references/replace-mode.md](references/replace-mode.md).

**Route the request.** Look at what Nate gave you:

| Input                                                    | Mode    | Reference                          |
|----------------------------------------------------------|---------|------------------------------------|
| `setup` / "set up sentence mining" / no `config.json` yet  | setup   | [references/setup.md](references/setup.md)           |
| URL (instagram, youtube, tiktok, twitter) or local video | video   | [references/video-mode.md](references/video-mode.md) |
| Plain list of Korean words                               | bank    | [references/bank-mode.md](references/bank-mode.md)   |
| "replace"/"swap"/"better sentence for" existing cards, or "fix my flag:1 cards" | replace | [references/replace-mode.md](references/replace-mode.md) |
| "audit my deck", "check my cards", "are any of these not real words", "clean up my deck" | audit | [references/audit-mode.md](references/audit-mode.md) |
| Both (URL + words)                                       | ask Nate | —                                   |

**Replace vs bank/video:** if the words/cards already exist in Anki and the ask is
to swap in a *better* example sentence (the current one is too short, a fragment, or
incomprehensible) — that's **replace mode**, not bank mode. Bank/video mode *create*
new cards; replace mode *edits existing* ones in place and archives the old sentence.

**Audit vs replace:** if the sentence is fine but the *word itself* is the problem
(not a real standalone word, misattributed to a mis-transcription, wrong sense) or
Nate's asking about deck-wide quality/duplicates rather than one card's sentence —
that's **audit mode**. Replace mode fixes a card in place; audit mode decides what
should be *removed* and hands Nate a query to do it himself (see
[Never delete directly](references/audit-mode.md#never-delete-directly) — this
skill does not call `deleteNotes`).

**Before anything else, ensure Anki is up** (skip for setup mode, which has no collection to hit yet). The create/fix modes all need AnkiConnect, so run `bash <skill-dir>/scripts/ensure_anki.sh` first — it launches Anki for you if it's closed and waits for it to load, instead of dying mid-pipeline on "Connection refused". Only stop and tell the user if it exits non-zero.

**Then check setup.** If `<skill-dir>/config.json` does not exist, the skill is unconfigured — route to **setup mode** ([references/setup.md](references/setup.md)) first, then continue with the user's actual request.

## Inputs and required env

The skill is designed to be **shareable**: nothing about a specific person's Anki
is hardcoded. Two git-ignored files hold all the per-user state:

- **`<skill-dir>/config.json`** — note type, field mapping, deck names, known-word
  sources, sentence-bank locations, Anki media directory. Written by `/sentence-mining setup`. See
  `config.example.json` for the shape. Read by every script through `_config.py`.
- **`<skill-dir>/.env`** — API keys only:

  ```bash
  cp <skill-dir>/.env.example <skill-dir>/.env   # then paste the keys
  ```

  Required keys: `SONIOX_API_KEY` (video mode only) and `GEMINI_API_KEY`
  (both modes — explanation TTS + sentence TTS fallback). Real env vars override
  `.env`. If a key is missing, the script exits pointing at `.env` — don't fall
  back to alternatives without asking.

**The only hard dependencies besides those two files:**
- Anki running with **AnkiConnect** (default port 8765) — **don't verify by hand; run `bash <skill-dir>/scripts/ensure_anki.sh` once at the start of any mode.**
- `yt-dlp`, `ffmpeg` on PATH
- Python: `pip install kiwipiepy google-genai`

**AnkiMorphs is NOT required.** The i+1 known-word diff is re-implemented inside
the skill using kiwipiepy. See [references/known-words.md](references/known-words.md).

## Steps 1–3 (mode-specific)

Follow the reference for the mode you routed into. By the end of those steps you have a `candidates.json` (or `banksearch.json`) shaped as:

```jsonc
{
  "source": "video" | "bank-search",
  "source_id": "...",
  "source_url": "...",     // optional, video only
  "candidates": [
    { "lemma": "...", "sentence": "...", "deck": "<config.decks.main>", "i_level": "i1" | "i?", ... },
    ...
  ]
}
```

Read it. Zero candidates? Tell Nate why (all words known, all dupes, no hits across banks, etc.) and stop.

## Step 3.5 — Curate (shared, inline)

Walk the candidate list and drop entries that aren't worth a card. **Filter aggressively** — a Nate-quality card teaches a generalizable word he'll hit again, not a one-off label from this specific source. Drop:

- **Pop-culture proper nouns** — drama/variety/webtoon titles, character names, idol/actor names that are just names. Real-world brands or places (`스타벅스`, `서울`, `한강`) are fine; pop-culture-specific names are not.
- **Tokenizer fragments** — lemmas that are clearly partial morphemes or particle attachments that kiwipiepy mislabeled as content words. Spot-check: if the form looks like a suffix glued to a stem or a lone grammatical morpheme, drop it. **Exception: candidates with `"pos": "GRAM"` are intentional grammar-pattern matches** from `scripts/grammar.py` (e.g. `-는데`, `-고 있다`), not tokenizer mistakes — never drop a GRAM candidate under this rule. Still apply the other curation rules to them normally (trail-off sentences, wrong sense, subs2srs concatenation, etc.).
- **Transcription garbage** — nonsense given the sentence's clear topic. Don't try to rescue a contaminated sentence; drop the candidate. (Video mode only.)
- **Trail-off / partial sentences** — sentence ends mid-clause with a dangling connective ending (-고, -서, -는데 hanging with no main clause), or starts with a connecting element; the audio clip will sound broken. Drop it or pick a complete runner-up, even if the fragment has zero other unknowns.
- **Wrong sense for the card** — Korean words often have multiple senses. If no candidate uses the target word in the expected sense, flag it rather than teaching the wrong meaning.
- **Prefer the canonical collocation** — between equally-clean candidates, pick the one showing the word's most natural pairing over an incidental usage.
- **Subs2srs concatenated frames** — bank-mode sentences like `(line1)   (line2)`: keep only the chunk containing the target word.

When dropping is judgment-call, lean toward dropping. **Nate would rather mine 3 great cards than 15 mediocre ones.**

Apply by deleting entries from `data["candidates"]` and saving back. Print a short "kept N / dropped M because …" summary.

## Step 4 — Generate explanations (shared, inline)

For **each** candidate, generate the Korean explanation inline — don't shell out. Use this prompt verbatim, swapping `{word}` and `{sentence}`:

```
Please write a short explanation of the word '{word}' using the context of the original sentence: '{sentence}'.

Write an explanation that helps a Korean beginner understand the word and how it is used with this context as an example.

Explain it in the same way a native would explain it to a 13-year-old. Don't use any English, only use simpler Korean.

1. Don't write pronunciation guides in brackets after the word.
2. Don't start with stuff like 이 단어를 간단히 설명할게요, just dive straight into explaining after starting with the word.
```

Write each explanation into the candidate's `explanation` field. Keep each under ~250 Korean characters — it gets read aloud by TTS.

**Grammar candidates (`"pos": "GRAM"`) use a variant prompt** — same shape, but framed around explaining a grammar pattern's meaning/usage rather than a vocabulary word. See [references/explanation-prompt.md](references/explanation-prompt.md) for both prompts (canonical word version + grammar variant) and worked examples.

## Step 5 — Generate media (mode-specific)

| Mode  | Script                          | What it does                                                              |
|-------|---------------------------------|---------------------------------------------------------------------------|
| video | `scripts/generate_media.py`     | ffmpeg clip + screenshot from the video, Gemini TTS on explanation        |
| bank  | `scripts/generate_media_bank.py`| copy bank's audio/image (or Gemini TTS sentence if absent), TTS explanation |

Media lands in `config.anki_media_dir` (set at setup — on Windows typically `C:\Users\<user>\AppData\Roaming\Anki2\User 1\collection.media\`).

Cards run through a pool of 3 concurrent workers (TTS capped at 3 with 429 backoff). **Video mode pushes inline by default:** pass `--no-push` to only stage the draft.

## Step 6 — Push & summarize

**Default behavior: push immediately after Step 5 succeeds.** No approval gate. Skip auto-push only if Nate explicitly said "draft only" / "don't push" / "let me review first".

Summary format (video mode):

```
Pushed 17 cards from <SOURCE_ID> to Anki ✓

  → "Nate's Sentence Cards" (i+1): 12
    1. 기박 — "그는 기박이 넘치는 눈빛으로..." [KoFREN rank 4823]
    ...

  → "Deferred" (i+2/i+3): 5
    ...

Skipped during curation: <N>
Draft: ~/Downloads/sentence-mining/<source>.draft.json
```

### Legacy approval flow (only when Nate says "draft only" / "don't push")

Print the summary but wait for explicit OK. Nate may drop entries, swap sentences, or say no.

## Step 7 — What `push.py` does (tags, formatting, dedup)

```bash
python3 <skill-dir>/scripts/push.py --draft ~/Downloads/sentence-mining/<source>.draft.json
```

- Calls AnkiConnect `addNotes`
- Tags every card: `claude-sentence-mining` (video) or `claude-sentence-bank` (bank), plus `i1`/`i2`/`i3`/`i?`
- **Empty picture → `。` filler, never blank** (prevents back-template double-audio replay)
- Nothing is suspended

## Step 8 — Cleanup

Leave all working files in `~/Downloads/sentence-mining/`. Don't auto-delete.

## Reference files

- [references/setup.md](references/setup.md)
- [references/known-words.md](references/known-words.md)
- [references/video-mode.md](references/video-mode.md)
- [references/bank-mode.md](references/bank-mode.md)
- [references/replace-mode.md](references/replace-mode.md)
- [references/audit-mode.md](references/audit-mode.md)
- [references/apkg-schema.md](references/apkg-schema.md)
- [references/bank-formats.md](references/bank-formats.md)
- [references/note-type.md](references/note-type.md)
- [references/transcript-schema.md](references/transcript-schema.md)
- [references/explanation-prompt.md](references/explanation-prompt.md)

## Scripts inventory

| script                  | mode  | purpose |
|-------------------------|-------|---------|
| `setup.py`              | setup | probe Anki, tools, keys; write `config.json` |
| `_config.py`            | all   | load config — single source of truth |
| `transcribe.py`         | video | Soniox Korean transcription with diarization |
| `analyze.py`            | video | kiwipiepy tokenize + known-word diff (cached) + KoFREN frequency rank |
| `grammar.py`            | both  | grammar-pattern matcher (`find_grammar_matches`) — grammar patterns are mined as "words" too, see [references/known-words.md](references/known-words.md) |
| `generate_media.py`     | video | ffmpeg clip + screenshot + Gemini TTS; inline push |
| `extract_bank.py`       | bank  | parse `.apkg` → local index JSON + media dir |
| `search_banks.py`       | bank  | word-list → top-N candidates across indexed banks |
| `generate_media_bank.py`| bank  | copy bank media (or TTS fallback) + TTS explanation |
| `replace_search.py`     | replace | resolve cards → search local bank → filter + re-rank → replace-draft JSON |
| `replace_apply.py`      | replace | stage media + TTS, archive old sentence, overwrite fields, retag, rehabilitate |
| `audit_pull.py`         | audit | fetch a deck's notes (word/sentence/noteId), strip HTML — review itself is inline |
| `push.py`               | both  | AnkiConnect addNotes via `config.field_map` |
| `ensure_anki.sh`        | all   | ping AnkiConnect; launch Anki if down; verify stable |
| `_env.py`               | both  | loads `.env` into `os.environ` |
| `_anki.py`              | both  | AnkiConnect helper + `storeMediaFile` |

## Gotchas (universal)

- **AnkiConnect must be running** — run `bash <skill-dir>/scripts/ensure_anki.sh`; don't make Nate launch it manually.
- **Don't push cards with empty explanation.** Drop them rather than pushing hollow cards.
- **Gemini TTS free tier is 10 RPM.** Scripts back off exponentially on 429.
- **`allowDuplicate: False`** — re-pushing the same word is silently rejected. `analyze.py` pre-dedupes against configured mining decks.
- **Known-word scan is cached** for `config.known_words.cache_hours` (default 6). After a big review session, pass `analyze.py --refresh-known`.
- **Never leave the picture field blank — write `。`.**
