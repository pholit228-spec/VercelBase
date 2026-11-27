"""Vercel webhook entrypoint: aiogram 3.x + existing synchronous handlers."""

import asyncio
import ast
import hashlib
import os
import sys
import traceback
import importlib.util
from pathlib import Path

from flask import Flask, abort, request
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Update

if not os.environ.get("DATABASE_URL"):
    for key in (
        "STORAGE_POSTGRES_URL",
        "STORAGE_DATABASE_URL",
        "STORAGE_POSTGRES_URL_NO_SSL",
        "STORAGE_DATABASE_URL_UNPOOLED",
        "STORAGE_DATABASE_URL_NON_POOLING",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
    ):
        value = os.environ.get(key)
        if value:
            os.environ["DATABASE_URL"] = value
            break

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

WEBHOOK_SECRET = (
    os.environ.get("WEBHOOK_SECRET")
    or hashlib.sha256(BOT_TOKEN.encode("utf-8")).hexdigest()
)

if not os.environ.get("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL is missing: connect the Vercel project to Neon")

import sitecustomize  # noqa: F401,E402

app = Flask(__name__)

_legacy_bot = None
_db_checked = False
_aiogram_dispatcher = Dispatcher()
_router = Router()
_aiogram_dispatcher.include_router(_router)


def _load_legacy_bot():
    """Load bot.py while replacing its old TeleBot import with our aiogram facade."""
    global _legacy_bot

    path = Path(__file__).resolve().parent.parent / "bot.py"
    source = path.read_text(encoding="utf-8")

    # bot.py is large and contains the working business logic. We keep it intact,
    # but swap its Telegram transport module before executing it.
    source = source.replace(
        "import telebot\n    from telebot import apihelper",
        "import aiogram_legacy as telebot\n    from aiogram_legacy import apihelper",
        1,
    )

    tree = ast.parse(source, filename=str(path))

    # Disable only the old top-level startup() call. Webhook lifecycle belongs here.
    kept = []
    removed = 0
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "startup"
            and not node.value.args
            and not node.value.keywords
        ):
            removed += 1
            continue
        kept.append(node)

    if removed != 1:
        raise RuntimeError(
            f"Could not disable legacy startup() in bot.py (found {removed})"
        )

    tree.body = kept
    ast.fix_missing_locations(tree)

    spec = importlib.util.spec_from_file_location("bot", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not create loader for bot.py")

    module = importlib.util.module_from_spec(spec)
    module.__name__ = "bot"
    module.__file__ = str(path)
    sys.modules["bot"] = module

    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


def _get_legacy_bot():
    global _legacy_bot, _db_checked

    if _legacy_bot is None:
        print("[startup] loading bot.py with aiogram transport", flush=True)
        _legacy_bot = _load_legacy_bot()

    if not _db_checked:
        import pg_migrate
        pg_migrate.migrate_once()
        _db_checked = True
        print("[startup] Neon migration checked", flush=True)

    return _legacy_bot


async def _dispatch_update(raw: dict):
    legacy = _get_legacy_bot()
    bot = Bot(BOT_TOKEN)

    # The legacy synchronous facade sends all Telegram API calls through
    # this exact aiogram Bot and this request's event loop.
    legacy.bot.bind(bot, asyncio.get_running_loop())

    try:
        update = Update.model_validate(raw)
        await _aiogram_dispatcher.feed_update(bot, update)
    finally:
        await bot.session.close()


def _group_greeting(chat_id: int):
    """Return the saved greeting for a group, or None when it is not configured."""
    from pg_backend import connect

    conn = connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_greetings (
                chat_id BIGINT PRIMARY KEY,
                greeting TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur = conn.execute(
            "SELECT greeting FROM group_greetings WHERE chat_id=?",
            (chat_id,),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _set_group_greeting(chat_id: int, greeting: str):
    """Persist one greeting per group in Neon PostgreSQL."""
    from pg_backend import connect

    conn = connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_greetings (
                chat_id BIGINT PRIMARY KEY,
                greeting TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute(
            """
            INSERT INTO group_greetings (chat_id, greeting)
            VALUES (?, ?)
            ON CONFLICT (chat_id)
            DO UPDATE SET greeting=EXCLUDED.greeting, updated_at=NOW()
            """,
            (chat_id, greeting),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def _handle_group_features(message):
    """Handle persistent group greetings before legacy message dispatch."""
    chat = message.chat
    if chat.type not in {"group", "supergroup"}:
        return False

    text_value = (message.text or "").strip()

    # Only the configured Telegram user ID may change the greeting.
    if text_value.startswith("/setgreet"):
        admin_id = os.environ.get("ADMIN_ID", "").strip()
        if admin_id and str(message.from_user.id) == admin_id:
            greeting = text_value[len("/setgreet"):].strip()
            if not greeting:
                await message.answer("Использование: /setgreet Текст приветствия")
                return True

            _set_group_greeting(chat.id, greeting)
            await message.answer(f"Приветствие сохранено: {greeting}")
            return True

        await message.answer("Эту команду может использовать только администратор.")
        return True

    if message.new_chat_members:
        greeting = _group_greeting(chat.id)
        if greeting:
            for member in message.new_chat_members:
                # Greet both human users and bots that join the group.
                await message.answer(f"{member.full_name}, {greeting}")

    return False


@_router.message()
async def _all_messages(message):
    if await _handle_group_features(message):
        return

    legacy = _get_legacy_bot()
    await legacy.bot.dispatch_message(message)


@_router.callback_query()
async def _all_callbacks(callback):
    legacy = _get_legacy_bot()
    await legacy.bot.dispatch_callback(callback)


@_router.chat_member()
async def _all_chat_members(update):
    legacy = _get_legacy_bot()
    await legacy.bot.dispatch_chat_member(update)


def _claim_update(update_id: int) -> bool:
    """Atomically claim a Telegram update across all Vercel instances."""
    from pg_backend import connect

    conn = connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_processed_updates (
                update_id BIGINT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'processing',
                received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ
            )
        """)
        cur = conn.execute(
            """
            INSERT INTO telegram_processed_updates (update_id, status)
            VALUES (?, 'processing')
            ON CONFLICT (update_id) DO NOTHING
            """,
            (update_id,),
        )
        claimed = cur.rowcount == 1
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _finish_update(update_id: int, status: str) -> None:
    """Record the final outcome; the initial claim remains even on failure."""
    from pg_backend import connect

    conn = connect()
    try:
        conn.execute(
            """
            UPDATE telegram_processed_updates
            SET status=?, finished_at=NOW()
            WHERE update_id=?
            """,
            (status, update_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _check_secret():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)


@app.route("/", methods=["GET", "HEAD"])
def health():
    return "OK", 200


@app.route("/webhook", methods=["POST"])
async def webhook():
    _check_secret()

    try:
        raw = request.get_json(force=True, silent=False)
        if not isinstance(raw, dict):
            return "Invalid update", 400

        try:
            update_id = int(raw["update_id"])
        except (KeyError, TypeError, ValueError):
            return "Invalid update_id", 400

        print(f"[webhook] received update_id={update_id}", flush=True)

        # The unique PostgreSQL key makes this safe across concurrent
        # serverless instances and survives cold starts/redeployments.
        if not _claim_update(update_id):
            print(f"[webhook] duplicate ignored update_id={update_id}", flush=True)
            return "Duplicate ignored", 200

        try:
            await _dispatch_update(raw)
        except Exception:
            print(
                f"[webhook] handler failed; update will not be replayed update_id={update_id}",
                file=sys.stderr,
                flush=True,
            )
            traceback.print_exc(file=sys.stderr)
            try:
                _finish_update(update_id, "failed")
            except Exception:
                traceback.print_exc(file=sys.stderr)
            # The update may already have sent messages before the exception.
            # A 200 prevents Telegram from replaying and duplicating those sends.
            return "Processed with handler error; replay suppressed", 200

        try:
            _finish_update(update_id, "completed")
        except Exception:
            # The committed claim still blocks duplicate deliveries.
            print(
                f"[webhook] could not mark completed update_id={update_id}",
                file=sys.stderr,
                flush=True,
            )
            traceback.print_exc(file=sys.stderr)

        print(f"[webhook] completed update_id={update_id}", flush=True)
        return "", 200

    except Exception:
        print("[webhook] ERROR before update claim", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return "Webhook processing failed", 500
