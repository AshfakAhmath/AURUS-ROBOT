"""Validate the Groq-backed AURUS assistant configuration."""

from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def main() -> int:
    try:
        from groq import Groq
    except ImportError:
        print("Groq SDK missing. Run: pip install -r requirements.txt")
        return 1

    if not os.getenv("GROQ_API_KEY", "").strip():
        print("GROQ_API_KEY is missing. Add it to .env; no local LLM download is required.")
        return 1

    model = os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-20b")
    try:
        client = Groq(timeout=15.0, max_retries=0)
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with READY only."}],
            temperature=0,
            max_completion_tokens=128,
            reasoning_effort="low",
        )
        response = (completion.choices[0].message.content or "").strip()
        print(f"Groq assistant ready ({model}): {response or 'connected'}")
        return 0
    except Exception as exc:
        print(f"Groq setup check failed: {str(exc)[:240]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
