# url-shortener-microservices

Сокращатель ссылок: 2 микросервиса, Docker, nginx (балансировщик), PostgreSQL (общая БД).

| Модуль | Протоколы | Статус |
|---|---|---|
| `link-service` — создание ссылок | REST (HTTP/JSON) | готов |
| `redirect-service` — редирект и статистика | HTTP 302, gRPC, WebSocket | готов |

## Запуск

```bash
docker compose up --build
```

- http://localhost — форма создания ссылок (модуль 1)
- http://localhost/stats/ — статистика переходов в реальном времени (модуль 2)
- `localhost:50051` — gRPC StatsService (модуль 2)

## Маршрутизация (nginx)

| Путь | Куда | Балансировка |
|---|---|---|
| `/`, `/api/` | link-service ×3 | round-robin |
| `/{code}` | redirect-service ×3 | round-robin |
| `/stats/` | redirect-service ×3 | round-robin |
| `/ws/` | redirect-service ×3 | `ip_hash` + `Upgrade`/`Connection` |
| `:50051` (gRPC) | redirect-service ×3 | round-robin, HTTP/2 |

В каждом ответе есть заголовок `X-Served-By` с именем реплики. В `docker compose logs nginx` видно, на какой адрес ушёл каждый запрос.

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

## redirect-service

| Протокол | Где | Что делает |
|---|---|---|
| HTTP | `GET /{code}` | находит ссылку, пишет клик в `clicks`, отдаёт 302 на оригинал |
| gRPC | `stats.StatsService/GetStats` | клики всего, дата последнего перехода, клики по дням |
| gRPC | `stats.StatsService/GetTopLinks` | N самых популярных ссылок |
| WebSocket | `/ws/{code}`, `/ws/all` | при каждом переходе присылает новый счётчик |
| HTTP | `/stats/`, `/stats/api/{code}`, `/stats/api/top` | страница статистики и REST-шлюз к gRPC |

Описание gRPC — `redirect-service/proto/stats.proto`.

**Как счётчик доходит до WebSocket, если реплик три.** Переход может обработать `redirect-service-1`, а сокет браузера открыт на `redirect-service-3`. Поэтому после записи клика сервис делает `pg_notify('click_events', …)`. Каждая реплика слушает этот канал (`LISTEN`) и рассылает событие своим клиентам. Синхронизация идёт через общую БД.

```bash
# редиректы расходятся по репликам
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -D - localhost/<код> | grep -i x-served-by; done

# статистика через REST-шлюз (внутри — вызов gRPC через nginx)
curl localhost/stats/api/<код>
curl "localhost/stats/api/top?n=5"

# gRPC напрямую (grpcurl, reflection включён)
grpcurl -plaintext -d '{"code":"<код>"}' localhost:50051 stats.StatsService/GetStats
grpcurl -plaintext -d '{"n":5}'           localhost:50051 stats.StatsService/GetTopLinks
```

## Сценарий демонстрации

1. `docker compose up --build`, открыть http://localhost, сократить ссылку и получить код.
2. Открыть `http://localhost/stats/?code=<код>`. Индикатор WebSocket горит зелёным.
3. Несколько раз перейти по `http://localhost/<код>` из другой вкладки или запустить `curl` в цикле.
4. Цифра на странице растёт без перезагрузки. В блоке «События» видно, какая реплика сделала редирект и какая отправила событие в сокет.
5. `docker compose logs -f nginx`: запросы `/api/` и редиректы распределяются по разным репликам, WebSocket одного клиента держится на одной.

## Структура

```
├── docker-compose.yml        # всё вместе: БД, 3+3 реплики, nginx
├── db/init.sql               # таблицы общей БД (links, clicks)
├── nginx/nginx.conf          # балансировщик (HTTP, WebSocket, gRPC)
├── link-service/             # модуль 1 — REST
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       └── static/index.html
└── redirect-service/         # модуль 2 — редирект, gRPC, WebSocket
    ├── Dockerfile
    ├── requirements.txt
    ├── proto/stats.proto     # описание gRPC-сервиса
    └── app/
        ├── main.py           # редирект, WebSocket, /stats/, REST-шлюз к gRPC
        ├── grpc_server.py    # StatsService
        ├── db.py             # работа с PostgreSQL
        └── static/stats.html # страница со счётчиком
```
