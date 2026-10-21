FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY proto ./proto
COPY app ./app

# Генерируем gRPC-код из .proto прямо при сборке образа
RUN python -m grpc_tools.protoc -I proto \
      --python_out=app --grpc_python_out=app proto/stats.proto

WORKDIR /srv/app

# Запуск не от root
RUN useradd --create-home appuser
USER appuser

# 8000 — HTTP (редирект, /stats/) и WebSocket (/ws/), 50051 — gRPC
EXPOSE 8000 50051

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
