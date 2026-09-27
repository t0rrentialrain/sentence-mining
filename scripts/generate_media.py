#!/usr/bin/env python3
"""For each candidate: clip sentence audio, grab middle frame, generate explanation TTS.

Input: a candidates JSON file where each candidate already has an `explanation` field
       filled in by Claude.
Output: a draft.json with `sentenceAudio`, `picture`, and `explanationAudio` filenames
        added to each candidate.

Cards are processed concurrently (a pool of TTS_CONCURRENCY workers); with --push,
each card is inserted into Anki the moment its own media finishes, so cards stream
in one by one as they complete rather than all at the end. Without --push, the
script just emits the draft for a separate push.py run (the legacy two-step flow).

All media is written to a tempdir first and then registered through AnkiConnect's
`storeMediaFile`. See _anki.py for why we don't write directly into collection.media.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_skill_env
from _anki import store_media
import push as push_mod
from _config import load_config

GEMINI_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_VOICE = "Puck"
# Gemini TTS has a 10 RPM cap on the free tier. 3 concurrent workers each make one
# TTS call per card; spaced across ffmpeg/screenshot work that stays well under the
# cap while keeping three audio generations in flight at once.
TTS_CONCURRENCY = 3


def clip_audio(video_path, start_ms, end_ms, out_path):
    """Extract sentence audio as mp3 with a small lead-in/out pad."""
    pad = 150
    start_s = max(0, (start_ms - pad)) / 1000
    duration_s = (end_ms - start_ms + 2 * pad) / 1000
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{start_s:.3f}",
            "-i", video_path,
            "-t", f"{duration_s:.3f}",
            "-vn", "-acodec", "libmp3lame", "-q:a", "4",
            out_path,
        ],
        check=True,
    )


def grab_screenshot(video_path, start_ms, end_ms, out_path):
    middle_s = ((start_ms + end_ms) / 2) / 1000
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{middle_s:.3f}",
            "-i", video_path,
            "-vframes", "1",
            "-vf", "scale='min(640,iw)':-2",
            "-q:v", "3",
            out_path,
        ],
        check=True,
    )


async def gemini_tts(text, out_path, semaphore):
    """Generate Japanese explanation audio with Gemini 3.1 Flash TTS. Writes mp3."""
    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors

    client = genai.Client()
    async with semaphore:
        # Retry up to 5 times on 429 (free tier is 10 RPM, easy to bump into).
        for attempt in range(5):
            try:
                resp = await client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=text,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=GEMINI_VOICE)
                            )
                        ),
                    ),
                )
                break
            except genai_errors.ClientError as e:
                if getattr(e, "code", None) == 429 and attempt < 4:
                    # Use server-suggested retry delay if available, else exp backoff.
                    delay = 10 * (attempt + 1)
                    await asyncio.sleep(delay)
                    continue
                raise
    pcm = resp.candidates[0].content.parts[0].inline_data.data

    # Gemini returns raw 24kHz s16le mono PCM. Wrap as WAV, then transcode to mp3
    # so Anki media size stays small.
    wav_path = out_path + ".tmp.wav"
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
         "-codec:a", "libmp3lame", "-q:a", "4", out_path],
        check=True,
    )
    os.remove(wav_path)


def _push_one(candidate, source_url, source_id):
    """Insert a single finished card via AnkiConnect. Returns (ok, detail).
    Reuses push.py's note builder + config so tagging/field-mapping stay identical
    to the batch path. A duplicate (allowDuplicate=False) surfaces as ok=False."""
    note = push_mod.build_note(candidate, source_url, push_mod.PERMANENT_TAG_VIDEO, source_id)
    try:
        note_id = push_mod.anki_request("addNote", note=note)
    except RuntimeError as e:
        return False, str(e)
    return (note_id is not None), ("duplicate" if note_id is None else note_id)


async def process_one(video_path, source_id, idx, candidate, workdir, semaphore,
                      push=False, source_url="", push_lock=None, report=None,
                      want_explanation=True):
    sent_audio = f"sm_{source_id}_{idx:03d}.mp3"
    screenshot = f"sm_{source_id}_{idx:03d}.jpg"

    sent_local = os.path.join(workdir, sent_audio)
    shot_local = os.path.join(workdir, screenshot)

    # ffmpeg work is CPU/IO bound; run in default executor.
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, clip_audio, video_path,
                             candidate["sentence_start_ms"], candidate["sentence_end_ms"], sent_local),
        loop.run_in_executor(None, grab_screenshot, video_path,
                             candidate["sentence_start_ms"], candidate["sentence_end_ms"], shot_local),
    ]
    explain_audio = None
    exp_local = None
    if want_explanation:
        explain_audio = f"sm_explain_{source_id}_{idx:03d}.mp3"
        exp_local = os.path.join(workdir, explain_audio)
        tasks.append(gemini_tts(candidate["explanation"], exp_local, semaphore))
    await asyncio.gather(*tasks)

    # Register through AnkiConnect so add-on hooks see them as real adds.
    store_media(sent_local, sent_audio)
    store_media(shot_local, screenshot)

    candidate["sentenceAudio_file"] = sent_audio
    candidate["picture_file"] = screenshot
    if want_explanation:
        store_media(exp_local, explain_audio)
        candidate["explanationAudio_file"] = explain_audio

    if push:
        # Insert this card as soon as its media is ready, so cards stream into Anki
        # one by one. Serialize the AnkiConnect add behind a lock for clean ordering.
        async with push_lock:
            ok, detail = await loop.run_in_executor(
                None, _push_one, candidate, source_url, source_id)
        if report is not None:
            report(candidate, ok, detail)
    return candidate


async def main_async(args):
    load_skill_env()
    cfg = load_config()
    # Explanation TTS is only generated when the note type actually has a
    # mapped `explanation` field — no point requiring GEMINI_API_KEY or
    # calling Gemini for a field that's never written to Anki.
    want_explanation = bool(cfg["field_map"].get("explanation"))
    if want_explanation and not os.environ.get("GEMINI_API_KEY"):
        sys.exit(
            "GEMINI_API_KEY not set. Add it to <skill-dir>/.env "
            "(copy .env.example to .env) or export it in your shell."
        )

    with open(args.candidates, encoding="utf-8") as f:
        data = json.load(f)

    source_url = data.get("source_url", "")

    if args.push:
        # Wire push.py's globals from config so build_note tags/maps identically to
        # the batch path, and pre-create the decks once before cards start landing.
        push_mod.ANKICONNECT = cfg["anki_connect_url"]
        push_mod.MODEL = cfg["note_type"]
        push_mod.FIELD_MAP = cfg["field_map"]
        if not push_mod.MODEL or not push_mod.FIELD_MAP.get("word") or not push_mod.FIELD_MAP.get("sentence"):
            sys.exit("config note_type/field_map incomplete — run `/sentence-mining setup`.")
        for d in {c["deck"] for c in data["candidates"]}:
            push_mod.anki_request("createDeck", deck=d)

    push_lock = asyncio.Lock()
    pushed = {"ok": 0, "fail": []}

    def report(candidate, ok, detail):
        if ok:
            pushed["ok"] += 1
            deck = candidate["deck"].split("::")[-1]
            print(f"  ✓ {candidate['lemma']} ({candidate.get('reading','')}) "
                  f"[{candidate.get('i_level','')}] → {deck}", file=sys.stderr, flush=True)
        else:
            pushed["fail"].append(candidate["lemma"])
            print(f"  ⚠ {candidate['lemma']} not added ({detail})", file=sys.stderr, flush=True)

    sem = asyncio.Semaphore(TTS_CONCURRENCY)
    with tempfile.TemporaryDirectory(prefix="sm_video_media_") as tmp:
        tasks = [
            process_one(args.video, args.source_id, i, c, tmp, sem,
                        push=args.push, source_url=source_url,
                        push_lock=push_lock, report=report,
                        want_explanation=want_explanation)
            for i, c in enumerate(data["candidates"])
            if (not want_explanation) or c.get("explanation", "").strip()
        ]
        processed = await asyncio.gather(*tasks)

    data["candidates"] = processed
    if args.push:
        print(f"\nInserted {pushed['ok']}/{len(processed)} cards"
              + (f" (not added: {pushed['fail']})" if pushed["fail"] else ""),
              file=sys.stderr, flush=True)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--source-id", required=True)
    ap.add_argument("--no-push", dest="push", action="store_false",
                    help="Only write the draft.json; do NOT insert into Anki "
                         "(draft-only / legacy two-step flow). Default is to push each "
                         "card into Anki as soon as its media is ready.")
    ap.set_defaults(push=True)
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
