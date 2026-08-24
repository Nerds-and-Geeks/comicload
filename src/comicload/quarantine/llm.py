"""Vision LLM adapter for quarantine cover text extraction.

Extracts title, issue number, and optional publication year from comic cover image bytes
and returns a query string (e.g. "Superman #35" or "Alex + Ada #2") ready for
ConfirmService.lookup(). Fail-closed: returns "" on missing API key, network error,
or unparseable response.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from comicload.config import LlmConfig


PROMPT = (
    "Identify the comic book series title, issue number, and publication year "
    "visible on this cover photo. Respond ONLY with a single query line in the format: "
    "Title #Issue Year (e.g. 'Superman #35 2024' or 'Alex + Ada #2'). "
    "If unreadable or unknown, respond with nothing."
)


def _prepare_image_for_llm(image_bytes: bytes) -> tuple[bytes, str]:
    """Ensure image is formatted as a valid JPEG under 1560px for vision API constraints."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if max(img.width, img.height) > 1560:
        img.thumbnail((1560, 1560), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


def describe_cover(image_bytes: bytes | None, config: LlmConfig) -> tuple[str, str | None]:
    """Use vision API to extract comic title and issue number from cover image bytes.

    Returns (query_string, error_message).
    """
    if not image_bytes:
        return "", "No cover image bytes provided"

    api_key = config.resolved_api_key()
    if not api_key:
        env_var = (
            "ANTHROPIC_API_KEY"
            if config.provider == "anthropic"
            else "OPENAI_API_KEY"
            if config.provider == "openai"
            else "GEMINI_API_KEY"
        )
        return "", f"No API key found. Set {env_var} environment variable."

    try:
        prepared_bytes, mime_type = _prepare_image_for_llm(image_bytes)
        if config.provider == "anthropic":
            return _call_anthropic(prepared_bytes, mime_type, config, api_key), None
        elif config.provider == "openai":
            return _call_openai(prepared_bytes, mime_type, config, api_key), None
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="ignore")
            data = json.loads(body)
            err_obj = data.get("error")
            msg = err_obj.get("message") if isinstance(err_obj, dict) else None
            msg = msg or data.get("message") or body
        except Exception:
            msg = exc.reason
        return "", f"HTTP Error {exc.code}: {msg}"
    except Exception as exc:
        return "", f"API Error: {exc}"

    return "", "Unsupported LLM provider"


def _call_anthropic(image_bytes: bytes, mime_type: str, config: LlmConfig, api_key: str) -> str:
    b64_image = base64.b64encode(image_bytes).decode("ascii")

    payload = {
        "model": config.model or "claude-3-5-sonnet-20241022",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64_image,
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        text: str = data["content"][0]["text"].strip()
        return text


def _call_openai(image_bytes: bytes, mime_type: str, config: LlmConfig, api_key: str) -> str:
    b64_image = base64.b64encode(image_bytes).decode("ascii")

    payload = {
        "model": config.model or "gpt-4o",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64_image}"},
                    },
                ],
            }
        ],
    }

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        text: str = data["choices"][0]["message"]["content"].strip()
        return text
