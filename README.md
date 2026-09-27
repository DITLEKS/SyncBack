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
- **Файловое хранилище**: Minio (S3-совместимое, приватный бакет)
- **Аутентификация**: JWT (PyJWT) + bcrypt (passlib)
- **Парсинг документов**: python-docx (docx), нативная обработка (txt/markdown)
- **LLM-интеграция**: httpx, конфигурируемый провайдер через `.env`

## Быстрый старт

```bash
cp .env.example .env
# при необходимости поправьте LLM_ENDPOINT/LLM_API_KEY — по умолчанию LLM_PROVIDER=stub,
# реальный внешний вызов не требуется для локальной разработки

# ВАЖНО (Windows): убедитесь, что порт 5432 не занят другим PostgreSQL-сервисом
# (`netstat -ano | findstr :5432`) — конфликт приводит к asyncpg.InvalidPasswordError при
# подключении с хоста.

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
| Redis | `REDIS_URL` | Брокер и result backend Celery, кэш rate limiting |
| Minio | `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKET`, `MINIO_SECURE`, `MINIO_PRESIGNED_URL_EXPIRE_SECONDS` | Файловое хранилище документов и источников |
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
| `sources` | Источники истины (file/note/link), переиспользуемые; поле `scope` | M:N с `documents` через `document_sources` |
| `document_sources` | Связка документ↔источник | — |
| `document_blocks` | Структурные блоки документа (абзацы/заголовки) | 1:N `suggestions` через `block_id` (anchor) |
| `analysis_jobs` | Запуски анализа (pending/processing/success/partial_success/failed/cancelled) | 1:N `suggestions`; `idempotency_key` |
| `suggestions` | Точечные правки (add/modify/delete) | `block_id` (anchor), `source_reference`, `confidence_score`, `explanation` |
| `audit_logs` | Журнал действий (accept/reject/download/finalize) | `suggestion_id` или `document_id` (CHECK-constraint `ck_audit_logs_target`) |

Роли: `admin` (видит всё) и `user` (только свои проекты). Точка расширения — `project_members`.

### Цепочка миграций Alembic

```
0001_initial_schema
0002a_audit_logs_download
0002b_p0_3_document_blocks
0003_audit_logs_columns
0004a_audit_logs_suggestion_id_nullable
0004b_p0_review_version_block_id
0005_project_description
0006_document_status_lifecycle
0007_idempotency_key_partial_success
0008_source_scope
0009_document_review_version
0010_document_opens
0011_document_original_storage_key
0012_analysis_job_status_partial_success
0013_document_blocks_and_suggestion_anchors
0014_keyset_indexes
0015_suggestion_job_status_index
0016_schema_review_fixes
0017_r1_r2_r3_r4_r5_r6_schema_cleanup
0018_drop_text_content_from_sources + 0018_n1_n2_n4_n5_n6_n7_n8_fixes
0019_add_indexes_opt4_opt5          ← OPT-4/OPT-5: индекс по suggestions.document_id + GIN pg_trgm
```

`alembic/env.py` использует `transaction_per_migration=True` — PostgreSQL требует коммита нового значения enum перед использованием в CHECK constraint.

> **Применение последней миграции** (индексы OPT-4/5):
> ```bash
> # требуется расширение pg_trgm (один раз на БД):
> # psql -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
> alembic upgrade 0019
> ```
> Индекс по `pg_trgm` создаётся через `CONCURRENTLY` — не блокирует таблицу в продакшне.

## API — сводка эндпоинтов (префикс `/api/v1`)

```
POST   /auth/register
POST   /auth/login
GET    /auth/me

POST   /projects
GET    /projects                                                  (пагинация: ?limit=&offset=)
GET    /projects/{project_id}

POST   /projects/{project_id}/documents                          (multipart, upload)
GET    /projects/{project_id}/documents                          (пагинация: ?limit=&offset=)
GET    /projects/{project_id}/documents/{document_id}
GET    /projects/{project_id}/documents/{document_id}/download   (presigned URL)
GET    /projects/{project_id}/documents/{document_id}/export     (финальный файл с правками)
POST   /projects/{project_id}/documents/{document_id}/sources    (привязка источников)

POST   /projects/{project_id}/sources                            (note/link)
POST   /projects/{project_id}/sources/file                       (multipart, upload)
GET    /projects/{project_id}/sources                            (пагинация: ?limit=&offset=)

POST   /projects/{project_id}/documents/{document_id}/analysis-jobs
GET    /projects/{project_id}/documents/{document_id}/analysis-jobs/{job_id}
POST   /projects/{project_id}/documents/{document_id}/analysis-jobs/{job_id}/cancel

GET    /projects/{project_id}/documents/{document_id}/suggestions                        (пагинация: ?limit=&offset=)
PUT    /projects/{project_id}/documents/{document_id}/suggestions/review                 (bulk: If-Match / optimistic lock)
POST   /projects/{project_id}/documents/{document_id}/suggestions/{suggestion_id}/accept
POST   /projects/{project_id}/documents/{document_id}/suggestions/{suggestion_id}/reject
POST   /projects/{project_id}/documents/{document_id}/suggestions/bulk-accept
POST   /projects/{project_id}/documents/{document_id}/suggestions/finalize

GET    /workspace/dashboard                                       (статистика рабочего пространства)
GET    /system/llm-health                                         (диагностика провайдера)
GET    /health                                                    (без префикса /api/v1)
```

**Пагинация**: все list-эндпоинты принимают `limit` (по умолчанию 50, максимум 200) и `offset` (по умолчанию 0), возвращают `{"items": [...], "total": N, "limit": L, "offset": O}` (схема `Page[T]`). Срезка выполняется на уровне SQL.

**Оптимистичный лок review**: `PUT /suggestions/review` поддерживает заголовок `If-Match: <review_version>` — при конфликте версий возвращает `412 Precondition Failed`; без заголовка (legacy) — `409 Conflict`.

**OpenAPI-схема**: `response_model` для list-эндпоинтов указывает на конкретный алиас `PageSuggestionResponse = Page[SuggestionResponse]`, разрешённый при определении класса — FastAPI корректно строит схему без runtime-introspection generic alias.

## Жизненный цикл документа

Документ имеет четыре пользовательских статуса: `draft` → `in_progress` → `awaiting_approval` / `ready`.

- После загрузки — `draft`.
- После успешной постановки задачи в Celery — `in_progress`.
- Успешный анализ с правками — `awaiting_approval`; без правок — `ready`.
- Ошибка или отмена — обратно в `draft`.
- Из `awaiting_approval` в `ready` — только через `POST .../suggestions/finalize` при отсутствии `pending`-правок.

Для одного документа разрешена только одна активная задача (`pending` или `processing`). Ограничение обеспечено сервисом и частичным уникальным индексом PostgreSQL.

## Пайплайн анализа (Celery)

1. `POST /analysis-jobs` создаёт `AnalysisJob` (status=`pending`) и ставит `run_analysis_job` в очередь.
2. `run_analysis_job` (`_start_job`) переводит job/документ в `processing`, скачивает и парсит документ **один раз** (parse-once), кэширует `plain_text` в Redis с TTL `max(llm_timeout × sources_count × 2, 300)` сек., затем запускает `chord` из `process_source_for_analysis_job` по одному на каждый источник.
3. Каждая под-задача читает `plain_text` из Redis-кэша (при cache miss — деградирует до прямого скачивания из MinIO), получает текст источника через `SourceConnector`, вызывает `LLMClient.generate_suggestions()` и сохраняет правки.
4. **Retry / dead-letter — по каждому источнику отдельно**: при сбое LLM/парсинга под-задача ретраится с экспоненциальной задержкой (`LLM_TIMEOUT_SECONDS × 2^retries`) до `LLM_MAX_RETRIES` раз; после исчерпания — запись уходит в Redis-список `syncscribe:analysis:dead_letter`.
5. `finalize_analysis_job` агрегирует результат: `SUCCESS` если все источники дали правки; `PARTIAL_SUCCESS` если часть; `FAILED` с кодом `ALL_SOURCES_FAILED` или `NO_SOURCES_ATTACHED` иначе. Кэш `plain_text` очищается в `finally`.
6. **chord on_error**: при падении Celery backend (недоступен result store) `_chord_error_handler` форсирует финализацию с пустым списком результатов — документ переходит в `FAILED/draft` вместо вечного `in_progress`.
7. **Recovery при ошибке коммита финализации**: компенсирующая транзакция переводит job → `FAILED`, документ → `draft`. Если recovery-коммит тоже падает — вторичное исключение пробрасывается с `__cause__`, чтобы Celery применил retry/dead-letter (не поглощает ошибку).
8. **None-guard'ы**: все три этапа проверяют job/document/source на `None`. Различаются `JOB_NOT_FOUND` («не существует в БД» — `logger.error`) от «job уже в финальном статусе» («race» — `logger.info`).

### Зависимости воркера (M-8)

`_get_storage()`, `_get_connector()`, `_get_llm_client()`, `_get_parser_registry()` — `@functools.cache` provider-функции вместо module-level синглтонов. Runtime-семантика не изменилась (один объект на процесс). В тестах:

```python
import app.workers.tasks.analysis_tasks as t
t._get_storage    = lambda: FakeStorage()
t._get_connector  = lambda: FakeConnector()
t._get_llm_client = lambda: FakeLLMClient()
```

## Оптимизации производительности (OPT-1 … OPT-5)

| # | Место | Суть |
|---|---|---|
| OPT-1 | `suggestion_repository.list_with_total` | Удалён мёртвый код `count_q`/`items_q`; `func.count().over()` корректно возвращает 0 на пустой выборке — 1 SELECT вместо 2 |
| OPT-2 | `dashboard_repository.get_recent_documents` | N+1 коррелированных `scalar_subquery()` заменены на `LEFT JOIN + COUNT FILTER + GROUP BY` — 1 запрос вместо N+1 |
| OPT-3 | `document_repository.list_all_for_user` | 2 round-trip заменены на CTE + `COUNT() OVER()` — PostgreSQL выполняет CTE однократно |
| OPT-4 | `alembic/versions/0019_add_indexes_opt4_opt5.py` | `CREATE INDEX ix_suggestions_document_id ON suggestions(document_id)` — ускоряет JOIN/EXISTS по `document_id` |
| OPT-5 | `alembic/versions/0019_add_indexes_opt4_opt5.py` | GIN-индекс `ix_documents_name_trgm` (расширение `pg_trgm`) — превращает `ILIKE '%…%'` из seq-scan в index-scan |

## Доменные исключения

Каждое исключение соответствует одной бизнес-ситуации и конвертируется в HTTP-ответ в роутере:

| Исключение | HTTP | Когда |
|---|---|---|
| `DocumentNotFoundError` | 404 | Документ не существует или не в проекте |
| `SuggestionNotFoundError` | 404 | Правка не найдена в БД вообще |
| `StaleSuggestionJobError` | 404 | Правка существует, но принадлежит устаревшему job (документ переанализирован) |
| `JobNotFoundError` | — | job_id в воркере не найден в БД (не «завершён», а «отсутствует») |
| `SuggestionAlreadyDecidedError` | 409 | Race condition: правка уже обработана другим запросом |
| `InvalidDocumentStatusError` | 409 | Операция недопустима для текущего статуса документа |
| `ReviewVersionConflictError` | 409 / 412 | Optimistic lock: `review_version` изменился параллельным запросом |
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
| `DocumentProtocol` / `SuggestionProtocol` | ORM-модели (через `Protocol`) | Сервисный слой не зависит от SQLAlchemy напрямую |

`HttpLLMClient` — generic-клиент для любого внешнего HTTP-провайдера, настраиваемый только через `.env`. `OnPremLLMClient` реализован независимо (у on-prem может быть иной контракт запроса/ответа), общая между ними только retry-логика (`HttpConnectionRetryMixin`). Контракт в `infrastructure/llm/schemas.py` — **условный плейсхолдер** до выбора реального провайдера.

## Безопасность

- Пароли — только bcrypt-хэш (passlib), plain-text не хранится и не логируется.
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
│       └── 0019_add_indexes_opt4_opt5.py
└── app/
    ├── main.py
    ├── core/{config,logging_setup,correlation_middleware,body_size_limit_middleware,dependencies}.py
    ├── api/
    │   ├── deps.py
    │   ├── upload_utils.py
    │   ├── schemas/{auth,project,document,source,analysis_job,suggestion,review,pagination}.py
    │   └── v1/routers/{auth,projects,documents,sources,analysis_jobs,suggestions,system,workspace}.py
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
│   ├── test_password_hasher.py
│   ├── test_analysis_tasks_missing_records.py
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
