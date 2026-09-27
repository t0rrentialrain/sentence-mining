# `.apkg` Schema

A `.apkg` is a ZIP archive containing an Anki collection database plus its media. Understanding its layout lets bank mode pull sentences, audio, and images out of decks Nate downloaded from subs2srs or other sources.

## ZIP layout

```
foo.apkg
├── collection.anki21      # SQLite — newer format (Anki ≥ 2.1.x)
│   ─ OR ─
├── collection.anki2       # SQLite — older format. Fall back to this if .anki21 is absent.
├── media                  # JSON map: numeric-string → original filename
└── 0, 1, 2, 3, …          # actual media files, renamed to numeric strings
```

The reason media files are renamed to integers: the ZIP format historically had Unicode-filename issues, so Anki dodges them by writing files as integers and keeping the original name → integer mapping in `media`.

To get a real media file (e.g. `tokyo_ghoul_01_0_0.06.33.620-0.06.36.980.mp3`):
1. Look up the original name in `media` (it's a JSON `{ "0": "tokyo_ghoul_…mp3", "1": "…", … }`)
2. Find the integer key whose value matches the desired filename
3. Read the file at that integer-named path in the ZIP

`extract_bank.py` reverses the map to `{filename: integer-key}` once per bank to make this fast.

## SQLite layout (`collection.anki2` / `.anki21`)

Relevant tables:

| table  | role                                                      |
|--------|-----------------------------------------------------------|
| `col`  | One row. `models` column is a JSON dict of all notetypes. |
| `notes`| One row per note. `mid` = model id, `flds` = field values |
| `cards`| One row per card (multiple cards per note possible)       |
| `media`| Sometimes present, redundant with the `media` file        |

`col.models` looks like:
```json
{
  "1352568357693": {
    "name": "subs2srs",
    "flds": [
      {"name": "SequenceMarker2", "ord": 0, ...},
      {"name": "SequenceMarker",  "ord": 1, ...},
      {"name": "Audio",           "ord": 2, ...},
      {"name": "Snapshot",        "ord": 3, ...},
      {"name": "Expression",      "ord": 4, ...},
      {"name": "English",         "ord": 5, ...},
      {"name": "Reading",         "ord": 6, ...},
      {"name": "Notes",           "ord": 7, ...}
    ]
  }
}
```

`notes.flds` stores all field values for a note concatenated with the **`\x1f` (unit separator)** character. So `flds.split("\x1f")` gives the ordered list of field values, in the same order as `flds` in the model.

The note's `mid` column points back to the model id, so you know which field names map to which positions.

## Media-reference patterns inside fields

Field values are mini-HTML. Common embeds:

| reference  | regex                       | role                |
|------------|-----------------------------|---------------------|
| `[sound:foo.mp3]` | `\[sound:([^\]]+)\]`     | audio (any extension: mp3, ogg, wav, m4a) |
| `<img src="foo.jpg">` | `<img[^>]+src="([^"]+)"` | image |

Audio and image files referenced this way live in the ZIP under their integer names.

## What `extract_bank.py` produces

For each `.apkg` it writes a `<bank-id>.notes.json` in `~/Downloads/sentence-mining/banks/index/` shaped like:

```json
{
  "bank_id": "goblin_s1",
  "source_path": ".../Goblin S1.apkg",
  "models": {
    "1352568357693": {
      "name": "subs2srs",
      "fields": ["SequenceMarker2", "SequenceMarker", "Audio", "Snapshot", "Expression", "English", "Reading", "Notes"],
      "roles": {"sentence": 4, "audio": 2, "image": 3, "meaning": 5, "reading": null, "target_word": null}
    }
  },
  "total_notes_in_db": 50,
  "notes": [
    {
      "bank_id": "goblin_s1",
      "note_id": 1474496405974,
      "model": "subs2srs",
      "sentence": "그와 동기인 두 사람은 모두 강하고...",
      "meaning": "goblin_s1_07_0",
      "target_word": "",
      "audio_files": ["goblin_s1_07_0_0.08.56.120-0.09.02.340.mp3"],
      "image_files": ["goblin_s1_07_0_0.08.59.230.jpg"]
    }
  ],
  "media_dir": ".../goblin_s1.media",
  "media_copied": 100
}
```

The companion `<bank-id>.media/` directory holds the extracted media with their original filenames (no integer renaming — easier for downstream use).
