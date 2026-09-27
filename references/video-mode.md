# Video Mode

Source: a **video URL** (Instagram reel, YouTube video/Short, TikTok, Twitter/X video) or a **local video file path**. The flow downloads (if URL), transcribes via Soniox, splits into sentence chunks, runs kiwipiepy + the built-in known-word diff (see [known-words.md](known-words.md)) to find i+1 sentences (one unknown word), and produces draft cards.

When this mode triggers:
- Nate pastes any of the URL types above
- Nate says "mine this video", "make sentence cards from <url>", "turn this reel into cards"
- Nate writes `/sentence-mining` with a URL

If Nate gives a list of words instead → that's [bank-mode](bank-mode.md).

## Step 1: Download

```bash
mkdir -p ~/Downloads/sentence-mining
cd ~/Downloads/sentence-mining
yt-dlp -o "%(extractor)s-%(id)s.%(ext)s" --no-playlist <URL>
```

For local files, skip — note the path.

Record `VIDEO_PATH` and `SOURCE_ID` (e.g. `youtube-dQw4w9WgXcQ`) — everything downstream depends on these.

## Step 2: Transcribe (raw)

```bash
python3 <skill-dir>/scripts/transcribe.py "$VIDEO_PATH" \
    > ~/Downloads/sentence-mining/$SOURCE_ID.transcript.json
```

Output contains `full_text` and a flat `words` array. Each word has `start_ms`, `end_ms`, and a `speaker` label from Soniox's diarization pass. **Sentence splitting is intentionally NOT done here** — see Step 2.5.

**Why no automatic splitter:** Soniox's sentence segmenter can be unreliable for casual/fast Korean speech. Claude does this with full context instead.

**Why diarization:** speaker turn changes are the strongest "natural meaning boundary" signal — much better than punctuation alone for casual conversation.

## Step 2.5: Correct + split (inline — no script)

Read the raw transcript. Produce a `sentences` array where each sentence is a card-worthy chunk: roughly **3–12 seconds** of audio, **10–50 Korean characters**, ending at a natural meaning boundary.

**Speaker turn = strongest natural boundary.** Every time the speaker changes, close the current chunk. Within a single speaker's turn you can let a chunk run a bit longer (up to ~12s / 50 chars) if it adds useful context. Inside a long turn, prefer secondary boundaries: sentence-final ending (습니다, 어요, 다, 야), clause break, or hard punctuation.

A chunk's `speaker` field is the speaker of its words. If a chunk straddles speakers (usually a backchannel like "응", "맞아"), pick the dominant speaker.

**Correction pass first.** Walk through `full_text`. For each suspicious sequence — anything that doesn't read as natural Korean — figure out what was likely actually said given context and substitute:
- Phonetically similar mistakes (e.g. wrong homophone chosen when topic is clear)
- Numbers or proper nouns transcribed incorrectly
- Satoori or speech-filled passages where the ASR guessed wrong

Don't over-correct — ambiguous → leave it. The goal is fixing obvious errors, not rewriting the speaker.

**Then split.** Use the corrected text + word-level timings + speaker labels. Each chunk needs:
- `text` — corrected sentence (one self-contained thought)
- `start_ms`, `end_ms` — from first/last word
- `speaker` — dominant speaker label
- `words` — slice of input `words` (preserves original timings; ffmpeg slices off these)

When you correct text, the `text` field reflects the correction but the `words` array preserves originals.

Write back to the same transcript file (set the `sentences` key).

Before moving on, print a short summary (duration + char count + first 30 chars per chunk) so Nate can spot obvious splitting mistakes.

## Step 3: Analyze — tokenize, diff, dedupe, rank

Single video:

```bash
python3 <skill-dir>/scripts/analyze.py \
    --transcript ~/Downloads/sentence-mining/$SOURCE_ID.transcript.json \
    --source-id "$SOURCE_ID" \
    --source-url "$URL" \
    > ~/Downloads/sentence-mining/$SOURCE_ID.candidates.json
```

**Batch mode (preferred when mining ≥2 videos)** — share one kiwipiepy + AnkiConnect load. Write `manifest.json`:

```json
[
  {"transcript": "~/Downloads/sentence-mining/youtube-AAA.transcript.json", "source_id": "youtube-AAA", "source_url": "..."},
  {"transcript": "~/Downloads/sentence-mining/youtube-BBB.transcript.json", "source_id": "youtube-BBB", "source_url": "..."}
]
```

```bash
python3 <skill-dir>/scripts/analyze.py \
    --manifest ~/Downloads/sentence-mining/manifest.json \
    --output-dir ~/Downloads/sentence-mining/
```

**What analyze.py does:**
- Loads the **known-word set** once from `config.known_words.sources` (cached; see [known-words.md](known-words.md))
- Tokenizes each sentence with kiwipiepy
- For each unknown lemma, finds the **best sentence** (i+1 preferred; falls back i+2, i+3…)
- Queries AnkiConnect for the existing target-word field across the configured mining decks; drops dupes
- Ranks remaining unknowns by KoFREN frequency if `config.frequency_csv` is set (lower line = more frequent = higher priority); otherwise keeps source order
- Caps output at 50 candidates

Output: candidate cards with `lemma`, `surface`, `pos`, `sentence`, `sentence_start_ms`, `sentence_end_ms`, `target_word_start_ms`, `unknown_count_in_sentence`, `frequency_rank`, `deck`, `i_level` (`i1`, `i2`, `i3`, …), and `speaker`.

**Deck routing:**
- **i+1 cards** (one unknown) → `decks.main` (enter normal daily review)
- **i+2 and higher** → `decks.deferred`. Falls back to `decks.main` if no deferred deck is configured.

Read the output. If zero candidates, tell Nate why (all words known? all dupes? no Korean detected?) and stop.

## Step 5 (video-specific): Generate media

```bash
python3 <skill-dir>/scripts/generate_media.py \
    --video "$VIDEO_PATH" \
    --candidates <candidates-with-explanations.json> \
    --source-id "$SOURCE_ID" \
    > ~/Downloads/sentence-mining/$SOURCE_ID.draft.json
```

Per candidate:
- Clips sentence audio with ffmpeg → `<anki-media>/sm_<source-id>_<idx>.mp3`
- Grabs middle frame as JPEG (640px wide max) → `<anki-media>/sm_<source-id>_<idx>.jpg`
- Calls Gemini TTS on the explanation → `<anki-media>/sm_explain_<source-id>_<idx>.mp3`

Anki media folder: `config.anki_media_dir` (on Windows: `C:\Users\<user>\AppData\Roaming\Anki2\User 1\collection.media\`)

**Concurrency + inline push (default).** Cards run through a pool of 3 workers (TTS capped at 3 with 429 backoff). By default each card is inserted into Anki the instant its own media is ready, so cards stream in one by one while the next batch is still generating. Pass `--no-push` to only stage the draft and push later with `push.py`.

## Gotchas specific to video mode

- **Soniox Korean quality varies.** Music-heavy reels with overlaid voice may give garbage. If a transcript has obvious errors (broken sentences, nonsense output), surface this to Nate before generating cards — bad sentences mean bad cards.
- **Segmentation isn't perfect.** Korean agglutination means kiwipiepy sometimes over-splits or under-splits. Because the known-set and the candidates use the *same* tokenizer, segmentation is at least self-consistent — but still spot-check the target-word field for weirdness in the draft summary.
- **Diarization is not always clean for backchannels.** "응", "맞아" interjections may flip speaker labels mid-sentence. Use judgment in Step 2.5 to consolidate.
