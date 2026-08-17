from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    zep_api_key: str = os.getenv("ZEP_API_KEY", "")
    semantic_graph_id: str = os.getenv("ZEP_SEMANTIC_GRAPH_ID", "vinuni-lab17-domain-kb")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    context_tokens: int = int(os.getenv("LAB_CONTEXT_TOKENS", "8000"))
    zep_poll_timeout: int = int(os.getenv("ZEP_POLL_TIMEOUT", "240"))
    zep_poll_interval: float = float(os.getenv("ZEP_POLL_INTERVAL", "5"))

    # LLM used only for the UI chat reply (never for benchmark scoring).
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
    # A floating alias, not a pinned version: Google retired
    # gemini-2.5-flash-lite for new keys ("no longer available to new users",
    # 404), so a pinned default breaks the demo whenever a version is sunset.
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")


settings = Settings()
