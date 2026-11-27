"""
Small compatibility facade for the old synchronous bot.py.

Telegram transport is aiogram 3.x. The legacy handler functions remain
synchronous so the 3.6 MB bot does not need a risky manual rewrite.
"""

from __future__ import annotations

import asyncio
import io
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from aiogram import Bot as AiogramBot
from aiogram.types import (
    BufferedInputFile,
    FSInputFile,
    URLInputFile,
    Update,
)


class _ApiHelper:
    RETRY_ON_ERROR = True
    MAX_RETRIES = 4


apihelper = _ApiHelper()


class _Types:
    Update = Update


types = _Types()


@dataclass
class _MessageHandler:
    func: Callable[[Any], bool] | None = None
    commands: list[str] | None = None
    handler: Callable[[Any], Any] | None = None


@dataclass
class _CallbackHandler:
    func: Callable[[Any], bool] | None = None
    handler: Callable[[Any], Any] | None = None


@dataclass
class _ChatMemberHandler:
    handler: Callable[[Any], Any] | None = None


class TeleBot:
    """Legacy-shaped facade backed entirely by aiogram.Bot."""

    def __init__(self, token: str, *args, **kwargs):
        self.token = token
        self._bot: AiogramBot | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._message_handlers: list[_MessageHandler] = []
        self._callback_handlers: list[_CallbackHandler] = []
        self._chat_member_handlers: list[_ChatMemberHandler] = []

    def bind(self, bot: AiogramBot, loop: asyncio.AbstractEventLoop) -> None:
        self._bot = bot
        self._loop = loop

    def _require_bound(self) -> tuple[AiogramBot, asyncio.AbstractEventLoop]:
        if self._bot is None or self._loop is None:
            raise RuntimeError("aiogram bot is not bound to the current webhook loop")
        return self._bot, self._loop

    def _run(self, coro):
        _, loop = self._require_bound()
        if loop.is_closed():
            raise RuntimeError("webhook event loop is closed")
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=50)

    def message_handler(self, **kwargs):
        def decorator(fn):
            commands = kwargs.get("commands")
            self._message_handlers.append(
                _MessageHandler(
                    func=kwargs.get("func"),
                    commands=list(commands) if commands else None,
                    handler=fn,
                )
            )
            return fn
        return decorator

    def callback_query_handler(self, **kwargs):
        def decorator(fn):
            self._callback_handlers.append(
                _CallbackHandler(func=kwargs.get("func"), handler=fn)
            )
            return fn
        return decorator

    def chat_member_handler(self, **kwargs):
        def decorator(fn):
            self._chat_member_handlers.append(_ChatMemberHandler(handler=fn))
            return fn
        return decorator

    @staticmethod
    def _command_matches(message, commands):
        if not commands:
            return False
        text = getattr(message, "text", None) or ""
        if not text.startswith("/"):
            return False
        command = text.split()[0][1:].split("@", 1)[0].lower()
        return command in {str(x).lstrip("/").lower() for x in commands}

    def _message_matches(self, entry, message):
        if entry.commands and not self._command_matches(message, entry.commands):
            return False
        if entry.func is not None:
            try:
                if not bool(entry.func(message)):
                    return False
            except Exception:
                return False
        return True

    async def dispatch_message(self, message) -> None:
        for entry in self._message_handlers:
            if self._message_matches(entry, message):
                await asyncio.to_thread(entry.handler, message)
                return

    async def dispatch_callback(self, call) -> None:
        for entry in self._callback_handlers:
            try:
                matched = entry.func is None or bool(entry.func(call))
            except Exception:
                matched = False
            if matched:
                await asyncio.to_thread(entry.handler, call)
                return

    async def dispatch_chat_member(self, update) -> None:
        for entry in self._chat_member_handlers:
            await asyncio.to_thread(entry.handler, update)

    def process_new_updates(self, updates):
        # Kept only for compatibility with any legacy startup code.
        async def runner():
            for raw in updates:
                update = raw if isinstance(raw, Update) else Update.model_validate(raw)
                if update.message:
                    await self.dispatch_message(update.message)
                if update.callback_query:
                    await self.dispatch_callback(update.callback_query)
                if update.chat_member:
                    await self.dispatch_chat_member(update.chat_member)
        return self._run(runner())

    def reply_to(self, message, text, *args, **kwargs):
        chat_id = message.chat.id
        reply_to = getattr(message, "message_id", None)
        return self._run(
            self._bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to,
                **kwargs,
            )
        )

    def send_message(self, chat_id, text, **kwargs):
        return self._run(self._bot.send_message(chat_id=chat_id, text=text, **kwargs))

    @staticmethod
    def _input_file(source, default_name="file.bin"):
        if isinstance(source, (BufferedInputFile, FSInputFile, URLInputFile)):
            return source
        if isinstance(source, bytes):
            return BufferedInputFile(source, filename=default_name)
        if isinstance(source, bytearray):
            return BufferedInputFile(bytes(source), filename=default_name)
        if isinstance(source, io.BytesIO):
            return BufferedInputFile(source.getvalue(), filename=default_name)
        if hasattr(source, "read"):
            try:
                pos = source.tell()
            except Exception:
                pos = None
            try:
                if pos is not None:
                    source.seek(0)
                data = source.read()
            finally:
                if pos is not None:
                    try:
                        source.seek(pos)
                    except Exception:
                        pass
            return BufferedInputFile(data, filename=default_name)
        if isinstance(source, str):
            if source.startswith(("http://", "https://")):
                return URLInputFile(source)
            return FSInputFile(source)
        raise TypeError(f"Unsupported Telegram file source: {type(source).__name__}")

    def send_photo(self, chat_id, photo, **kwargs):
        return self._run(
            self._bot.send_photo(
                chat_id=chat_id,
                photo=self._input_file(photo, "photo.jpg"),
                **kwargs,
            )
        )

    def send_video(self, chat_id, video, **kwargs):
        return self._run(
            self._bot.send_video(
                chat_id=chat_id,
                video=self._input_file(video, "video.mp4"),
                **kwargs,
            )
        )

    def send_animation(self, chat_id, animation, **kwargs):
        return self._run(
            self._bot.send_animation(
                chat_id=chat_id,
                animation=self._input_file(animation, "animation.gif"),
                **kwargs,
            )
        )

    def answer_callback_query(self, callback_query_id, text=None, **kwargs):
        return self._run(
            self._bot.answer_callback_query(
                callback_query_id=callback_query_id,
                text=text,
                **kwargs,
            )
        )

    def get_chat(self, chat_id):
        return self._run(self._bot.get_chat(chat_id=chat_id))

    def get_chat_member(self, chat_id, user_id):
        return self._run(
            self._bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        )

    def leave_chat(self, chat_id):
        return self._run(self._bot.leave_chat(chat_id=chat_id))

    def get_webhook_info(self):
        return self._run(self._bot.get_webhook_info())

    def set_webhook(self, *args, **kwargs):
        return self._run(self._bot.set_webhook(*args, **kwargs))
