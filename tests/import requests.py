"""Manual ElevenLabs model query. Set ELEVENLABS_API_KEY before running."""
import json
import os
from pathlib import Path
import requests


def main():
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set ELEVENLABS_API_KEY in the environment first")
    response = requests.get(
        "https://api.elevenlabs.io/v1/models",
        headers={"xi-api-key": key, "Accept": "application/json"},
        timeout=15,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise SystemExit(f"ElevenLabs request failed: HTTP {response.status_code}")
    Path("models.json").write_text(
        json.dumps(response.json(), ensure_ascii=False, indent=4), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
