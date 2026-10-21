# url-shortener-microservices

Сокращатель ссылок: 2 микросервиса, Docker, nginx (балансировщик), PostgreSQL (общая БД).

| Модуль | Протокол | Статус |
|---|---|---|
| `link-service` — создание ссылок | REST (HTTP/JSON) | готов |
| `redirect-service` — редирект и статистика | gRPC + WebSocket | в работе |

## Запуск

```bash
docker compose up --build
```

Открыть http://localhost — страница с формой.

## API link-service

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/api/links` | тело `{"url": "https://..."}` → короткий код |
| GET | `/api/links` | список всех ссылок |
| DELETE | `/api/links/{code}` | удалить ссылку (204, или 404 если нет) |
| GET | `/api/health` | проверка сервиса и БД |

```bash
curl -X POST localhost/api/links -H "Content-Type: application/json" -d '{"url":"https://github.com"}'
curl localhost/api/links
curl -X DELETE localhost/api/links/aB3xK9
```

## Проверка балансировки

```bash
for i in 1 2 3 4 5 6; do curl -s localhost/api/health; echo; done
```

Поле `served_by` (и заголовок `X-Served-By`) по очереди меняется: `link-service-1` → `-2` → `-3` → `-1` …

## Структура

```
├── docker-compose.yml        # всё вместе: БД, 3 реплики, nginx
├── db/init.sql               # таблицы общей БД (links, clicks)
├── nginx/nginx.conf          # балансировщик
└── link-service/
    ├── Dockerfile
    ├── requirements.txt
    └── app/
        ├── main.py           # REST API
        └── static/index.html # страница с формой
```
