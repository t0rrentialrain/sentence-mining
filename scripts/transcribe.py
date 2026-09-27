#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transcribe a video/audio file with Soniox (Korean), output word-level JSON.

Usage: transcribe.py <video-path>
Stdout: JSON with {full_text, words: [{text, start_ms, end_ms, speaker}]}

Claude fills in the `sentences` array in Step 2.5 of SKILL.md.

Soniox async flow (verified against the live API 2026-08-31):
  1. POST /v1/files            (multipart upload)      -> {"id": file_id}
  2. POST /v1/transcriptions   (JSON, references file_id,
                                 model "stt-async-v5")   -> {"id": job_id, "status": ...}
  3. GET  /v1/transcriptions/{job_id}                    -> {"status": ...}  (poll)
  4. GET  /v1/transcriptions/{job_id}/transcript          -> {"text": ..., "tokens": [...]}

Uses `requests` (streamed multipart) instead of urllib — urllib's raw in-memory
POST of a large (tens-of-MB) multipart body was observed to trigger a
ConnectionResetError partway through the upload on this network; requests/curl
did not have this problem.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_skill_env

import requests

SONIOX_API = "https://api.soniox.com/v1"
MODEL = "stt-async-v5"


def _extract_audio(video_path):
    """Transcode to mono 16kHz WAV — smaller upload, faster ASR decode."""
    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found; uploading the full file (slower).", file=sys.stderr)
        return video_path, False
    fd, audio_path = tempfile.mkstemp(suffix=".wav", prefix="sm_audio_")
    os.close(fd)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", audio_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return audio_path, True
    except (subprocess.CalledProcessError, OSError) as e:
        os.path.exists(audio_path) and os.remove(audio_path)
        print(f"audio extraction failed ({e}); uploading the full file.", file=sys.stderr)
        return video_path, False


def main(video_path):
    load_skill_env()
    key = os.environ.get("SONIOX_API_KEY")
    if not key:
        sys.exit(
            "SONIOX_API_KEY not set. Add it to <skill-dir>/.env "
            "(copy .env.example to .env) or export it in your shell."
        )

    auth_headers = {"Authorization": f"Bearer {key}"}

    audio_path, is_temp = _extract_audio(video_path)
    try:
        size = os.path.getsize(audio_path)
        print(f"Uploading {size // 1024}KB audio to Soniox…", file=sys.stderr)

        # Step 1: upload the file.
        with open(audio_path, "rb") as f:
            r = requests.post(
                f"{SONIOX_API}/files",
                headers=auth_headers,
                files={"file": (os.path.basename(audio_path), f, "audio/wav")},
                timeout=(15, 600),
            )
        r.raise_for_status()
        file_id = r.json()["id"]
    finally:
        if is_temp and os.path.exists(audio_path):
            os.remove(audio_path)

    # Step 2: create the transcription job.
    r = requests.post(
        f"{SONIOX_API}/transcriptions",
        headers={**auth_headers, "Content-Type": "application/json"},
        json={
            "file_id": file_id,
            "model": MODEL,
            "language_hints": ["ko"],
            "enable_speaker_diarization": True,
        },
        timeout=30,
    )
    r.raise_for_status()
    job = r.json()
    job_id = job["id"]
    print(f"Transcription job submitted: {job_id}", file=sys.stderr)

    # Step 3: poll until complete (up to ~30 min).
    for attempt in range(360):
        time.sleep(5)
        r = requests.get(f"{SONIOX_API}/transcriptions/{job_id}", headers=auth_headers, timeout=30)
        r.raise_for_status()
        status = r.json()
        state = (status.get("status") or "").lower()
        if state == "completed":
            break
        if state == "error":
            sys.exit(f"Soniox transcription failed: {status.get('error_message') or status}")
        if attempt % 6 == 0:
            print(f"  …{state} ({attempt * 5}s elapsed)", file=sys.stderr)
    else:
        sys.exit("Soniox transcription timed out after 30 minutes.")

    # Step 4: fetch the transcript.
    r = requests.get(f"{SONIOX_API}/transcriptions/{job_id}/transcript", headers=auth_headers, timeout=60)
    r.raise_for_status()
    transcript = r.json()

    # Soniox returns CHARACTER-level tokens (each syllable/char is its own
    # token) with whitespace marking word boundaries — not word-level tokens.
    # IMPORTANT: a leading space is not always its own token — Soniox
    # sometimes glues it onto the *next* char's token (e.g. text=" 해").
    # A boundary check that only looks for text.strip() == "" misses those
    # and silently fuses real words together. Split on ANY leading/embedded
    # whitespace inside a token, not just whole-whitespace tokens.
    raw_tokens = transcript.get("tokens") or []
    raw_words = []
    buf_text, buf_start, buf_end, buf_speaker = "", None, None, None

    def _flush():
        nonlocal buf_text, buf_start, buf_end, buf_speaker
        if buf_text:
            raw_words.append({"text": buf_text, "start_ms": buf_start,
                               "end_ms": buf_end, "speaker": buf_speaker})
        buf_text, buf_start, buf_end, buf_speaker = "", None, None, None

    for t in raw_tokens:
        text = t.get("text", "")
        # Split this token on internal whitespace runs; each split marks a
        # word boundary regardless of whether whitespace was its own token.
        segments = re.split(r'(\s+)', text)
        for seg in segments:
            if seg == "":
                continue
            if seg.strip() == "":
                _flush()
                continue
            if buf_start is None:
                buf_start = t.get("start_ms", 0)
                buf_speaker = t.get("speaker")
            buf_text += seg
            buf_end = t.get("end_ms", buf_end)
    _flush()

    full_text = (transcript.get("text") or " ".join(w["text"] for w in raw_words)).strip()

    out = {
        "transcript_id": job_id,
        "language": "ko",
        "audio_duration_ms": status.get("audio_duration_ms") or 0,
        "full_text": full_text,
        "words": raw_words,
        # `sentences` is intentionally absent — Claude fills it in Step 2.5 of
        # SKILL.md by reading `words` + `full_text` and emitting corrected,
        # well-split chunks with preserved timing + speaker labels.
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: transcribe.py <video-path>")
    main(sys.argv[1])
