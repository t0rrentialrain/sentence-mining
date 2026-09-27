# Bank Formats

Each `.apkg` ships its own notetype with arbitrary field names. `extract_bank.py` detects field **roles** (sentence, audio, image, meaning, target_word) per notetype via heuristics over a sample of notes.

## Field-role detection (what `detect_field_roles` actually does)

For each field of each notetype, we compute:
- `has_sound` — any value contains `[sound:…]`
- `has_img` — any value contains `<img src="…">`
- `avg_kr` — average count of Korean characters (Hangul syllable blocks) per value
- `avg_len` — average string length (after HTML strip)
- `mostly_ascii` — non-empty values are ≥80% ASCII

Then we assign roles in this order:

1. **audio** → first field with `has_sound`
2. **image** → first field with `has_img`
3. **sentence** → preferred candidate: a field whose *name* contains one of `expression`, `sentence`, `text`, `front`, `korean`, `context`, AND has Korean content, AND is not a `notes`/`definition`/`extra` field. Among matches, prefer the one with the most Hangul. If no named candidate, fall back to any Korean-content field that isn't audio/image.
4. **meaning** → first `mostly_ascii` field (non-empty) that isn't audio/image/sentence (usually the English translation).
5. **target_word** → field whose name contains `vocab`, `word`, `target`, or `morph`, AND has Korean content with short avg length.

### Why we demote `Notes` fields

Many subs2srs notetypes have a `Notes` field with dictionary entries — long text not suitable for a sentence card. Anything named `notes`, `definition`, `dict`, `extra`, or `comment` is excluded from the sentence-candidate pool.

## Known notetypes in Nate's library

These are the notetypes seen so far. New banks may introduce new ones — re-run `extract_bank.py` to log them and update this list.

### `subs2srs` (standard format)
```
Common fields: [SequenceMarker, Audio, Snapshot, Expression, English]
Roles: sentence=Expression, audio=Audio, image=Snapshot, meaning=English
```

### `Korean (recognition)`
```
Common fields: [Vocab, Expression, Meaning, Image, Audio]
Roles: sentence=Expression, meaning=Meaning, audio=Audio, image=Image
```

### `Basic`
Standard Anki notetype. Fields: `[Front, Back]`. Hard to use for bank mining — skip unless overridden.

## Overriding role detection

When the heuristic guesses wrong, edit `<bank-id>.notes.json` directly — the `models.<mid>.roles` block. Then re-run `search_banks.py`; it reads roles from the JSON, not from the heuristic.

## Edge cases

**Empty sentence-side fields.** Some notes have `Expression` blank. `extract_bank.py` skips notes where both `sentence` AND `target_word` are empty.

**Subtitle-line concatenation.** subs2srs sometimes joins two adjacent subtitle lines into one note: `(line1)   (line2)`. The current extractor passes this through verbatim. When building a card from such a note, drop the parenthesized chunk that doesn't contain the target word.

**Multiple media references per field.** Some banks have `[sound:foo.mp3][sound:bar.mp3]` in one field. The extractor captures all of them in `audio_files` but downstream picks the first one.

**HTML bolding.** Many vocab-side banks bold the target word: `오늘은 <b>동기</b>와 밥을 먹었어`. The HTML strip in `extract_bank.py` removes the tags but keeps the content.
