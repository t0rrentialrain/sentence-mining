#!/usr/bin/env bash
# Ensure Anki + AnkiConnect are up and stable before the pipeline touches them.
#
# Usage:  bash scripts/ensure_anki.sh
# Exit 0 = AnkiConnect reachable and stable; exit 1 = give up, tell the user.
#
# Honors config.json's anki_connect_url if set (defaults to localhost:8765).

set -u

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

URL="$(python3 - "$SKILL_DIR" <<'PY' 2>/dev/null || true
import json, sys, pathlib
cfg = pathlib.Path(sys.argv[1]) / "config.json"
url = "http://localhost:8765"
try:
    url = json.loads(cfg.read_text()).get("anki_connect_url") or url
except Exception:
    pass
print(url)
PY
)"
URL="${URL:-http://localhost:8765}"

ping() {
  curl -s --max-time 5 "$URL" -d '{"action":"version","version":6}' 2>/dev/null | grep -q '"result"'
}

stable() {
  for _ in 1 2 3; do
    ping || return 1
    sleep 2
  done
  return 0
}

if ping; then
  if stable; then
    echo "AnkiConnect up and stable ($URL)"
    exit 0
  fi
  echo "AnkiConnect answered once but isn't stable (mid-load?) — waiting it out…" >&2
fi

echo "AnkiConnect not reachable at $URL — launching Anki…" >&2

# Windows (Git Bash / MSYS2): launch Anki directly by exe path.
# 'Start-Process Anki' only works if Anki registered itself as an App Execution
# Alias (not guaranteed) — resolving the real anki.exe is more reliable, and
# silent failure here is exactly the kind of thing that wastes a full ~3min
# retry loop below for nothing.
# Falls back to 'open -a Anki' on macOS if PowerShell is not available.
launch_windows_anki() {
  local candidates=(
    "$LOCALAPPDATA/Programs/Anki/anki.exe"
    "/c/Program Files/Anki/anki.exe"
    "/c/Program Files (x86)/Anki/anki.exe"
  )
  for c in "${candidates[@]}"; do
    if [ -n "$c" ] && [ -f "$c" ]; then
      "$c" >/dev/null 2>&1 &
      disown 2>/dev/null || true
      return 0
    fi
  done
  # Fallback: let Windows resolve it (works if Anki set up a Start Menu /
  # App Execution Alias entry even though the direct paths above missed it).
  powershell.exe -Command "Start-Process 'Anki'" 2>/dev/null || true
}

if command -v powershell.exe >/dev/null 2>&1; then
  launch_windows_anki
elif command -v open >/dev/null 2>&1; then
  open -a Anki 2>/dev/null || true
else
  echo "  Don't know how to launch Anki on this platform — please open it manually." >&2
fi

for i in $(seq 1 36); do
  if ping; then
    echo "AnkiConnect responded after ~$((i*5))s — verifying stability…" >&2
    if stable; then
      echo "AnkiConnect up and stable ($URL)"
      exit 0
    fi
    echo "  not stable yet, still loading…" >&2
  fi
  sleep 5
done

echo "AnkiConnect still unreachable at $URL after launching Anki." >&2
echo "Anki may be showing a modal (sync / database check) that blocks the addon, or the addon is disabled." >&2
exit 1
