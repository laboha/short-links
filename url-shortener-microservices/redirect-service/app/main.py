"""Модуль 2: редирект + статистика (HTTP, gRPC, WebSocket).

В одном контейнере работают:
  * HTTP :8000   — GET /{code} (302-редирект), страница /stats/, REST-шлюз /stats/api/*
  * WS   :8000   — /ws/{code} и /ws/all — живой счётчик переходов
  * gRPC :50051  — StatsService (GetStats, GetTopLinks)

Синхронизация между репликами: клик пишется в БД и делается pg_notify,
каждая реплика держит LISTEN и рассылает событие своим WebSocket-клиентам.
Поэтому неважно, какая реплика обработала редирект, а какая держит сокет.
"""
import asyncio
import json
import logging
import os
import re
import socket
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import grpc
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import db
import grpc_server
import stats_pb2
import stats_pb2_grpc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("redirect")

REPLICA = os.getenv("REPLICA_NAME") or socket.gethostname()
GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))
# Куда ходит REST-шлюз за статистикой. По умолчанию — через nginx,
# чтобы gRPC-запросы тоже балансировались между репликами.
GRPC_TARGET = os.getenv("GRPC_TARGET", "nginx:50051")
CODE_RE = re.compile(r"^[A-Za-z0-9]{6}$")
STATS_PAGE = (Path(__file__).parent / "static" / "stats.html").read_text(encoding="utf-8")


# ---------------- WebSocket-хаб ----------------
class Hub:
    def __init__(self) -> None:
        self.subs: dict[str, set[WebSocket]] = defaultdict(set)

    def add(self, key: str, ws: WebSocket) -> None:
        self.subs[key].add(ws)

    def remove(self, key: str, ws: WebSocket) -> None:
        self.subs[key].discard(ws)
        if not self.subs[key]:
            self.subs.pop(key, None)

    async def broadcast(self, msg: dict) -> None:
        targets = [("all", ws) for ws in self.subs.get("all", ())]
        targets += [(msg["code"], ws) for ws in self.subs.get(msg["code"], ())]
        for key, ws in targets:
            try:
                await ws.send_json(msg)
            except Exception:  # noqa: BLE001 — клиент отвалился
                self.remove(key, ws)


hub = Hub()


def on_notify(_conn, _pid, _channel, payload: str) -> None:
    msg = json.loads(payload)
    msg["ws_replica"] = REPLICA  # какая реплика отправила событие в сокет
    asyncio.get_running_loop().create_task(hub.broadcast(msg))


# ---------------- жизненный цикл ----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    listen_conn = await asyncpg.connect(db.DSN)
    await listen_conn.add_listener(db.NOTIFY_CHANNEL, on_notify)
    grpc_srv = await grpc_server.start(GRPC_PORT)
    app.state.channel = grpc.aio.insecure_channel(GRPC_TARGET)
    app.state.stub = stats_pb2_grpc.StatsServiceStub(app.state.channel)
    log.info("Реплика %s запущена, gRPC-шлюз -> %s", REPLICA, GRPC_TARGET)
    yield
    await app.state.channel.close()
    await grpc_srv.stop(grace=2)
    await listen_conn.close()
    await db.close()


app = FastAPI(title="Redirect & Stats service", lifespan=lifespan)


@app.middleware("http")
async def add_replica_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Served-By"] = REPLICA
    return response


# ---------------- служебное ----------------
@app.get("/healthz")
async def healthz():
    return {"status": "ok", "replica": REPLICA}


# ---------------- статистика (страница + REST-шлюз к gRPC) ----------------
@app.get("/stats/", response_class=HTMLResponse)
async def stats_page():
    return STATS_PAGE


@app.get("/stats/api/top")
async def stats_top(request: Request, n: int = 10):
    reply = await request.app.state.stub.GetTopLinks(stats_pb2.TopRequest(n=n), timeout=5)
    return {
        "links": [{"code": l.code, "original_url": l.original_url, "clicks": l.clicks} for l in reply.links],
        "grpc_replica": reply.replica,
        "http_replica": REPLICA,
    }


@app.get("/stats/api/{code}")
async def stats_one(code: str, request: Request):
    try:
        reply = await request.app.state.stub.GetStats(stats_pb2.StatsRequest(code=code), timeout=5)
    except grpc.aio.AioRpcError as e:
        if e.code() == grpc.StatusCode.NOT_FOUND:
            return JSONResponse({"detail": e.details()}, status_code=404)
        raise HTTPException(502, f"gRPC: {e.code().name}: {e.details()}")
    return {
        "code": reply.code,
        "original_url": reply.original_url,
        "total_clicks": reply.total_clicks,
        "last_click": reply.last_click or None,
        "by_day": [{"day": d.day, "clicks": d.clicks} for d in reply.by_day],
        "grpc_replica": reply.replica,
        "http_replica": REPLICA,
    }


# ---------------- WebSocket ----------------
@app.websocket("/ws/{key}")
async def ws_endpoint(ws: WebSocket, key: str):
    """key = короткий код (счётчик одной ссылки) или 'all' (все переходы)."""
    if key != "all" and not CODE_RE.fullmatch(key):
        await ws.close(code=1008)
        return
    await ws.accept()
    hub.add(key, ws)
    log.info("[%s] WS подписка на %s (всего: %s)", REPLICA, key, len(hub.subs.get(key, ())))
    try:
        if key != "all":
            await ws.send_json({
                "type": "init", "code": key,
                "total": await db.count_clicks(key), "ws_replica": REPLICA,
            })
        while True:  # держим соединение; клиент может слать ping
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(key, ws)


# ---------------- редирект (объявлен последним!) ----------------
@app.get("/{code}")
async def redirect(code: str, request: Request):
    if not CODE_RE.fullmatch(code):
        raise HTTPException(404, "Неверный короткий код")
    link = await db.get_link(code)
    if link is None:
        raise HTTPException(404, "Ссылка не найдена")
    if link["expires_at"] and link["expires_at"] < db.utcnow():
        raise HTTPException(410, "Срок действия ссылки истёк")

    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "")
    ua = request.headers.get("user-agent", "")
    total = await db.register_click(code, ua, ip, REPLICA)
    log.info("[%s] redirect %s -> %s (кликов: %s)", REPLICA, code, link["original_url"], total)

    return RedirectResponse(
        link["original_url"], status_code=302,
        headers={"Cache-Control": "no-store"},  # чтобы браузер не кэшировал и каждый переход считался
    )
