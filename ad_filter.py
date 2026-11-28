"""Standalone #реклама moderation extension for the aiogram webhook."""

import re
from aiogram.types import Message

_REKLAMA = re.compile(r"(?<!\w)#реклама\b", re.IGNORECASE)


async def handle_message(message: Message) -> bool:
    """Delete messages containing #реклама and stop further processing."""
    text = message.text or message.caption or ""
    if not _REKLAMA.search(text):
        return False

    try:
        await message.delete()
        print(
            f"[ad_filter] deleted #реклама: "
            f"chat={message.chat.id} message={message.message_id}",
            flush=True,
        )
    except Exception as exc:
        print(
            f"[ad_filter] failed to delete: "
            f"chat={message.chat.id} message={message.message_id}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

    return True
