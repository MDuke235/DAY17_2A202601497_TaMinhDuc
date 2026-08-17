"""Thin Gemini wrapper for the demo UI chat reply.

This is the ONLY place the lab calls a generative LLM. Benchmark scoring never
uses an LLM (see LAB.md): retrieval evidence is graded deterministically. Here
Gemini only turns retrieved memory context into a grounded assistant reply so
the mini-product feels real.

Default model: gemini-flash-lite-latest (override with GEMINI_MODEL). The alias
is used on purpose — pinned 2.5 versions return 404 "no longer available to new
users" once Google sunsets them.
"""

from __future__ import annotations

from typing import Any

from .config import settings

SYSTEM_INSTRUCTION = (
    "You are the assistant of a personal memory agent for VinUni Lab 17. "
    "Answer the user grounded ONLY in the retrieved memory context provided. "
    "If the context does not contain the answer, say so plainly instead of "
    "inventing facts. Be concise and cite the concrete markers/ids you used. "
    "You may reply in the user's language (Vietnamese or English)."
)


def gemini_available() -> bool:
    """True when a key is configured. UI uses this to show status."""
    return bool(settings.gemini_api_key)


def _to_contents(history: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Map chat history to google-genai `contents` turns.

    Roles: user -> "user", everything else (assistant/model) -> "model".
    """
    contents: list[dict[str, Any]] = []
    for msg in history:
        role = "user" if msg.get("role") == "user" else "model"
        text = msg.get("content", "")
        if not text:
            continue
        contents.append({"role": role, "parts": [{"text": text}]})
    return contents


def generate_reply(
    memory_context: str,
    history: list[dict[str, str]],
    user_message: str,
    *,
    model: str | None = None,
) -> str:
    """Generate a grounded assistant reply with Gemini.

    Raises RuntimeError if no key, and lets SDK/network errors bubble up so the
    UI can surface them. `history` should include the latest user turn or not —
    `user_message` is appended as the final user turn regardless.
    """
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Copy .env.example to .env and add a "
            "Google AI Studio key to enable chat replies."
        )

    # Lazy import so the rest of the package works without google-genai installed
    # (tests, report generation, retrieval benchmarks never need it).
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    model_name = model or settings.gemini_model

    grounding = (
        "Retrieved memory context for this turn:\n"
        "-------------------------------------\n"
        f"{memory_context.strip() or '(no memory retrieved)'}\n"
        "-------------------------------------\n\n"
        f"User message: {user_message}"
    )

    contents = _to_contents(history)
    contents.append({"role": "user", "parts": [{"text": grounding}]})

    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.3,
            max_output_tokens=800,
        ),
    )
    return (getattr(response, "text", "") or "").strip()
