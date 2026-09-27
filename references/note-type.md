# Note type & field mapping

The skill writes onto whatever note type the user picks at setup (`config.note_type`).
`config.field_map` maps the skill's internal field **roles** onto that note type's actual
fields. Only roles the user mapped (non-empty) are written.

## Internal roles

| Role (config key) | Required | What this skill puts in it |
|---|---|---|
| `word` | **yes** | Dictionary form (lemma) of the unknown word, e.g. `기운` |
| `sentence` | **yes** | The source sentence containing the word |
| `sentence_audio` | no | `[sound:…mp3]` — ffmpeg clip (video) or bank audio / Gemini TTS (bank) |
| `picture` | no | `<img src="…jpg">` — middle frame (video) or bank screenshot (bank) |
| `explanation` | no | Claude-generated short Korean explanation |
| `explanation_audio` | no | `[sound:…mp3]` — Gemini TTS of the explanation |
| `source_url` | no | The original video URL (video mode) |
| `previous_versions` | no | Append-only archive of replaced sentences (replace mode) |

Any other fields the note type has (definition, pitch accent, frequency, etc.) are
left untouched for the user to fill at review time.

## Grammar-pattern cards

Grammar patterns (`-는데`, `-고 있다`, `-(으)ㄹ 수 있다`, ...) are mined and pushed
through this exact same note type, decks, and field mapping — no separate note type
or deck. "Grammar is just words." The `word` field may hold **either** a vocabulary
lemma (`기운`, `먹다`) **or** a canonical grammar-pattern string (`-는데`).

**Telling them apart:** grammar-pattern ids always start with a leading dash (`-`)
— see `scripts/grammar_patterns.json` — while a vocabulary lemma never does. That's
the only in-Anki signal; the draft/candidate JSON carries `"pos": "GRAM"` before
push for programmatic use (curation, explanation-prompt selection), but this field
doesn't ride along into Anki itself, same as `speaker`/`bank_id`/etc.

## Nate's mapping (for reference)

Nate's note type is `Nate's Sentence Mining`. His `config.field_map`:
`word→wordForm`, `sentence→sentence`, `sentence_audio→sentenceAudio`,
`picture→picture`, `explanation→explanation`, `explanation_audio→explanationAudio`,
`source_url→source_url`, `previous_versions→previous_versions`.

## Tags

Every card gets exactly two tags (set by `push.py`):
- the permanent kind tag — `claude-sentence-mining` (video) or `claude-sentence-bank` (bank)
- the i-level — `i1` / `i2` / `i3` / `i?`

Nothing is suspended. Deferred (i+2/i+3) video cards go to `config.decks.deferred`
and stay unsuspended.
