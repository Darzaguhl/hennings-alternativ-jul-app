import base64
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

# Fast/cheap is the right trade-off here -- this is a rough suggestion the
# volunteer confirms or edits, not the final record. See InventoryItem's
# docstring for why quantity in particular is never trusted from this
# alone.
_MODEL = "claude-haiku-4-5-20251001"

_PROMPT = (
    "You are helping a charity volunteer log a donated item into an "
    "inventory system. Look at the photo and respond with ONLY a JSON "
    "object (no other text, no markdown fences) with exactly these keys: "
    '"category_guess" (a short 1-3 word category name in Norwegian, e.g. '
    '"Vinterjakker", "Leker", "Bøker"), "description_guess" (a short '
    "Norwegian description of what's in the photo, e.g. \"Bla vinterjakke, "
    'str M"), and "quantity_guess" (your best-effort integer count of how '
    "many distinct items are visible -- if you can't tell, use 1). If the "
    "photo doesn't clearly show a donated item, still make your best "
    "guess rather than refusing."
)


def identify_item(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    """Ask Claude to suggest a category/description/quantity for a photo of
    a donated item. Returns {} if ANTHROPIC_API_KEY isn't set, the request
    fails, or the response doesn't parse into the expected shape -- never
    raises, same contract as email.send_invite_email. The caller (the
    inventory/identify/ view) always treats this as a best-effort
    suggestion the volunteer can freely override, never as something
    that's saved on its own -- see InventoryItem's docstring on why
    quantity especially is never trusted from a single photo alone."""

    if not settings.ANTHROPIC_API_KEY:
        logger.info("ANTHROPIC_API_KEY not set; skipping item identification.")
        return {}

    payload = {
        "model": _MODEL,
        "max_tokens": 256,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.error("Failed to identify inventory item: %s", exc)
        return {}

    try:
        text = body["content"][0]["text"]
        suggestion = json.loads(text)
        return {
            "category_guess": str(suggestion.get("category_guess", ""))[:100],
            "description_guess": str(suggestion.get("description_guess", ""))[:255],
            "quantity_guess": max(int(suggestion.get("quantity_guess", 1)), 1),
        }
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        logger.error("Unexpected response shape identifying inventory item: %s", exc)
        return {}
