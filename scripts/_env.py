"""Load KEY=VALUE pairs from the skill's .env into os.environ.

Real env vars win — .env is a fallback so the skill works without polluting ~/.zshrc.
No external dependency (avoids needing python-dotenv).
"""
import os
import sys
from pathlib import Path


def load_skill_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def warn_if_ephemeral_gemini_key():
    """A permanent Gemini API key looks like `AIza…`. Anything else (e.g. an OAuth
    access token `AQ.…`/`ya29.…`) expires in ~1h and will 401 mid-run. Warn so the
    user swaps in a permanent key for unattended use. Returns True if the key is set."""
    k = os.environ.get("GEMINI_API_KEY", "")
    if not k:
        return False
    if not k.startswith("AIza"):
        print("  ⚠ GEMINI_API_KEY looks like a temporary OAuth token (expires ~1h), not a\n"
              "    permanent API key. TTS will 401 once it expires. Get a permanent key at\n"
              "    https://aistudio.google.com/apikey (looks like 'AIzaSy…').", file=sys.stderr)
    return True
