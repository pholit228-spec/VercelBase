"""Standalone hashtag moderation extension for BOTIK.

Deletes any incoming group message containing #реклама, including messages
sent by moderators/admins. Telegram must allow the bot to delete messages
in the target chat.
"""

import re

REKLAMA_RE = re.compile(r"(?<!\w)#реклама\b", re.IGNORECASE)


def install(bot_module):
    bot = getattr(bot_module, "bot", None)
    if bot is None:
        return False

    def has_reklama(message):
        text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        return bool(REKLAMA_RE.search(text))

    @bot.message_handler(func=has_reklama, content_types=[
        "text", "photo", "video", "document", "animation", "audio",
        "voice", "video_note", "sticker"
    ])
    def delete_reklama(message):
        try:
            bot.delete_message(message.chat.id, message.message_id)
            print(
                f"[ad_filter] deleted #реклама from "
                f"user={getattr(message.from_user, 'id', '?')} "
                f"chat={message.chat.id}"
            )
        except Exception as exc:
            print(
                f"[ad_filter] failed to delete message "
                f"chat={message.chat.id} message={message.message_id}: "
                f"{type(exc).__name__}: {exc}"
            )

    return True
