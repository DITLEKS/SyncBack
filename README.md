# SyncScribe Backend

**SyncScribe** — B2B SaaS-инструмент автоматического обновления технической документации на основе источников истины (release notes, код, Jira, Confluence, транскрибации созвонов). Продукт находит смысловые различия между документом и источником, предлагает точечные правки (добавить/изменить/удалить), а пользователь подтверждает или отклоняет каждую правку.

Целевые пользователи: технические писатели, solution/implementation engineers, presale-инженеры в B2B IT/SaaS/ИБ-компаниях.

Этот README описывает backend MVP, собранный за 7 этапов разработки.

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

Ключевой принцип: **зависимости направлены внутрь** — `domain` не знает о FastAPI, SQLAlchemy или Celery. Все внешние системы (LLM-провайдер, источники истины, файловое хранилище, парсер документов, экспортёр) подключены через абстрактные интерфейсы (`Protocol`) в `domain/interfaces`, что позволяет менять конкретную реализацию (например, LLM-провайдера) без правок бизнес-логики.

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

docker compose up --build
docker compose exec backend alembic upgrade head

curl http://localhost:8000/health
```

Все сервисы поднимаются одной командой: `backend` (FastAPI), `worker` (Celery), `postgres`, `redis`, `minio` + `minio-init` (создаёт приватный бакет автоматически).

## Переменные окружения (`.env`)

| Группа | Переменные | Назначение |
|---|---|---|
| БД | `DATABASE_URL` | Строка подключения PostgreSQL (async, `postgresql+asyncpg://`) |
| Redis | `REDIS_URL` | Брокер и result backend Celery, кэш rate limiting |
| Minio | `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKET`, `MINIO_SECURE`, `MINIO_PRESIGNED_URL_EXPIRE_SECONDS` | Файловое хранилище документов и источников |
| JWT | `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Подпись и срок жизни токенов доступа |
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
| `audit_logs` | Журнал действий (accept/reject/download) | по `suggestion_id` или `document_id` |

Роли: только `admin` (видит и модифицирует всё) и `user` (только свои проекты через `owner_id`). Точка расширения на будущий `project_members` — единая функция `get_allowed_project`.

## API — сводка эндпоинтов (префикс `/api/v1`)

```
POST   /auth/register
POST   /auth/login
GET    /auth/me

POST   /projects
GET    /projects
GET    /projects/{project_id}

POST   /projects/{project_id}/documents                          (multipart, upload)
GET    /projects/{project_id}/documents
GET    /projects/{project_id}/documents/{document_id}
GET    /projects/{project_id}/documents/{document_id}/download   (presigned URL)
GET    /projects/{project_id}/documents/{document_id}/export     (финальный файл с правками)
POST   /projects/{project_id}/documents/{document_id}/sources    (привязка источников)

POST   /projects/{project_id}/sources                            (note/link)
POST   /projects/{project_id}/sources/file                       (multipart, upload)
GET    /projects/{project_id}/sources

POST   /projects/{project_id}/documents/{document_id}/analysis-jobs
GET    /projects/{project_id}/documents/{document_id}/analysis-jobs/{job_id}

GET    /projects/{project_id}/documents/{document_id}/suggestions
POST   /projects/{project_id}/documents/{document_id}/suggestions/{suggestion_id}/accept
POST   /projects/{project_id}/documents/{document_id}/suggestions/{suggestion_id}/reject
POST   /projects/{project_id}/documents/{document_id}/suggestions/bulk-accept

GET    /system/llm-health                                         (диагностика провайдера)
GET    /health                                                    (без префикса /api/v1)
```

## Пайплайн анализа (Celery)

1. `POST /analysis-jobs` создаёт запись `AnalysisJob` (status=`pending`) и ставит задачу `run_analysis_job` в очередь.
2. `run_analysis_job` переводит job/документ в `processing` и запускает по одной под-задаче `process_source_for_analysis_job` на каждый привязанный источник (через Celery `group`/`chord`).
3. Каждая под-задача независимо парсит документ, получает текст источника через `SourceConnector`, вызывает `LLMClient.generate_suggestions()` и сохраняет правки.
4. **Retry/dead-letter — по каждому источнику отдельно**: при сбое LLM/парсинга под-задача ретраится с экспоненциальной задержкой (`LLM_TIMEOUT_SECONDS × 2^retries`) до `LLM_MAX_RETRIES` раз; после исчерпания попыток запись уходит в Redis-список `syncscribe:analysis:dead_letter`, а под-задача возвращает "мягкий" отказ — сбой одного источника не блокирует обработку остальных.
5. `finalize_analysis_job` агрегирует результат: `SUCCESS`, если хотя бы один источник дал правки (детали по неудавшимся — в `error_message`); `FAILED` с кодом `ALL_SOURCES_FAILED` или `NO_SOURCES_ATTACHED` в остальных случаях.

## Абстракции и точки расширения

| Порт (`domain/interfaces`) | Единственная MVP-реализация | Назначение расширения |
|---|---|---|
| `LLMClient` | `StubLLMClient`, `HttpLLMClient`, `OnPremLLMClient` | Смена провайдера инференса без правок пайплайна (`LLM_PROVIDER` в `.env`) |
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
- Приватный Minio-бакет; скачивание документа — через presigned URL с TTL; экспорт финального документа — потоково через backend (результат не хранится как отдельный артефакт).
- Структурированные логи без секретов (пароли/токены исключены на уровне форматтера); каждое действие с правками и каждое скачивание/экспорт пишутся в `audit_logs`.
- Общий exception handler для доменных ошибок — исключает утечку внутренних деталей (стектрейсов) в ответах API.

## Осознанные упрощения MVP (зафиксированные ограничения)

- **Версионирование не хранится**: только текущее состояние документа + результат последнего анализа.
- **Роли внутри проекта не введены**: `project_members`/shared-доступ — точка расширения на будущее, авторизация уже вынесена в одну функцию под это.
- **Применение правок — без посимвольного diff**: текстовая замена `old_text → new_text` для txt/markdown; для docx — замена текста всего абзаца, содержащего `old_text` (внутреннее форматирование изменённого абзаца не сохраняется). `diff_match_patch` как вспомогательный сигнал позиционирования — возможное развитие, не блокирующая зависимость пайплайна.
- **Источники типа "ссылка"**: контент по URL не парсится автоматически — передаётся в LLM только как текстовый адрес.
- **LLM-контракт** — плейсхолдер до выбора реального провайдера.

## Что не сделано (следующий этап — CI и безопасность)

- CI-пайплайн (ruff, тесты, сборка Docker-образов) — не реализован.
- Дополнительное укрепление безопасности сверх базового набора (ограничение размера тела запроса на уровне Gunicorn/Nginx, более детальный аудит доступа).

## Структура репозитория (полная)

```
syncscribe-backend/
├── docker/{backend,worker}.Dockerfile
├── docker-compose.yml
├── .env.example
├── pyproject.toml
├── alembic.ini
├── alembic/{env.py, script.py.mako, versions/0001_initial_schema.py}
└── app/
    ├── main.py
    ├── core/{config, logging_setup, correlation_middleware, dependencies}.py
    ├── api/
    │   ├── deps.py
    │   ├── schemas/{auth,project,document,source,analysis_job,suggestion}.py
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
```
</content>
