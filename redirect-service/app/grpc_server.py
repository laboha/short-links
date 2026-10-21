"""gRPC-сервер статистики (StatsService из proto/stats.proto)."""
import logging
import os
import socket

import grpc
from grpc_reflection.v1alpha import reflection

import db
import stats_pb2
import stats_pb2_grpc

log = logging.getLogger("grpc")
REPLICA = os.getenv("REPLICA_NAME") or socket.gethostname()


class StatsService(stats_pb2_grpc.StatsServiceServicer):

    async def GetStats(self, request, context):
        code = request.code.strip()
        log.info("[%s] gRPC GetStats(%s)", REPLICA, code)
        s = await db.get_stats(code)
        if s is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"Ссылка {code} не найдена")
        return stats_pb2.StatsReply(
            code=code,
            original_url=s["original_url"],
            total_clicks=s["total"],
            last_click=s["last_click"].isoformat() if s["last_click"] else "",
            by_day=[stats_pb2.DayCount(day=d.isoformat(), clicks=c) for d, c in s["by_day"]],
            replica=REPLICA,
        )

    async def GetTopLinks(self, request, context):
        n = request.n if request.n > 0 else 10
        n = min(n, 100)
        log.info("[%s] gRPC GetTopLinks(%s)", REPLICA, n)
        rows = await db.top_links(n)
        return stats_pb2.TopReply(
            links=[
                stats_pb2.LinkStat(code=r["short_code"], original_url=r["original_url"], clicks=r["clicks"])
                for r in rows
            ],
            replica=REPLICA,
        )


async def start(port: int = 50051) -> grpc.aio.Server:
    server = grpc.aio.server()
    stats_pb2_grpc.add_StatsServiceServicer_to_server(StatsService(), server)
    # reflection — чтобы можно было дёргать сервис через grpcurl без .proto
    service_names = (
        stats_pb2.DESCRIPTOR.services_by_name["StatsService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    log.info("gRPC-сервер слушает порт %s", port)
    return server
