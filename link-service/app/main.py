"""
link-service — модуль создания коротких ссылок (REST API).

Эндпоинты:
  POST   /api/links         — создать короткую ссылку
  GET    /api/links         — список всех ссылок
  DELETE /api/links/{code}  — удалить ссылку
  GET    /api/health        — проверка, что сервис жив
  GET    /                  — HTML-страница с формой
"""

import os
import secrets
import socket
import string
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

# ---------- Настройки (берутся из переменных окружения docker-compose) ----------

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://shortener:shortener@db:5432/shortener"
)
BASE_URL = os.getenv("BASE_URL", "http://localhost").rstrip("/")
CODE_LENGTH = 6
ALPHABET = string.ascii_letters + string.digits  # a-z, A-Z, 0-9 = 62 символа
MAX_URL_LENGTH = 2048

# Имя контейнера: внутри Docker hostname = id контейнера.
# По нему видно, какая реплика ответила (для демонстрации балансировки).
INSTANCE = os.getenv("INSTANCE_NAME") or socket.gethostname()

STATIC_DIR = Path(__file__).parent / "static"


# ---------- Работа с БД ----------

def get_conn():
    """Открывает соединение с общей БД. Строки возвращаются как словари."""
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def ensure_table():
    """Создаёт таблицу, если её ещё нет (на случай запуска без init.sql)."""
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS links (
                id           SERIAL PRIMARY KEY,
                short_code   VARCHAR(16)  NOT NULL UNIQUE,
                original_url TEXT         NOT NULL,
                created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            )
            """
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_table()
    yield


app = FastAPI(title="link-service", lifespan=lifespan)


@app.middleware("http")
async def add_instance_header(request, call_next):
    """Добавляет к каждому ответу заголовок с именем реплики."""
    response = await call_next(request)
    response.headers["X-Served-By"] = INSTANCE
    return response


# ---------- Модели запросов/ответов ----------

class LinkCreate(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("URL не может быть пустым")
        if len(value) > MAX_URL_LENGTH:
            raise ValueError(f"URL длиннее {MAX_URL_LENGTH} символов")

        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL должен начинаться с http:// или https://")
        host = parsed.hostname or ""
        if not host or ("." not in host and host != "localhost"):
            raise ValueError("В URL нет корректного домена")
        return value


class LinkOut(BaseModel):
    short_code: str
    short_url: str
    original_url: str
    created_at: datetime


def to_out(row: dict) -> LinkOut:
    return LinkOut(
        short_code=row["short_code"],
        short_url=f"{BASE_URL}/{row['short_code']}",
        original_url=row["original_url"],
        created_at=row["created_at"],
    )


# ---------- Генерация кода ----------

def generate_code() -> str:
    """Случайный код из 6 символов. secrets — криптостойкий генератор,
    поэтому коды нельзя угадать подряд."""
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


# ---------- Эндпоинты ----------

@app.post("/api/links", status_code=201)
def create_link(body: LinkCreate):
    with get_conn() as conn:
        # Пытаемся вставить код; если такой уже есть (ON CONFLICT),
        # вставка ничего не вернёт — генерируем новый и пробуем снова.
        for _ in range(10):
            code = generate_code()
            row = conn.execute(
                """
                INSERT INTO links (short_code, original_url)
                VALUES (%s, %s)
                ON CONFLICT (short_code) DO NOTHING
                RETURNING short_code, original_url, created_at
                """,
                (code, body.url),
            ).fetchone()
            if row:
                return {**to_out(row).model_dump(), "served_by": INSTANCE}

    raise HTTPException(500, "Не удалось сгенерировать уникальный код")


@app.get("/api/links")
def list_links():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT short_code, original_url, created_at "
            "FROM links ORDER BY created_at DESC"
        ).fetchall()
    return {
        "served_by": INSTANCE,
        "count": len(rows),
        "links": [to_out(r).model_dump() for r in rows],
    }


@app.delete("/api/links/{code}", status_code=204)
def delete_link(code: str):
    with get_conn() as conn:
        deleted = conn.execute(
            "DELETE FROM links WHERE short_code = %s RETURNING id", (code,)
        ).fetchone()
    if not deleted:
        raise HTTPException(404, "Ссылка не найдена")
    return Response(status_code=204)


@app.get("/api/health")
def health():
    with get_conn() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "served_by": INSTANCE}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
