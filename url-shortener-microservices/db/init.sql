-- Общая (централизованная) БД для обоих модулей.
-- Выполняется автоматически при первом запуске контейнера postgres.

-- Модуль 1 (link-service) пишет сюда, модуль 2 (redirect-service) читает
CREATE TABLE IF NOT EXISTS links (
    id           SERIAL PRIMARY KEY,
    short_code   VARCHAR(16)  NOT NULL UNIQUE,
    original_url TEXT         NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Модуль 2 (redirect-service) записывает сюда каждый переход
CREATE TABLE IF NOT EXISTS clicks (
    id          SERIAL PRIMARY KEY,
    short_code  VARCHAR(16) NOT NULL REFERENCES links(short_code) ON DELETE CASCADE,
    clicked_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_agent  TEXT,
    ip          VARCHAR(64)
);

CREATE INDEX IF NOT EXISTS idx_clicks_code ON clicks(short_code);
