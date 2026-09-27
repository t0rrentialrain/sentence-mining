#!/usr/bin/env python3
"""Bank-mode media generation.

For each candidate already paired with a bank source (has `existing_audio` /
`existing_image` paths from search_banks.py):
  - Registers the bank's audio file in Anki's media (renamed to a stable
    `sm_bank_<bank-id>_<noteid>.<ext>` filename).
  - Registers the bank's image similarly. Missing image is fine — we leave
    `picture_file` empty.
  - If the bank has NO audio, synthesizes sentence audio with Gemini TTS.
  - Always runs Gemini TTS on the candidate's `explanation` field for the
    explanation audio.

All writes go through AnkiConnect's `storeMediaFile` rather than directly into
`collection.media`. See _anki.py for why.

Output: a draft.json shaped exactly like generate_media.py's output, so push.py
works unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_skill_env  # noqa: E402
from _anki import store_media  # noqa: E402

GEMINI_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_VOICE = "Puck"
TTS_CONCURRENCY = int(os.environ.get("SM_TTS_CONCURRENCY", "2"))


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", s).strip("_") or "x"


async def gemini_tts(text: str, out_path: Path, semaphore: asyncio.Semaphore) -> None:
    """Render `text` as Japanese speech and write mp3 to `out_path`."""
    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors

    client = genai.Client()
    async with semaphore:
        for attempt in range(5):
            try:
                resp = await client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=text,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=GEMINI_VOICE
                                )
                            )
                        ),
                    ),
                )
                # Empty/blocked responses come back with no candidate audio — treat
                # as retryable rather than crashing the whole asyncio.gather.
                cand = (resp.candidates or [None])[0]
                parts = getattr(getattr(cand, "content", None), "parts", None)
                if not parts or getattr(getattr(parts[0], "inline_data", None), "data", None) is None:
                    if attempt < 4:
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    raise RuntimeError("Gemini TTS returned no audio after retries")
                break
            except genai_errors.ClientError as e:
                if getattr(e, "code", None) == 429 and attempt < 4:
                    await asyncio.sleep(10 * (attempt + 1))
                    continue
                raise
    pcm = resp.candidates[0].content.parts[0].inline_data.data
    wav_path = out_path.with_suffix(out_path.suffix + ".tmp.wav")
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
         "-codec:a", "libmp3lame", "-q:a", "4", str(out_path)],
        check=True,
    )
    wav_path.unlink()


async def process_one(idx: int, candidate: dict, workdir: Path, sem: asyncio.Semaphore) -> dict:
    bank = _slug(candidate.get("bank_id", "bank"))
    note_id = candidate.get("note_id", idx)
    base = f"sm_bank_{bank}_{note_id}"

    # Sentence audio: register from bank if present, else synthesize then register.
    sent_audio_field = ""
    src_audio = candidate.get("existing_audio", "")
    if src_audio and Path(src_audio).exists():
        ext = Path(src_audio).suffix.lower() or ".mp3"
        target = f"{base}_sentence{ext}"
        store_media(src_audio, target)
        sent_audio_field = target
    else:
        target = f"{base}_sentence_tts.mp3"
        local = workdir / target
        try:
            await gemini_tts(candidate["sentence"], local, sem)
            store_media(local, target)
            sent_audio_field = target
        except Exception as e:
            print(f"  sentence TTS failed for {candidate.get('lemma')!r}: {e}", file=sys.stderr)

    # Image: register from bank if present.
    pic_field = ""
    src_img = candidate.get("existing_image", "")
    if src_img and Path(src_img).exists():
        ext = Path(src_img).suffix.lower() or ".jpg"
        target_img = f"{base}_image{ext}"
        store_media(src_img, target_img)
        pic_field = target_img

    # Explanation TTS — always.
    exp_target = f"sm_explain_bank_{bank}_{note_id}.mp3"
    exp_local = workdir / exp_target
    try:
        await gemini_tts(candidate["explanation"], exp_local, sem)
        store_media(exp_local, exp_target)
    except Exception as e:
        print(f"  explanation TTS failed for {candidate.get('lemma')!r}: {e}", file=sys.stderr)
        exp_target = ""

    candidate["sentenceAudio_file"] = sent_audio_field
    candidate["picture_file"] = pic_field
    candidate["explanationAudio_file"] = exp_target
    return candidate


async def main_async(args):
    load_skill_env()
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY not set — add to <skill-dir>/.env or export.")

    data = json.loads(Path(args.candidates).expanduser().read_text())

    keep = [c for c in data["candidates"] if c.get("explanation", "").strip()]
    skipped = len(data["candidates"]) - len(keep)
    if skipped:
        print(f"Skipping {skipped} candidate(s) with empty explanation", file=sys.stderr)

    sem = asyncio.Semaphore(TTS_CONCURRENCY)
    with tempfile.TemporaryDirectory(prefix="sm_bank_media_") as tmp:
        workdir = Path(tmp)
        tasks = [process_one(i, c, workdir, sem) for i, c in enumerate(keep)]
        processed = await asyncio.gather(*tasks)

    data["candidates"] = processed
    out = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).expanduser().write_text(out)
        print(f"Wrote {len(processed)} draft cards to {args.output}", file=sys.stderr)
    else:
        print(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, help="Candidates JSON with explanation field filled.")
    ap.add_argument("--output", default=None, help="Where to write draft.json (default stdout).")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
