# Лёгкий официальный образ Python
FROM python:3.12-slim

# Python не пишет .pyc и сразу выводит логи (видно в docker logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Сначала только зависимости — Docker закэширует этот слой,
# и при изменении кода пересборка будет быстрой
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Потом сам код
COPY app ./app

# Запуск не от root — хорошая практика
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
