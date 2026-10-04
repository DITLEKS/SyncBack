# SyncScribe Backend

**SyncScribe** — B2B SaaS-инструмент автоматического обновления технической документации на основе источников истины (release notes, код, Jira, Confluence, транскрибации созвонов). Продукт находит смысловые различия между документом и источником, предлагает точечные правки (добавить/изменить/удалить), а пользователь подтверждает или отклоняет каждую правку.

Целевые пользователи: технические писатели, solution/implementation engineers, presale-инженеры в B2B IT/SaaS/ИБ-компаниях.

---

## Архитектура

Backend построен по принципам чистой (hexagonal) архитектуры с чётким разделением слоёв:

```
app/
├── api/            — HTTP-слой (FastAPI роутеры, Pydantic-схемы, зависимости авторизации)
├── domain/         — бизнес-логика (сервисы), доменные исключения, порты (интерфейсы/Protocol)
├── infrastructure/ — реализации портов: БД (SQLAlchemy), Minio, Redis, LLM-клиенты,
│                     парсеры документов, экспортёры, security
├── workers/        — Celery: приложение, задачи пайплайна анализа, вспомогательные модули
└── core/           — конфигурация (Settings), логирование, DI-фабрики, middleware
```

Ключевой принцип: **зависимости направлены внутрь** — `domain` не знает о FastAPI, SQLAlchemy или Celery. Все внешние системы (LLM-провайдер, источники истины, файловое хранилище, парсер документа, экспортёр) подключены через абстрактные `Protocol`-интерфейсы в `domain/interfaces`, что позволяет менять конкретную реализацию без правок бизнес-логики.

Доменные сущности (`Document`, `Suggestion`, `AnalysisJob` и т.д.) аннотированы через `Protocol` (`DocumentProtocol`, `SuggestionProtocol`), а не через ORM-модели — сервисный слой не импортирует `infrastructure.*` ни при выполнении, ни под `TYPE_CHECKING`.

## Технологический стек

- **API**: FastAPI + Pydantic v2, Uvicorn/Gunicorn
- **БД**: PostgreSQL + SQLAlchemy (async) + Alembic
- **Очереди**: Celery + Redis (брокер и result backend)
- **Real-time**: SSE (Server-Sent Events) через Redis Pub/Sub (fallback: in-memory)
- **Файловое хранилище**: Minio (S3-совместимое, приватный бакет)
- **Аутентификация**: JWT (PyJWT) + bcrypt
- **Парсинг документов**: python-docx (docx), нативная обработка (txt/markdown)
- **LLM-интеграция**: httpx, конфигурируемый провайдер через `.env`

## Быстрый старт

```bash
cp .env.example .env
# при необходимости поправьте LLM_ENDPOINT/LLM_API_KEY — по умолчанию LLM_PROVIDER=stub,
# реальный внешний вызов не требуется для локальной разработки

# Базовый compose не публикует порты Postgres/Redis/MinIO на хост.
# Если нужен доступ к ним с машины разработчика — добавьте docker-compose.dev.yml:
#   docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
# ВАЖНО (Windows): при этом порт 5432 не должен быть занят другим PostgreSQL —
# конфликт приводит к asyncpg.InvalidPasswordError при подключении с хоста.

docker compose up --build
docker compose exec backend alembic upgrade head

curl http://localhost:8000/health
```

Все сервисы поднимаются одной командой: `backend` (FastAPI), `worker` (Celery), `postgres`, `redis`, `minio` + `minio-init` (создаёт приватный бакет автоматически).

Swagger-документация API доступна на `http://localhost:8000/docs` — удобно для ручного тестирования сценариев без ожидания фронтенда.

## Тестирование

```bash
docker compose exec backend pytest tests/unit -v
docker compose exec backend pytest tests/integration -v
docker compose exec backend ruff check .
```

### Unit-тесты (`tests/unit/`)

Все тесты без реальных внешних зависимостей (мок-объекты, `@functools.cache` override):

| Файл | Что проверяет |
|---|---|
| `test_app_startup.py` | Приложение стартует без исключений |
| `test_body_size_limit_middleware.py` | Middleware отклоняет запросы сверх `MAX_UPLOAD_SIZE_MB` |
| `test_jwt_handler.py` | JWT всегда имеет срок жизни; невалидные токены отклоняются |
| `test_login_rate_limiter.py` | Rate limiter изолирует попытки по email; блокировка снимается корректно |
| `test_minio_presigned_url_ttl.py` | Presigned URL никогда не бессрочный |
| `test_password_hasher.py` | Пароль хранится только как bcrypt-хэш; plain-text не проходит verify |
| `test_analysis_tasks_missing_records.py` | `_process_source` возвращает `…_NOT_FOUND` вместо падения при удалённых сущностях |
| `test_atomic_review.py` | Инварианты оптимистичного лока review (unit-уровень) |
| `test_contract_auth.py` | Контракт сервиса авторизации (мок репозитория) |
| `test_contract_projects_documents.py` | Контракт сервисов проектов и документов (мок) |
| `test_contract_suggestions_editor.py` | Контракт сервиса правок со стороны редактора (мок) |
| `test_document_list_service.py` | Пагинация и фильтрация списка документов |
| `test_document_status_lifecycle.py` | Переходы статусов документа (draft → in_progress → …) |
| `test_editor_aggregate.py` | Агрегат редактора (заглушки, скипнутые сценарии) |
| `test_iter_accepted_changes_pages.py` | Пагинированный итератор принятых правок |
| `test_status_transitions.py` | Полная матрица допустимых/недопустимых переходов статусов |
| `test_unit_of_work_abc.py` | ABC UoW: реализации обязаны переопределять все методы |
| `test_my_documents_sources.py` | Фильтрация источников в «Мои документы» |
| `test_source_document_m2m.py` | M:N привязка источников к документам |
| `test_source_repository_scope.py` | Изоляция источников по scope и владельцу |
| `test_source_service_batch.py` | Пакетные операции с источниками |
| `test_list_with_total_window_count.py` | Пагинатор с оконным COUNT |

### Integration-тесты (`tests/integration/`)

Гоняют реальный Postgres/Redis/MinIO через `AsyncClient` поверх ASGI-приложения. Fixture `_isolated_redis_client` (autouse, `tests/integration/conftest.py`) сбрасывает глобальный Redis-синглтон между тестами, чтобы асинхронное соединение не оказывалось привязанным к закрытому event loop предыдущего теста.

| Файл | Что проверяет |
|---|---|
| `test_upload_rollback.py` | Откат MinIO-загрузки при ошибке коммита БД |
| `test_analysis_job_lifecycle.py` | Жизненный цикл job: pending → processing → success/failed |
| `test_analysis_job_queue_failure.py` | Поведение при недоступности Celery result store |
| `test_atomic_review_concurrent.py` | Параллельные запросы review не ломают `review_version` |
| `test_atomic_review_pg.py` | Оптимистичный лок review на реальном Postgres (SELECT FOR UPDATE) |
| `test_integration_documents.py` | CRUD документов и привязка источников через HTTP |

### Guard-тесты и системные (`tests/`)

- `test_sources_guard.py` — guard-тест: источники корректно изолируются по владельцу
- `test_system_capabilities.py` — smoke: импорт всех ключевых модулей проходит без ошибок

CI (`.github/workflows/ci.yml`) запускает оба набора автоматически: `lint-and-test` (ruff + unit) и отдельный job `integration-tests` с Postgres/Redis как service containers и MinIO через `docker run`.

> **Тестирование воркер-слоя**: зависимости `_get_storage`, `_get_connector`, `_get_llm_client` в `analysis_tasks.py` оформлены как `@functools.cache` provider-функции — в тестах достаточно переопределить функцию (`t._get_storage = lambda: FakeStorage()`), SQLAlchemy-сессия и Celery-воркер не нужны.

## Переменные окружения (`.env`)

| Группа | Переменные | Назначение |
|---|---|---|
| БД | `DATABASE_URL` | Строка подключения PostgreSQL (async, `postgresql+asyncpg://`) |
| Redis | `REDIS_URL` | Брокер и result backend Celery, счётчики блокировки входа, refresh-токены |
| Rate limit | `RATE_LIMIT_STORAGE_URI` | Хранилище счётчиков slowapi: `memory://` для одного процесса, Redis при нескольких воркерах |
| Прокси | `TRUSTED_PROXY_HOSTS` | Адреса reverse proxy, чьим `X-Forwarded-For` можно верить; пусто — заголовок игнорируется, `*` запрещено |
| Redis SSE | `REDIS_SSE_CHANNEL` | Канал Redis Pub/Sub для SSE (опционально; при отсутствии — in-memory fallback) |
| Minio | `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_SECURE`, `MINIO_PRESIGNED_URL_EXPIRE_SECONDS` | Файловое хранилище. Приложение ходит под сервисным пользователем с правами на один бакет; `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` нужны только контейнерам MinIO и minio-init |
| JWT | `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Подпись и срок жизни токенов доступа. **`JWT_SECRET` должен быть ≥ 32 байт** для HS256 |
| Логин | `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS` | Защита от брутфорса (счётчик в Redis) |
| Загрузка | `MAX_UPLOAD_SIZE_MB` | Лимит размера файла (документ/источник) |
| LLM | `LLM_PROVIDER` (`stub`\|`remote_http`\|`onprem`), `LLM_ENDPOINT`, `LLM_API_KEY`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` | Выбор и настройка провайдера инференса |
| Логи | `LOG_LEVEL`, `LOG_FORMAT` (`json`\|`text`) | Структурированное логирование |

## Схема базы данных

| Таблица | Назначение | Ключевые связи |
|---|---|---|
| `users` | Пользователи, глобальная роль `admin`/`user` | 1:N `projects` (через `owner_id`) |
| `projects` | Проекты — единица группировки | владелец через `owner_id`, опциональное `description` |
| `documents` | Целевые документы (doc/docx/txt/markdown) | M:N с `sources`, ссылка на последний `analysis_job`; поля `review_version`, `opens_count`, `original_storage_key` |
| `sources` | Источники истины (file/note/link), переиспользуемые; поле `scope` — UI-метка (не ограничение) | M:N с `documents` через `document_sources` |
| `document_sources` | Связка документ↔источник | — |
| `document_blocks` | Структурные блоки документа (абзацы/заголовки) | 1:N `suggestions` через `block_id` (anchor) |
| `document_opens` | Трекинг последнего открытия документа пользователем; PK составной `(user_id, document_id)` | FK → `users`, `documents`; upsert `ON CONFLICT DO UPDATE SET last_opened_at` |
| `analysis_jobs` | Запуски анализа (pending/processing/success/partial_success/failed/cancelled) | 1:N `suggestions`; `idempotency_key` |
| `suggestions` | Точечные правки (add/modify/delete) | `block_id` (anchor), `source_reference`, `confidence_score`, `explanation` |
| `audit_logs` | Журнал действий (accept/reject/reset/download/finalize) | `suggestion_id` или `document_id` (CHECK-constraint `ck_audit_logs_target`) |
| `dashboard_snapshots` | Ежедневный снэпшот метрик пользователя; PK составной `(owner_id, snapshot_date)` | FK → `users`; поля `total_count`, `awaiting_count`, `relevance_percent`; `ON CONFLICT DO UPDATE` |

### Поле `sources.scope`

`scope` (`project` / `document`) — **UI-метка**, не инвариант. Она подсказывает, в каком контексте создан источник, но не ограничивает привязку: любой источник может быть подключён к произвольному числу документов одного проекта через `document_sources`. Реальный набор источников документа определяется исключительно записями в `document_sources`.

Роли: `admin` (видит всё) и `user` (только свои проекты). Точка расширения — `project_members`.

## API — сводка эндпоинтов (префикс `/api/v1`)

```
POST   /auth/register
POST   /auth/login
POST   /auth/refresh
POST   /auth/logout                                                (отзыв refresh-токена, 204)
GET    /auth/me

POST   /projects
GET    /projects                                                   (пагинация: ?limit=&offset=)
GET    /projects/{project_id}
DELETE /projects/{project_id}

POST   /projects/{project_id}/documents                           (multipart, upload)
GET    /projects/{project_id}/documents                           (пагинация: ?limit=&offset=)
GET    /projects/{project_id}/documents/{document_id}
DELETE /projects/{project_id}/documents/{document_id}
GET    /projects/{project_id}/documents/{document_id}/download    (presigned URL)
GET    /projects/{project_id}/documents/{document_id}/export      (финальный файл с правками)
POST   /projects/{project_id}/documents/{document_id}/sources     (привязка источников)

POST   /documents                                                  (multipart; project_id в form-data — загрузка из «Мои документы»)
GET    /documents                                                   (пагинация; фильтры: ?status=&outdated=&search=&sort_by=&sort_dir=)

POST   /projects/{project_id}/sources                             (note/link)
POST   /projects/{project_id}/sources/file                        (multipart, upload)
GET    /projects/{project_id}/sources                             (пагинация: ?limit=&offset=)
DELETE /projects/{project_id}/sources/{source_id}

POST   /projects/{project_id}/documents/{document_id}/analysis-jobs
GET    /projects/{project_id}/documents/{document_id}/analysis-jobs/{job_id}
DELETE /projects/{project_id}/documents/{document_id}/analysis-jobs/{job_id}   (отмена; 200 + AnalysisJobResponse)
POST   /projects/{project_id}/documents/analysis-jobs/bulk        (групповой запуск; опционально ?document_ids=[])

GET    /projects/{project_id}/documents/{document_id}/suggestions              (пагинация; ?status= фильтр)
GET    /projects/{project_id}/documents/{document_id}/suggestions/{suggestion_id}
PATCH  /projects/{project_id}/documents/{document_id}/suggestions              (единый endpoint: single/bulk accept/reject/reset)
PUT    /projects/{project_id}/documents/{document_id}/suggestions/review       (batch: If-Match / optimistic lock)
POST   /projects/{project_id}/documents/{document_id}/suggestions/{suggestion_id}/reset

GET    /projects/{project_id}/documents/{document_id}/editor                   (агрегат редактора; ?suggestions_limit=&suggestions_offset=)
POST   /projects/{project_id}/documents/{document_id}/editor/reset             (сброс анализа → AWAITING_APPROVAL; 200 + ResetResponse)

GET    /events/documents                                           (SSE: real-time статусы; ?document_ids=uuid1,uuid2,..., макс 50)

GET    /dashboard                                                  (статистика рабочего пространства + метрики виджетов)
GET    /documents/attention                                        (топ-4 документа awaiting_approval по кол-ву pending-правок)
GET    /documents/recent                                           (5 последних открытых документов текущего пользователя)
POST   /projects/{project_id}/documents/{document_id}/open        (трекинг открытия документа; 204 No Content)

GET    /system/llm-health                                          (диагностика провайдера, только admin)
GET    /health                                                      (без префикса /api/v1)
```

**Пагинация**: все list-эндпоинты принимают `limit` (по умолчанию 50, максимум 200) и `offset` (по умолчанию 0), возвращают `{"items": [...], "total": N, "limit": L, "offset": O}` (схема `Page[T]`). Срезка выполняется на уровне SQL.

**PATCH /suggestions** — единственный endpoint изменения статуса правок. Принимает ровно одно из полей-селекторов:
- `ids` — список UUID для точечного обновления;
- `filter` — предустановленный фильтр: `pending` / `decided` / `all`.

Допустимые переходы: `pending → accepted`, `pending → rejected`, `decided → pending` (сброс).

**Оптимистичный лок review**: `PUT /suggestions/review` поддерживает заголовок `If-Match: <review_version>` — при конфликте версий возвращает `412 Precondition Failed`; без заголовка (legacy) — `409 Conflict`.

**Отмена задачи анализа**: `DELETE /analysis-jobs/{job_id}` — REST-правильный способ отмены (C-1). Возвращает `200 OK` + `AnalysisJobResponse` со статусом `cancelled`. Устаревший `POST .../cancel` удалён.

**SSE (`GET /events/documents`)**: клиент подключается как `EventSource` и получает события:
- `document_status_changed` — `{document_id, status, pending_suggestions}`
- `attention_count_changed` — `{count}` (кол-во AWAITING_APPROVAL)
- `dashboard_stats_changed` — `{total, awaiting, ready, relevance_percent}`
- `ping` — keepalive каждые 25 с

По умолчанию используется `RedisPubSubBroker` — события от воркеров доставляются всем подключённым клиентам независимо от инстанса. При недоступности Redis — автоматический fallback на `InMemorySSEBroker` (single-instance). Передача `?document_ids=` свыше 50 ID возвращает `422`.

**Bulk analysis jobs** (`POST /projects/{project_id}/documents/analysis-jobs/bulk`):
- Тело (опционально): `{"document_ids": ["uuid1", "uuid2"]}`. Без тела или при `document_ids=null` — запускает анализ для всех analyzable документов проекта.
- `201 Created` если `started > 0`; `200 OK` если все пропущены.

**Editor aggregate** (`GET /editor`): возвращает мета-данные документа, контент, `original_content` (для статусов `awaiting_approval` / `ready`), пагинированные правки, счётчики и объект `permissions`.

Query-параметры пагинации правок:
- `suggestions_limit` (default=50, max=200) — размер страницы;
- `suggestions_offset` (default=0) — смещение.

Поле `suggestions_total` в ответе — полный счётчик правок документа (для пагинатора фронта). Счётчики `pending`/`accepted`/`rejected` вычисляются O(1) агрегатным SQL-запросом, не O(n) проходом по текущей странице.

Поля `permissions`:
- `can_analyze` — доступно только из `draft` и `ready`. Из `awaiting_approval` фронт не показывает кнопку запуска анализа — документ уже содержит актуальные правки; для повторного запуска сначала используйте `POST /editor/reset`.

  > **NOTE (продуктовый вопрос)**: сервисный слой (`_ANALYSIS_ALLOWED_STATUSES` в `analysis_job_service.py`) по-прежнему принимает `awaiting_approval` напрямую через `POST /analysis-jobs`, обходя проверку `can_analyze`. Стоит решить: закрыть это на уровне сервиса (убрать `AWAITING_APPROVAL` из `_ANALYSIS_ALLOWED_STATUSES`), чтобы прямой вызов API также возвращал `409`. Это вопрос требований безопасности API, а не текущий баг.

- `can_review` — только `awaiting_approval`.
- `can_export` — только `ready`.
- `can_delete` — всё кроме `in_progress`.
- `sources_is_editable` — недоступно только при `in_progress` и `awaiting_approval`. Статусы `error` и `cancelled` трактуются как редактируемые (анализ не запущен, источники менять разрешено — аналогично `draft`).
- `view_mode` — режим отображения контента: `original` (draft/in_progress/error/cancelled), `suggested` (awaiting_approval), `clean` (ready).

**POST /editor/reset** — сброс анализа документа обратно в `awaiting_approval`. Возвращает `200 OK` с телом `ResetResponse`:
```json
{
  "document_status": "awaiting_approval",
  "review_version": 3,
  "suggestions_reset_count": 12
}
```
Фронт обновляет стор без дополнительного `GET /editor`.

**Dashboard** (`GET /dashboard`): возвращает агрегаты рабочего пространства + статистику виджетов в одном ответе (ранее был разделён на `/dashboard` и `/dashboard/stats` — объединён в OPT-D1). Дополнительные эндпоинты:
- `GET /documents/attention` — топ-4 документа со статусом `awaiting_approval`, отсортированные по кол-ву `pending`-правок.
- `GET /documents/recent` — 5 последних открытых текущим пользователем документов (по `last_opened_at` из таблицы `document_opens`).
- `POST /projects/{project_id}/documents/{document_id}/open` — трекинг открытия; upsert в `document_opens`; возвращает `204 No Content`.

**OpenAPI-схема**: `response_model` для list-эндпоинтов указывает на конкретный алиас `PageSuggestionResponse = Page[SuggestionResponse]`, разрешённый при определении класса — FastAPI корректно строит схему без runtime-introspection generic alias.

## Жизненный цикл документа

Документ имеет шесть статусов: `draft` → `in_progress` → `awaiting_approval` / `ready` / `error` / `cancelled`.

- После загрузки — `draft`.
- После успешной постановки задачи в Celery — `in_progress`.
- Успешный анализ с правками — `awaiting_approval`; без правок — `ready`.
- Ошибка — `error`; отмена — `cancelled`. Оба статуса **не разрешают прямой повторный запуск анализа** (`can_analyze=false`). Воркер автоматически переводит документ в `draft` при финализации с ошибкой/отменой — после чего кнопка анализа снова доступна.
- Из `awaiting_approval` в `ready` — только через `PUT /suggestions/review` с `finalize=true` или `PATCH /suggestions` при отсутствии `pending`-правок.
- Из `awaiting_approval` / `ready` — откат через `POST /editor/reset` (возвращает в `awaiting_approval`, сбрасывает правки, инкрементирует `review_version`).
- Из `awaiting_approval` — повторный запуск анализа **недоступен через UI** (`can_analyze=false`). Для пересчёта правок используйте `POST /editor/reset`, после чего документ переходит в `awaiting_approval`, а затем можно запустить новый анализ из этого статуса через прямой вызов `POST /analysis-jobs` (если `AWAITING_APPROVAL` не будет убран из `_ANALYSIS_ALLOWED_STATUSES` — см. NOTE выше).

Для одного документа разрешена только одна активная задача (`pending` или `processing`). Ограничение обеспечено сервисом и частичным уникальным индексом PostgreSQL.

## Пайплайн анализа (Celery)

1. `POST /analysis-jobs` создаёт `AnalysisJob` (status=`pending`) и ставит `run_analysis_job` в очередь.
2. `run_analysis_job` (`_start_job`) переводит job/документ в `processing`, скачивает и парсит документ **один раз** (parse-once), кэширует `plain_text` в Redis с TTL `max(llm_timeout × sources_count × 2, 300)` сек., затем запускает `chord` из `process_source_for_analysis_job` по одному на каждый источник.
3. Каждая под-задача читает `plain_text` из Redis-кэша (при cache miss — деградирует до прямого скачивания из MinIO), получает текст источника через `SourceConnector`, вызывает `LLMClient.generate_suggestions()` и сохраняет правки.
4. **Retry / dead-letter — по каждому источнику отдельно**: при сбое LLM/парсинга под-задача ретраится с экспоненциальной задержкой (`LLM_TIMEOUT_SECONDS × 2^retries`) до `LLM_MAX_RETRIES` раз; после исчерпания — запись уходит в Redis-список `syncscribe:analysis:dead_letter`.
5. `finalize_analysis_job` агрегирует результат: `SUCCESS` если все источники дали правки; `PARTIAL_SUCCESS` если часть; `FAILED` с кодом `ALL_SOURCES_FAILED` или `NO_SOURCES_ATTACHED` иначе. Кэш `plain_text` очищается в `finally`.
6. **chord on_error**: при падении Celery backend (недоступен result store) `_chord_error_handler` форсирует финализацию с пустым списком результатов — документ переходит в `FAILED/error` вместо вечного `in_progress`.
7. **Recovery при ошибке коммита финализации**: компенсирующая транзакция переводит job → `FAILED`, документ → `error`. Если recovery-коммит тоже падает — вторичное исключение пробрасывается с `__cause__`, чтобы Celery применил retry/dead-letter (не поглощает ошибку).
8. **None-guard'ы**: все три этапа проверяют job/document/source на `None`. Различаются `JOB_NOT_FOUND` («не существует в БД» — `logger.error`) от «job уже в финальном статусе» («race» — `logger.info`).

### Зависимости воркера

`_get_storage()`, `_get_connector()`, `_get_llm_client()`, `_get_parser_registry()` — `@functools.cache` provider-функции вместо module-level синглтонов. Runtime-семантика не изменилась (один объект на процесс). В тестах:

```python
import app.workers.tasks.analysis_tasks as t

t._get_storage = lambda: FakeStorage()
t._get_connector = lambda: FakeConnector()
t._get_llm_client = lambda: FakeLLMClient()
```

## Доменные исключения

Каждое исключение соответствует одной бизнес-ситуации и конвертируется в HTTP-ответ в роутере:

| Исключение | HTTP | Когда |
|---|---|---|
| `DocumentNotFoundError` | 404 | Документ не существует или не в проекте |
| `SuggestionNotFoundError` | 404 | Правка не найдена в БД |
| `StaleSuggestionJobError` | 404 | Правка существует, но принадлежит устаревшему job (документ переанализирован) |
| `JobNotFoundError` | — | job_id в воркере не найден в БД (не «завершён», а «отсутствует») |
| `SuggestionAlreadyDecidedError` | 409 | Race condition: правка уже обработана другим запросом |
| `SuggestionResetNotAllowedError` | 409 | Сброс правки невозможен в текущем состоянии |
| `InvalidDocumentStatusError` | 409 | Операция недопустима для текущего статуса документа |
| `OptimisticLockError` / `ReviewVersionConflictError` | 409 / 412 | Optimistic lock: `review_version` изменился параллельным запросом |
| `AnalysisAlreadyRunningError` | 409 | Для документа уже есть активный analysis job |
| `ReviewNotCompleteError` | 422 | Финализация невозможна: остались `pending`-правки или экспорт не удался |

## Абстракции и точки расширения

| Порт (`domain/interfaces`) | MVP-реализации | Назначение расширения |
|---|---|---|
| `LLMClientProtocol` | `StubLLMClient`, `HttpLLMClient`, `OnPremLLMClient` | Смена провайдера инференса без правок пайплайна (`LLM_PROVIDER` в `.env`) |
| `SourceConnectorProtocol` | `ManualUploadConnector` | Будущие `ConfluenceConnector`, `JiraConnector` и т.д. |
| `DocumentParserProtocol` | `TxtParser`, `MarkdownParser`, `DocxParser` | Новые форматы документов |
| `DocumentExporterProtocol` | `TextExporter`, `DocxExporter` | Новые форматы на экспорт |
| `FileStorageProtocol` | `MinioStorage` | Смена хранилища файлов |
| `ISSEBroker` | `RedisPubSubBroker`, `InMemorySSEBroker` | Смена транспорта real-time уведомлений |
| `DocumentProtocol` / `SuggestionProtocol` | ORM-модели (через `Protocol`) | Сервисный слой не зависит от SQLAlchemy напрямую |

`HttpLLMClient` — generic-клиент для любого внешнего HTTP-провайдера, настраиваемый только через `.env`. `OnPremLLMClient` реализован независимо (у on-prem может быть иной контракт запроса/ответа), общая между ними только retry-логика (`HttpConnectionRetryMixin`). Контракт в `infrastructure/llm/schemas.py` — **условный плейсхолдер** до выбора реального провайдера.

## Безопасность

- Пароли — только bcrypt-хэш, plain-text не хранится и не логируется.
- JWT с обязательным сроком жизни (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`).
- Rate limiting логина: счётчик неудачных попыток по email в Redis, блокировка после `LOGIN_MAX_ATTEMPTS`.
- Валидация всех входящих запросов через Pydantic-схемы.
- Авторизация на основе роли и владения проектом — единая точка `get_allowed_project`.
- Приватный Minio-бакет; скачивание — через presigned URL с TTL; экспорт финального файла — потоково через backend.
- Структурированные логи без секретов — редактирование рекурсивно обходит вложенные dict/list.
- `X-Request-ID` санитизируется по безопасному шаблону — произвольное значение не попадает в ответ напрямую.
- Общий exception handler для доменных ошибок — исключает утечку стектрейсов в API.
- `user_id` в `audit_log` — всегда реальный UUID из DI, заглушка `00000000-…` устранена.
- Все поля `extra={"job_id": ...}` в structured logging явно приводятся к `str()` — JSON-сериализатор не падает на `uuid.UUID`.

## Осознанные упрощения MVP (зафиксированные ограничения)

- **Версионирование не хранится**: только текущее состояние документа + результат последнего анализа.
- **Роли внутри проекта не введены**: `project_members`/shared-доступ — точка расширения.
- **Применение правок — без посимвольного diff**: замена `old_text → new_text` для txt/markdown; для docx — замена текста абзаца. При пересекающихся правках в одном абзаце вторая может не найти `old_text` — известное ограничение, не блокирует MVP.
- **Источники типа «ссылка»**: контент по URL не парсится — передаётся в LLM как текстовый адрес.
- **LLM-контракт** — плейсхолдер до выбора провайдера; сейчас `StubLLMClient` (фиктивная правка).
- **`DocumentRepository.attach_sources`** — чтение-мёрж-запись без атомарности; при параллельных вызовах возможен lost update. Низкий риск, зафиксирован как тех.долг.
- **Движок СУБД в Celery-воркере**: `isolated_uow()` создаёт новый `AsyncEngine` на каждый вызов под-задачи — корректно для event loop, но TCP/TLS handshake на каждый источник; при росте нагрузки стоит рассмотреть пул на уровне воркер-процесса.
- **`audit_logs` хранятся в основной PostgreSQL**: таблица растёт только вширь (записи не удаляются) и имеет иной профиль доступа, чем бизнес-данные (редкое чтение диапазонами vs. частое точечное чтение). На текущем масштабе это приемлемо. При росте нагрузки или появлении требований к retention-политике стоит рассмотреть вынос в отдельную схему, TimescaleDB или ClickHouse. До тех пор рекомендуется добавить партиционирование таблицы по `created_at`.
- **SSE in-memory fallback**: `InMemorySSEBroker` работает только с single-instance деплоем; в multi-instance без Redis события между инстансами не доставляются.

## Структура репозитория

```
SyncBack/
├── docker/{backend,worker}.Dockerfile
├── docker-compose.yml
├── .env.example
├── pyproject.toml
├── alembic.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_initial_schema.py
│       ├── 0002a_audit_logs_download.py
│       ├── 0002b_p0_3_document_blocks.py
│       ├── 0003_audit_logs_columns.py
│       ├── 0004a_audit_logs_suggestion_id_nullable.py
│       ├── 0004b_p0_review_version_block_id.py
│       ├── 0005_project_description.py
│       ├── 0006_document_status_lifecycle.py
│       ├── 0007_idempotency_key_partial_success.py
│       ├── 0008_source_scope.py
│       ├── 0009_document_review_version.py
│       ├── 0010_document_opens.py
│       ├── 0011_document_original_storage_key.py
│       ├── 0012_analysis_job_status_partial_success.py
│       ├── 0013_document_blocks_and_suggestion_anchors.py
│       ├── 0014_keyset_indexes.py
│       ├── 0015_suggestion_job_status_index.py
│       ├── 0016_schema_review_fixes.py
│       ├── 0017_r1_r2_r3_r4_r5_r6_schema_cleanup.py
│       ├── 0018_drop_text_content_from_sources.py
│       ├── 0018_n1_n2_n4_n5_n6_n7_n8_fixes.py
│       ├── 0019_add_indexes_opt4_opt5.py
│       └── 0020_drop_error_cancelled_analysis_allowed.py
└── app/
    ├── main.py
    ├── core/{config,logging_setup,correlation_middleware,body_size_limit_middleware,dependencies}.py
    ├── api/
    │   ├── deps.py
    │   ├── upload_utils.py
    │   ├── schemas/{auth,project,document,source,analysis_job,suggestion,review,editor,pagination}.py
    │   └── v1/routers/
    │       ├── auth.py
    │       ├── projects.py
    │       ├── documents.py
    │       ├── documents_global.py      ← POST /documents (загрузка из «Мои документы»)
    │       ├── my_documents.py          ← GET /documents (список всех документов пользователя)
    │       ├── sources.py
    │       ├── analysis_jobs.py
    │       ├── analysis_jobs_bulk.py    ← POST /documents/analysis-jobs/bulk
    │       ├── suggestions.py
    │       ├── editor.py                ← GET+POST /editor (агрегат + сброс)
    │       ├── sse.py                   ← GET /events/documents (SSE)
    │       ├── dashboard.py             ← GET /dashboard, /documents/attention, /documents/recent, POST .../open
    │       └── system.py
    ├── domain/
    │   ├── exceptions.py
    │   ├── value_objects.py
    │   ├── interfaces/{entities,file_storage,llm_client,source_connector,
    │   │              document_parser,document_exporter,unit_of_work}.py
    │   └── services/{auth,project,document,source,audit_log,analysis_job,
    │                 suggestion,document_export,dashboard}_service.py
    ├── infrastructure/
    │   ├── db/{base,session,models/*,repositories/*}.py
    │   ├── security/{password_hasher,jwt_handler,login_rate_limiter}.py
    │   ├── cache/{redis_client,sync_redis_client}.py
    │   ├── storage/minio_storage.py
    │   ├── parsers/{txt,markdown,docx}_parser.py + parser_registry.py
    │   ├── exporters/{text,docx}_exporter.py + exporter_registry.py
    │   ├── source_connectors/manual_upload_connector.py
    │   ├── llm/{schemas,http_retry_mixin,http_llm_client,on_prem_client,stub_client,factory}.py
    │   └── queue/dead_letter_store.py
    └── workers/
        ├── celery_app.py
        ├── pipeline/{llm_prompt_builder,suggestion_mapper}.py
        └── tasks/analysis_tasks.py

tests/
├── conftest.py
├── test_sources_guard.py
├── test_system_capabilities.py
├── unit/
│   ├── test_app_startup.py
│   ├── test_atomic_review.py
│   ├── test_body_size_limit_middleware.py
│   ├── test_contract_auth.py
│   ├── test_contract_projects_documents.py
│   ├── test_contract_suggestions_editor.py
│   ├── test_document_list_service.py
│   ├── test_document_status_lifecycle.py
│   ├── test_editor_aggregate.py
│   ├── test_iter_accepted_changes_pages.py
│   ├── test_jwt_handler.py
│   ├── test_login_rate_limiter.py
│   ├── test_minio_presigned_url_ttl.py
│   ├── test_my_documents_sources.py
│   ├── test_password_hasher.py
│   ├── test_analysis_tasks_missing_records.py
│   ├── test_source_document_m2m.py
│   ├── test_source_repository_scope.py
│   ├── test_source_service_batch.py
│   ├── test_list_with_total_window_count.py
│   ├── test_status_transitions.py
│   └── test_unit_of_work_abc.py
└── integration/
    ├── conftest.py
    ├── test_analysis_job_lifecycle.py
    ├── test_analysis_job_queue_failure.py
    ├── test_atomic_review_concurrent.py
    ├── test_atomic_review_pg.py
    ├── test_integration_documents.py
    └── test_upload_rollback.py
```
