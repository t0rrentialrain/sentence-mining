# Transcript JSON schema

The file goes through two stages:

## Stage A: raw output from `transcribe.py`

```json
{
  "transcript_id": "abc123...",
  "language": "ko",
  "audio_duration_ms": 17400,
  "full_text": "요즘 기운이 없어서 집에만 있어. 그래서 더 힘든 것 같아...",
  "words": [
    {"text": "요즘", "start_ms": 1234, "end_ms": 1600, "speaker": "A"},
    {"text": "기운이", "start_ms": 1600, "end_ms": 2100, "speaker": "A"},
    {"text": "없어서", "start_ms": 2100, "end_ms": 2600, "speaker": "A"},
    ...
  ]
}
```

Flat word stream with timings + per-word `speaker` label from Soniox's diarization.
We don't pass `speakers_expected` — auto-detection handles 1, 2, or N speakers.
Soniox's sentence breaks are unreliable for casual Korean speech, so we don't ship them.

**Note on Soniox word boundaries:** Soniox tokenizes at the eojeol level (space-delimited
chunks), which for Korean means words come with their particles attached
(e.g. `기운이` = `기운` + `이`). The `words` array reflects these eojeols, not morphemes.
kiwipiepy in `analyze.py` does the morphological split.

## Stage B: after Claude's correction + splitting (Step 2.5 of SKILL.md)

The same file gains a `sentences` array:

```json
{
  ...same fields as Stage A...,
  "sentences": [
    {
      "text": "요즘 기운이 없어서 집에만 있어.",
      "start_ms": 1234,
      "end_ms": 4567,
      "speaker": "A",
      "words": [
        {"text": "요즘", "start_ms": 1234, "end_ms": 1600, "speaker": "A"},
        ...
      ]
    },
    ...
  ]
}
```

The `text` field reflects any corrections Claude made. The `words` array preserves
Soniox's original timing so ffmpeg can still slice the right audio segment.

`analyze.py` reads from `sentences`, so step 2.5 must complete before it.

## Quality notes

- Soniox's word-level timing is per its eojeol tokenization, not per kiwipiepy morpheme.
  The mapping back to kiwipiepy tokens in `analyze.py` is heuristic (substring match) —
  good enough to pick a screenshot frame.
- Music-heavy videos can produce 10–30% transcription errors. Claude's correction step
  catches obvious phonetic confusions but won't fix systemic garbage — for those, surface
  the issue to Nate rather than churning out bad cards.
