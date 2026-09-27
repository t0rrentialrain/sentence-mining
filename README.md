# Sentence Mining

A [Claude Code](https://claude.com/claude-code) skill plus Python toolkit that turns
Korean video into study-ready Anki flashcards and keeps an existing deck in good shape.

## Modes

- **Video mode**: paste a YouTube, Instagram, TikTok, or Twitter URL, or a local file.
  The video is downloaded with `yt-dlp`, transcribed with the Soniox speech-to-text API,
  and tokenized with `kiwipiepy`. A built-in **i+1 diff** against your known words then
  picks the sentences that have exactly one new word.
- **Bank mode**: give it a list of target words. It searches your local subs2srs `.apkg`
  banks for natural example sentences and reuses their original audio and screenshots.
- **Replace mode**: finds a more comprehensible sentence for a card whose current one
  is bad, edits the card in place (keeping the old sentence in a history field), and
  resets it so you re-learn it.
- **Audit mode**: flags cards that aren't real words, cards with misattributed sentences
  (ASR errors or the wrong sense of a word), and duplicates. It outputs Anki `nid:`
  search queries so you do the cleanup yourself.

Every mode talks to Anki through the **AnkiConnect** API.

## Layout

| Path | Contents |
|---|---|
| `SKILL.md` | Skill entry point and workflow |
| `references/` | Per-mode instructions and data schemas |
| `scripts/` | Python implementation (transcribe, analyze, search, push, audit, …) |

## Setup

```bash
git clone https://github.com/t0rrentialrain/sentence-mining ~/.claude/skills/sentence-mining
```

1. Install [AnkiConnect](https://ankiweb.net/shared/info/2055492159) in Anki.
2. `pip install -r requirements.txt` (also needs `ffmpeg` on your PATH)
3. Copy `.env.example` to `.env` and add your API keys.
4. Run `/sentence-mining setup` in Claude Code, or `python scripts/setup.py`, to generate
   `config.json` with your note type and decks.

`.env` and `config.json` are gitignored.
