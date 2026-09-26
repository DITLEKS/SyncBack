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

- **Unit-тесты** (`tests/unit`) проверяют конкретные инварианты безопасности: пароль хранится только как солёный bcrypt-хэш, JWT всегда имеет срок жизни, presigned URL никогда не бессрочный, rate limiter изолирует попытки по email, а `_process_source` корректно возвращает `..._NOT_FOUND` вместо падения, если job/document/source удалены между постановкой в очередь и выполнением.
- **Integration-тесты** (`tests/integration`) гоняют реальный Postgres/Redis/MinIO через `AsyncClient` поверх ASGI-приложения. Fixture `_isolated_redis_client` (autouse, `tests/integration/conftest.py`) сбрасывает глобальный Redis-синглтон между тестами, чтобы асинхронное соединение не оказывалось привязанным к закрытому event loop предыдущего теста.
- CI (`.github/workflows/ci.yml`) запускает оба набора автоматически: `lint-and-test` (ruff + unit) и отдельный job `integration-tests` с Postgres/Redis как service containers и MinIO через `docker run`.

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
| `projects` | Проекты — единица группировки | владелец через `owner_id` |
| `documents` | Целевые документы (doc/docx/txt/markdown) | M:N с `sources`, ссылка на последний `analysis_job` |
| `sources` | Источники истины (file/note/link), переиспользуемые | M:N с `documents` через `document_sources` |
| `document_sources` | Связка документ↔источник | — |
| `analysis_jobs` | Запуски анализа (pending/processing/success/failed/cancelled) | 1:N `suggestions` |
| `suggestions` | Точечные правки (add/modify/delete) | `source_reference`/`confidence_score`/`explanation` |
| `audit_logs` | Журнал действий (accept/reject/download/finalize) | `suggestion_id` или `document_id` (CHECK-constraint `ck_audit_logs_target`) |

Роли: `admin` (видит всё) и `user` (только свои проекты). Точка расширения — `project_members`.

Миграции: `0001_initial_schema` → `0002_audit_logs_download` → `0003_audit_logs_columns` → `0004_suggestion_id_nullable`. `alembic/env.py` использует `transaction_per_migration=True` — PostgreSQL требует коммита нового значения enum перед использованием в CHECK constraint.

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
5. `finalize_analysis_job` агрегирует результат: `SUCCESS` если хотя бы один источник дал правки; `FAILED` с кодом `ALL_SOURCES_FAILED` или `NO_SOURCES_ATTACHED` иначе. Кэш `plain_text` очищается в `finally`.
6. **chord on_error**: при падении Celery backend (недоступен result store) `_chord_error_handler` форсирует финализацию с пустым списком результатов — документ переходит в `FAILED/draft` вместо вечного `in_progress`.
7. **Recovery при ошибке коммита финализации**: компенсирующая транзакция переводит job → `FAILED`, документ → `draft`. Если recovery-коммит тоже падает — вторичное исключение пробрасывается с `__cause__`, чтобы Celery применил retry/dead-letter (не поглощает ошибку).
8. **None-guard'ы**: все три этапа проверяют job/document/source на `None`. Различаются `JOB_NOT_FOUND` («не существует в БД» — `logger.error`) от «job уже в финальном статусе» («race» — `logger.info`).

### Зависимости воркера (M-8)

`_get_storage()`, `_get_connector()`, `_get_llm_client()`, `_get_parser_registry()` — `@functools.cache` provider-функции вместо module-level синглтонов. Runtime-семантика не изменилась (один объект на процесс). В тестах:

```python
import app.workers.tasks.analysis_tasks as t
t._get_storage   = lambda: FakeStorage()
t._get_connector = lambda: FakeConnector()
t._get_llm_client = lambda: FakeLLMClient()
```

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
│       ├── 0002_audit_logs_download.py
│       ├── 0003_audit_logs_columns.py
│       └── 0004_audit_logs_suggestion_id_nullable.py
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
├── unit/{test_app_startup,test_body_size_limit_middleware,test_jwt_handler,
│        test_login_rate_limiter,test_minio_presigned_url_ttl,test_password_hasher,
│        test_analysis_tasks_missing_records}.py
└── integration/{conftest,test_upload_rollback,test_analysis_job_queue_failure}.py
```
