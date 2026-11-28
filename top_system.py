"""Standalone /top leaderboard extension for BOTIK.

Tracks continuous time in the current top-5 using Neon/Postgres, without
editing the huge legacy bot.py.
"""

import random
import time

ALIASES = {"/top", "топ", "лидерборд", "топы", "доска лидеров"}


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS top5_streaks (
            user_id BIGINT PRIMARY KEY,
            entered_at DOUBLE PRECISION NOT NULL,
            last_seen_top5 DOUBLE PRECISION NOT NULL,
            awarded_count INTEGER NOT NULL DEFAULT 0
        )
    """)


def _name(row):
    username = (row["username"] or "").strip()
    first_name = (row["first_name"] or "").strip()
    return f"@{username}" if username else (first_name or f"Игрок {row['user_id']}")


def _esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_duration(seconds):
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes and len(parts) < 2:
        parts.append(f"{minutes} мин")
    return " ".join(parts) or "менее 1 мин"


def _grant_weekly_style(conn, legacy, user_id):
    styles = getattr(legacy, "SECRET_STYLES", {}) or {}
    if not styles:
        return None

    rows = conn.execute(
        "SELECT slime_key FROM secret_styles WHERE user_id=?",
        (user_id,),
    ).fetchall()
    owned = {r["slime_key"] for r in rows}
    available = [key for key in styles if key not in owned]
    if not available:
        return None

    slime_key = random.choice(available)
    conn.execute(
        """
        INSERT INTO secret_styles(user_id, slime_key, unlocked_at)
        VALUES(?, ?, ?)
        ON CONFLICT (user_id, slime_key) DO NOTHING
        """,
        (user_id, slime_key, time.time()),
    )
    info = styles[slime_key]
    return info.get("style_ru") or info.get("style") or slime_key


def install(bot_module):
    bot = getattr(bot_module, "bot", None)
    if bot is None:
        return False

    def is_top_trigger(message):
        if not getattr(message, "text", None):
            return False
        first = message.text.strip().lower().split(maxsplit=1)[0].split("@", 1)[0]
        return first in ALIASES

    @bot.message_handler(func=is_top_trigger)
    def top_cmd(message):
        from pg_backend import connect

        user_id = int(message.from_user.id)
        now = time.time()
        conn = connect()

        try:
            _ensure_table(conn)

            # Money ranking uses the same passive-income logic as the legacy bot.
            calc = getattr(bot_module, "calc_passive_income", None)
            if callable(calc):
                try:
                    calc(user_id)
                except Exception:
                    pass

            rows = conn.execute(
                """
                SELECT user_id, username, first_name, new_bucks
                FROM users
                ORDER BY new_bucks DESC, user_id ASC
                LIMIT 15
                """
            ).fetchall()

            rank = next(
                (i + 1 for i, row in enumerate(rows)
                 if int(row["user_id"]) == user_id),
                None,
            )
            top5_ids = {int(row["user_id"]) for row in rows[:5]}

            current = conn.execute(
                "SELECT * FROM top5_streaks WHERE user_id=?",
                (user_id,),
            ).fetchone()

            reward = None

            if user_id in top5_ids:
                if current:
                    entered_at = float(current["entered_at"])
                    if now - entered_at >= 7 * 86400:
                        reward = _grant_weekly_style(conn, bot_module, user_id)
                        conn.execute(
                            """
                            UPDATE top5_streaks
                            SET entered_at=?, last_seen_top5=?,
                                awarded_count=awarded_count+1
                            WHERE user_id=?
                            """,
                            (now, now, user_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE top5_streaks SET last_seen_top5=? WHERE user_id=?",
                            (now, user_id),
                        )
                else:
                    conn.execute(
                        """
                        INSERT INTO top5_streaks(user_id, entered_at, last_seen_top5)
                        VALUES(?, ?, ?)
                        """,
                        (user_id, now, now),
                    )
            else:
                # Once the player falls out of top-5, the weekly streak is reset.
                conn.execute("DELETE FROM top5_streaks WHERE user_id=?", (user_id,))

            conn.commit()

            lines = ["🏆 <b>ЛИДЕРБОРД — NEWBUCKS</b>", ""]
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

            for i, row in enumerate(rows):
                prefix = medals[i] if i < 5 else f"{i + 1}."
                lines.append(
                    f"{prefix} <b>{_esc(_name(row))}</b> — "
                    f"<b>{int(row['new_bucks']):,}</b> 💵"
                )

            if not rows:
                lines.append("Пока никто не набрал Newbucks.")

            lines.append("")
            if rank:
                lines.append(f"📍 Твоя позиция: <b>#{rank}</b>")
            else:
                lines.append("📍 Ты пока не вошёл в топ-15.")

            if user_id in top5_ids:
                streak_row = conn.execute(
                    "SELECT entered_at FROM top5_streaks WHERE user_id=?",
                    (user_id,),
                ).fetchone()

                if reward:
                    lines.extend([
                        "",
                        "🎁 <b>7 дней в топ-5!</b>",
                        f"✨ Тайный стиль: <b>{_esc(reward)}</b>",
                    ])
                elif streak_row:
                    elapsed = now - float(streak_row["entered_at"])
                    left = max(0, 7 * 86400 - elapsed)
                    lines.extend([
                        "",
                        f"🔥 В топ-5: <b>{_format_duration(elapsed)}</b>",
                        f"⏳ До тайного стиля: <b>{_format_duration(left)}</b>",
                    ])
            else:
                lines.extend(["", "🔥 Чтобы начать недельный streak, войди в топ-5."])

            bot.send_message(
                message.chat.id,
                "\n".join(lines),
                parse_mode="HTML",
                reply_to_message_id=getattr(message, "message_id", None),
            )
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[top] error: {type(exc).__name__}: {exc}")
            bot.reply_to(message, "❌ Не удалось загрузить лидерборд. Попробуйте ещё раз.")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    return True
