# SyncScribe Backend

**SyncScribe** — B2B SaaS-инструмент автоматического обновления технической документации на основе источников истины (release notes, код, Jira, Confluence, транскрибации созвонов). Продукт находит смысловые различия между документом и источником, предлагает точечные правки (добавить/изменить/удалить), а пользователь подтверждает или отклоняет каждую правку.

Целевые пользователи: технические писатели, solution/implementation engineers, presale-инженеры в B2B IT/SaaS/ИБ-компаниях.

Этот README описывает backend MVP после нескольких раундов код-ревью (race conditions, пагинация, оптимизация запросов).

---

## Архитектура

Backend построен по принципам чистой (hexagonal) архитектуры с чётким разделением слоёв:

```
app/
├── api/            — HTTP-слой (FastAPI роутеры, Pydantic-схемы, зависимости авторизации)
├── domain/         — бизнес-логика (сервисы), доменные исключения, порты (интерфейсы)
├── infrastructure/ — реализации портов: БД (SQLAlchemy), Minio, Redis, LLM-клиенты,
│                     парсеры документов, экспортёры, security
├── workers/         — Celery: приложение, задачи пайплайна анализа, вспомогательные модули
└── core/           — конфигурация (Settings), логирование, DI-фабрики, middleware
```

Ключевой принцип: **зависимости направлены внутрь** — `domain` не знает о FastAPI, SQLAlchemy или Celery. Все внешние системы (LLM-провайдер, источники истины, файловое хранилище, парсер документа, экспортёр) подключены через абстрактные интерфейсы (`Protocol`) в `domain/interfaces`, что позволяет менять конкретную реализацию (например, LLM-провайдера) без правок бизнес-логики. Именно это позволит подключить реальный ИИ-пайплайн (LangGraph и т.д.) вместо `StubLLMClient`, не трогая API-контракт.

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
- **Integration-тесты** (`tests/integration`) гоняют реальный Postgres/Redis/MinIO через `AsyncClient` поверх ASGI-приложения. Fixture `_isolated_redis_client` (autouse, `tests/integration/conftest.py`) сбрасывает глобальный Redis-синглтон между тестами, чтобы асинхронное соединение не оказывалось привязанным к закрытому event loop предыдущего теста (актуально только для pytest-асинхронного окружения, в проде не проявляется).
- CI (`.github/workflows/ci.yml`) запускает оба набора автоматически: `lint-and-test` (ruff + unit) и отдельный job `integration-tests` с Postgres/Redis как service containers и MinIO через `docker run`.

## Переменные окружения (`.env`)

| Группа | Переменные | Назначение |
|---|---|---|
| БД | `DATABASE_URL` | Строка подключения PostgreSQL (async, `postgresql+asyncpg://`) |
| Redis | `REDIS_URL` | Брокер и result backend Celery, кэш rate limiting |
| Minio | `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKET`, `MINIO_SECURE`, `MINIO_PRESIGNED_URL_EXPIRE_SECONDS` | Файловое хранилище документов и источников |
| JWT | `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Подпись и срок жизни токенов доступа. **`JWT_SECRET` должен быть ≥ 32 байт** для HS256 — иначе PyJWT выдаёт `InsecureKeyLengthWarning` |
| Логин | `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS` | Защита от брутфорса (счётчик в Redis) |
| Загрузка | `MAX_UPLOAD_SIZE_MB` | Лимит размера файла (документ/источник) |
| LLM | `LLM_PROVIDER` (`stub`\|`remote_http`\|`onprem`), `LLM_ENDPOINT`, `LLM_API_KEY`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` | Выбор и настройка провайдера инференса |
| Логи | `LOG_LEVEL`, `LOG_FORMAT` (`json`\|`text`) | Структурированное логирование |

## Схема базы данных

| Таблица | Назначение | Ключевые связи |
|---|---|---|
| `users` | Пользователи, глобальная роль `admin`/`user` | 1:N `projects` (через `owner_id`) |
| `projects` | Проекты — единица группировки | владелец через `owner_id`, без `project_members` в MVP |
| `documents` | Целевые документы (doc/docx/txt/markdown) | M:N с `sources`, ссылка на последний `analysis_job` |
| `sources` | Источники истины (file/note/link), переиспользуемые | M:N с `documents` через `document_sources` |
| `document_sources` | Связка документ↔источник | — |
| `analysis_jobs` | Запуски анализа (pending/processing/success/failed) | 1:N `suggestions` |
| `suggestions` | Точечные правки (add/modify/delete) | заготовки `source_reference`/`confidence_score`/`explanation` под будущую верификацию |
| `audit_logs` | Журнал действий (accept/reject/download) | по `suggestion_id` или `document_id` (взаимно исключающие, оба nullable, корректная комбинация гарантируется CHECK-constraint `ck_audit_logs_target`) |

Роли: только `admin` (видит и модифицирует всё) и `user` (только свои проекты через `owner_id`). Точка расширения на будущий `project_members` — единая функция `get_allowed_project`.

Миграции (`alembic/versions`): `0001_initial_schema` → `0002_audit_logs_download` (добавляет `download` в `audit_action`) → `0003_audit_logs_columns` (добавляет `document_id` и CHECK-constraint) → `0004_suggestion_id_nullable` (снимает `NOT NULL` с `suggestion_id`, забытый в 0003). Важно: `alembic/env.py` использует `transaction_per_migration=True` — `0002` добавляет значение enum, а `0003` его сразу же использует в CHECK constraint — PostgreSQL требует коммита нового значения enum перед использованием, иначе — `UnsafeNewEnumValueUsageError`.

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

GET    /projects/{project_id}/documents/{document_id}/suggestions                        (пагинация: ?limit=&offset=)
POST   /projects/{project_id}/documents/{document_id}/suggestions/{suggestion_id}/accept
POST   /projects/{project_id}/documents/{document_id}/suggestions/{suggestion_id}/reject
POST   /projects/{project_id}/documents/{document_id}/suggestions/bulk-accept

GET    /system/llm-health                                         (диагностика провайдера)
GET    /health                                                    (без префикса /api/v1)
```

**Пагинация**: все list-эндпоинты (`GET /projects`, `/documents`, `/sources`, `/suggestions`) принимают запросные параметры `limit` (по умолчанию 50, максимум 200) и `offset` (по умолчанию 0), возвращая объект `{"items": [...], "total": N, "limit": L, "offset": O}` (схема `Page[T]` в `app/api/schemas/pagination.py`) вместо плоского списка — без этого объём ответа рос бы линейно без ограничения при росте числа документов/правок у клиента.

## Пайплайн анализа (Celery)

1. `POST /analysis-jobs` создаёт запись `AnalysisJob` (status=`pending`) и ставит задачу `run_analysis_job` в очередь.
2. `run_analysis_job` переводит job/документ в `processing` и запускает по одной под-задаче `process_source_for_analysis_job` на каждый привязанный источник (через Celery `group`/`chord`).
3. Каждая под-задача независимо парсит документ, получает текст источника через `SourceConnector`, вызывает `LLMClient.generate_suggestions()` и сохраняет правки.
4. **Retry/dead-letter — по каждому источнику отдельно**: при сбое LLM/парсинга под-задача ретраится с экспоненциальной задержкой (`LLM_TIMEOUT_SECONDS × 2^retries`) до `LLM_MAX_RETRIES` раз; после исчерпания попыток запись уходит в Redis-список `syncscribe:analysis:dead_letter`.
5. `finalize_analysis_job` агрегирует результат: `SUCCESS`, если хотя бы один источник дал правки; `FAILED` с кодом `ALL_SOURCES_FAILED` или `NO_SOURCES_ATTACHED` в остальных случаях. При гонке параллельных `analysis_jobs` на одном документе `document.current_analysis_job_id` обновляется только если завершающийся job действительно новее уже сохранённого текущего.
6. **None-guard'ы**: все три этапа (`_start_job`, `_process_source`, `_finalize_job`) проверяют job/document/source на `None` после `get_by_id` — если запись удалена между постановкой задачи в очередь и выполнением (или Celery повторно доставил задачу после `acks_late`), подзадача возвращает контролируемый `"failed"`-результат вместо `AttributeError`.

## Абстракции и точки расширения

| Порт (`domain/interfaces`) | Единственная MVP-реализация | Назначение расширения |
|---|---|---|
| `LLMClient` | `StubLLMClient`, `HttpLLMClient`, `OnPremLLMClient` | Смена провайдера инференса без правок пайплайна (`LLM_PROVIDER` в `.env`) — точка подключения реального ИИ-пайплайна (например, LangGraph) |
| `SourceConnector` | `ManualUploadConnector` | Будущие `ConfluenceConnector`, `JiraConnector` и т.д. |
| `DocumentParser` | `TxtParser`, `MarkdownParser`, `DocxParser` (через `DocumentParserRegistry`) | Новые форматы документов |
| `DocumentExporter` | `TextExporter`, `DocxExporter` (через `DocumentExporterRegistry`) | Новые форматы на экспорт |
| `FileStorage` | `MinioStorage` | Смена хранилища файлов |

**LLM-провайдер сознательно не привязан к вендору.** `HttpLLMClient` — generic-клиент для любого внешнего HTTP-провайдера, настраиваемого только через `.env`. `OnPremLLMClient` реализован независимо от `HttpLLMClient` (не наследует бизнес-контракт запроса/ответа — вероятно, у on-prem модели он будет другим), общая между ними только сетевая retry-логика (`HttpConnectionRetryMixin`). Контракт запроса/ответа в `infrastructure/llm/schemas.py` — **условный плейсхолдер**, не подтверждённая спецификация: при выборе реального провайдера правки нужны только там.

## Безопасность

- Пароли — только bcrypt-хэш (passlib), plain-text не хранится и не логируется.
- JWT с обязательным сроком жизни (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`).
- Rate limiting логина: счётчик неудачных попыток по email в Redis, блокировка после `LOGIN_MAX_ATTEMPTS`.
- Валидация всех входящих запросов через Pydantic-схемы.
- Авторизация на основе роли и владения проектом — единая точка `get_allowed_project`.
- Приватный Minio-бакет; скачивание документа — через presigned URL с TTL; экспорт финального документа — потоково через backend.
- Структурированные логи без секретов — редактирование рекурсивно обходит вложенные dict/list, а не только верхний уровень `extra`.
- `X-Request-ID` санитизируется по безопасному шаблону — произвольное входящее значение заголовка не попадает в ответ напрямую.
- Общий exception handler для доменных ошибок — исключает утечку внутренних деталей (стектрейсов) в ответах API.

## Осознанные упрощения MVP (зафиксированные ограничения)

- **Версионирование не хранится**: только текущее состояние документа + результат последнего анализа.
- **Роли внутри проекта не введены**: `project_members`/shared-доступ — точка расширения на будущее.
- **Применение правок — без посимвольного diff**: текстовая замена `old_text → new_text` для txt/markdown; для docx — замена текста всего абзаца. При пересекающихся правках в одном абзаце вторая может не найти свой `old_text` после применения первой — известное ограничение, не блокирующее MVP.
- **Источники типа "ссылка"**: контент по URL не парсится автоматически — передаётся в LLM только как текстовый адрес.
- **LLM-контракт** — плейсхолдер до выбора реального провайдера; сейчас — `StubLLMClient` (всегда одна и та же фиктивная правка) — следующий этап развития продукта.
- **`DocumentRepository.attach_sources`** — чтение-мёрж-запись без атомарности на уровне связующей таблицы — при параллельных вызовах для одного документа возможен lost update. Низкий риск при текущем объёме использования, зафиксирован как тех.долг.
- **Движок СУБД в Celery-воркере**: `isolated_db_session()` создаёт новый `AsyncEngine` на каждый вызов подзадачи — правильно для корректности event loop, но цена — TCP/TLS handshake на каждый источник; при росте нагрузки стоит рассмотреть пул на уровне воркер-процесса.

## Структура репозитория (полная)

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
    ├── core/{config, logging_setup, correlation_middleware, body_size_limit_middleware, dependencies}.py
    ├── api/
    │   ├── deps.py
    │   ├── upload_utils.py
    │   ├── schemas/{auth,project,document,source,analysis_job,suggestion,pagination}.py
    │   └── v1/routers/{auth,projects,documents,sources,analysis_jobs,suggestions,system}.py
    ├── domain/
    │   ├── exceptions.py
    │   ├── interfaces/{file_storage,llm_client,source_connector,document_parser,document_exporter}.py
    │   └── services/{auth,project,document,source,audit_log,analysis_job,suggestion,document_export}_service.py
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
