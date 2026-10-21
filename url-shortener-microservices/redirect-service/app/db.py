"""Работа с общей БД PostgreSQL (таблицы links и clicks)."""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import asyncpg

log = logging.getLogger("db")

DSN = os.getenv("DATABASE_URL", "postgresql://shortener:shortener@db:5432/shortener")
NOTIFY_CHANNEL = "click_events"

pool: asyncpg.Pool | None = None


async def init() -> None:
    """Создать пул соединений (с повторами, пока БД поднимается)."""
    global pool
    for attempt in range(1, 31):
        try:
            pool = await asyncpg.create_pool(DSN, min_size=1, max_size=10)
            log.info("Подключились к БД")
            return
        except Exception as e:  # noqa: BLE001
            log.warning("БД недоступна (попытка %s): %s", attempt, e)
            await asyncio.sleep(1)
    raise RuntimeError("Не удалось подключиться к БД")


async def close() -> None:
    if pool:
        await pool.close()


async def get_link(code: str):
    return await pool.fetchrow(
        "SELECT short_code, original_url, expires_at FROM links WHERE short_code = $1",
        code,
    )


async def register_click(code: str, user_agent: str, ip: str, replica: str) -> int:
    """Записать клик и разослать уведомление всем репликам через NOTIFY.

    NOTIFY в PostgreSQL доставляется после COMMIT, поэтому подписчики
    получат событие только когда клик точно сохранён.
    """
    async with pool.acquire() as con:
        async with con.transaction():
            clicked_at = await con.fetchval(
                "INSERT INTO clicks (short_code, user_agent, ip) "
                "VALUES ($1, $2, $3) RETURNING clicked_at",
                code, user_agent, ip,
            )
            total = await con.fetchval(
                "SELECT count(*) FROM clicks WHERE short_code = $1", code
            )
            payload = json.dumps({
                "type": "click",
                "code": code,
                "total": total,
                "last_click": clicked_at.isoformat(),
                "redirect_replica": replica,
            })
            await con.execute("SELECT pg_notify($1, $2)", NOTIFY_CHANNEL, payload)
    return total


async def count_clicks(code: str) -> int:
    return await pool.fetchval("SELECT count(*) FROM clicks WHERE short_code = $1", code)


async def get_stats(code: str):
    link = await get_link(code)
    if link is None:
        return None
    agg = await pool.fetchrow(
        "SELECT count(*) AS total, max(clicked_at) AS last_click "
        "FROM clicks WHERE short_code = $1",
        code,
    )
    by_day = await pool.fetch(
        "SELECT (clicked_at AT TIME ZONE 'UTC')::date AS day, count(*) AS clicks "
        "FROM clicks WHERE short_code = $1 GROUP BY day ORDER BY day",
        code,
    )
    return {
        "original_url": link["original_url"],
        "total": agg["total"],
        "last_click": agg["last_click"],
        "by_day": [(r["day"], r["clicks"]) for r in by_day],
    }


async def top_links(n: int):
    return await pool.fetch(
        "SELECT l.short_code, l.original_url, count(c.id) AS clicks "
        "FROM links l LEFT JOIN clicks c ON c.short_code = l.short_code "
        "GROUP BY l.short_code, l.original_url "
        "ORDER BY clicks DESC, l.short_code "
        "LIMIT $1",
        n,
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
