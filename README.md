# SyncScribe Backend

**SyncScribe** — B2B SaaS-инструмент обновления технической документации по источникам истины (release notes, заметки, файлы, ссылки). Сервис сравнивает документ с источниками через LLM, предлагает точечные правки (добавить / изменить / удалить), а пользователь принимает или отклоняет каждую и выгружает итоговый файл.

Целевые пользователи: технические писатели, solution/implementation и presale-инженеры B2B IT/SaaS/ИБ-компаний.

---

## Архитектура

```
app/
├── api/            — HTTP: роутеры FastAPI, Pydantic-схемы, зависимости авторизации
├── domain/         — сервисы сценариев, жизненные циклы, VO, доменные события и исключения, порты
├── infrastructure/ — адаптеры портов: PostgreSQL (SQLAlchemy), S3-хранилище, Redis, Celery, LLM,
│                     SSE, парсеры и экспортёры документов, security
├── workers/        — Celery-задачи пайплайна анализа
└── core/           — Settings, логирование, DI, middleware
```

- Зависимости направлены внутрь: `domain` не импортирует FastAPI, SQLAlchemy, Redis и `infrastructure.*`; внешние системы подключаются через порты в `domain/interfaces` (`IUnitOfWork`, репозитории, `FileStorage`, `AnalysisQueue`, `LLMClient`, `IEventPublisher` и др.).
- Сервисы работают с сущностями через `Protocol` (`DocumentProtocol`, `SuggestionProtocol`); ORM-модели остаются в инфраструктуре и на read-side. Импорт ORM-моделей в домене допускается только под `TYPE_CHECKING` как временный компромисс.
- Правила переходов статусов собраны в одном месте — `domain/lifecycle.py` (`DocumentLifecycle`, `AnalysisJobLifecycle`); сервисы, воркер и роутеры не держат своих наборов статусов.
- Границы слоёв проверяются в CI скриптами `scripts/check_layer_imports.py` (запрещённые импорты) и `scripts/check_layer_contracts.py` (вызовы несуществующих методов портов и неизвестные аргументы).

## Стек

- **API**: FastAPI + Pydantic v2, Uvicorn/Gunicorn, slowapi (rate limit)
- **БД**: PostgreSQL 16 + SQLAlchemy 2 (async, asyncpg) + Alembic
- **Очередь**: Celery + Redis (брокер и result backend)
- **Real-time**: SSE через Redis Pub/Sub (fallback: in-memory)
- **Хранилище**: SeaweedFS через S3-шлюз (приватный бакет); клиент — minio-py как универсальный S3-клиент
- **Аутентификация**: JWT (PyJWT, HS256) + bcrypt, refresh-токены с ротацией в Redis
- **Документы**: python-docx (docx), txt и markdown — нативно
- **LLM**: httpx, провайдер выбирается через `LLM_PROVIDER`

## Быстрый старт

```bash
cp .env.example .env            # заполните секреты; по умолчанию LLM_PROVIDER=stub
docker compose up --build
docker compose exec backend alembic upgrade head
curl http://localhost:8000/health
```

Compose поднимает `backend`, `worker`, `postgres`, `redis` и `seaweedfs`. SeaweedFS работает одним процессом (`weed mini`: master, volume, filer и S3-шлюз на 8333); при старте он сам создаёт бакет и единственного пользователя с правами только на этот бакет из `S3_*` в `.env` (`docker/seaweedfs-start.sh`), root-учётки и init-контейнера нет. Секреты обязательны: compose падает с ошибкой, если переменная из `${VAR:?}` не задана.

Базовый compose не публикует порты Postgres/Redis/SeaweedFS на хост. Для доступа с машины разработчика:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build   # порты на 127.0.0.1
```

Swagger — `http://localhost:8000/docs`.

## Проверки и тесты

```bash
ruff check app tests && ruff format --check app tests
python scripts/check_layer_imports.py app
python scripts/check_layer_contracts.py app
pytest -m "not integration" tests --cov=app    # unit + контрактные, без внешних сервисов
pytest -m integration tests/integration         # нужны PostgreSQL, Redis, SeaweedFS
mypy app
```

Тестов 343: 314 unit и контрактных (SQLite in-memory, fakeredis, in-memory хранилище) и 29 интеграционных (PostgreSQL, Redis, SeaweedFS). Порог покрытия в CI — 70%.

CI (`.github/workflows/ci.yml`): `lint-and-test` (ruff, проверки слоёв, mypy в режиме `continue-on-error`, pytest с покрытием), `check-migrations` (`alembic upgrade head` на чистой БД и `alembic check`), `build-images`, `integration-tests` (PostgreSQL и Redis как services, SeaweedFS — `docker run chrislusf/seaweedfs:4.48` с тем же стартовым скриптом, что и в compose).

## Переменные окружения

| Группа | Переменные | Назначение |
|---|---|---|
| Окружение | `ENV`, `DEBUG` | Профиль запуска; `DEBUG` в продакшене не включать |
| БД | `DATABASE_URL`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS`, `DB_POOL_RECYCLE_SECONDS` | `postgresql+asyncpg://…` и пул API-процесса. Суммарно `workers × (size + overflow)` должно укладываться в `max_connections` |
| Redis | `REDIS_URL`, `REDIS_SSE_CHANNEL` | Celery, блокировка входа, refresh-токены, кэш текста документа, канал SSE |
| Rate limit | `RATE_LIMIT_STORAGE_URI` | `memory://` для одного процесса; при нескольких — Redis (отдельная база) |
| Прокси | `TRUSTED_PROXY_HOSTS` | Чьим `X-Forwarded-For` верить; пусто — заголовок игнорируется, `*` запрещено |
| CORS | `CORS_ALLOWED_ORIGINS` | Список origin через запятую; `*` не используется (credentials включены) |
| Хранилище | `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_SECURE`, `S3_PRESIGNED_URL_EXPIRE_SECONDS` | Любой S3-совместимый endpoint (в compose — SeaweedFS `seaweedfs:8333`); те же переменные читает контейнер `seaweedfs`, чтобы создать бакет и пользователя. Старые имена `MINIO_*` принимаются как алиасы |
| JWT | `JWT_SECRET` (≥ 32 байт), `JWT_ALGORITHM`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | Подпись и срок жизни токенов |
| Вход | `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS` | Блокировка по паре (email, IP) |
| Загрузка | `MAX_UPLOAD_SIZE_MB` | Лимит файла документа или источника (по умолчанию 50) |
| LLM | `LLM_PROVIDER` (`stub` \| `remote_http` \| `onprem`), `LLM_ENDPOINT`, `LLM_API_KEY`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `LLM_MAX_INPUT_CHARS` | Провайдер, таймаут запроса, число повторов временных сбоев, лимит документа + источника в символах |
| Логи | `LOG_LEVEL`, `LOG_FORMAT` (`json` \| `text`) | Структурированные логи |

## API

Все эндпоинты, кроме `/health` и `/api/v1/system/capabilities`, под префиксом `/api/v1` и требуют `Authorization: Bearer <access_token>`. Доступ к проекту проверяется одной зависимостью `get_allowed_project`: `admin` видит всё, `user` — только свои проекты; чужой проект отвечает `404`, а не `403`.

```
POST   /auth/register                     5/мин на IP
POST   /auth/login                        20/мин на IP; блокировка после LOGIN_MAX_ATTEMPTS → 429
POST   /auth/refresh                      ротация refresh-токена; повторное использование → 401
POST   /auth/logout                       отзыв refresh-токена, 204
GET    /auth/me

POST   /projects
GET    /projects                          ?limit=&offset=
GET    /projects/{p}                      ?include=documents&include=sources (до 200 элементов)
PATCH  /projects/{p}
DELETE /projects/{p}                      204, каскадно удаляет документы и источники

POST   /documents                         multipart: file + project_id («Мои документы»)
GET    /documents                         все документы пользователя: ?status=&outdated=&search=&sort_by=&sort_dir=&limit=&offset=

POST   /projects/{p}/documents            multipart: file
GET    /projects/{p}/documents            ?status=&limit=&offset=
GET    /projects/{p}/documents/{d}
DELETE /projects/{p}/documents/{d}        204; нельзя во время анализа
GET    /projects/{p}/documents/{d}/content    разобранный текст и секции
GET    /projects/{p}/documents/{d}/download   presigned URL на исходник
GET    /projects/{p}/documents/{d}/export     итоговый файл; только ready, только исходный формат
POST   /projects/{p}/documents/{d}/sources    привязка источников к документу
POST   /projects/{p}/documents/{d}/open       отметка открытия для «Недавних», 204

POST   /projects/{p}/sources              ссылка (url)
POST   /projects/{p}/sources/note         текстовая заметка
POST   /projects/{p}/sources/file         multipart: file + name + scope [+ document_id]
GET    /projects/{p}/sources              ?scope=&limit=&offset=
DELETE /projects/{p}/sources/{s}          204

POST   /projects/{p}/documents/{d}/analysis-jobs          Idempotency-Key; тело {"force": true} для ready
GET    /projects/{p}/documents/{d}/analysis-jobs/{j}
DELETE /projects/{p}/documents/{d}/analysis-jobs/{j}      отмена → 200 + задача в cancelled
POST   /projects/{p}/documents/analysis-jobs/bulk         {"document_ids": [...]} или все подходящие

GET    /projects/{p}/documents/{d}/suggestions            ?status=&limit=&offset=
GET    /projects/{p}/documents/{d}/suggestions/{s}
PATCH  /projects/{p}/documents/{d}/suggestions            accept/reject/reset: {ids} или {filter: pending|decided|all}
PUT    /projects/{p}/documents/{d}/suggestions/review     пакетное сохранение ревью; If-Match: <review_version>; finalize
POST   /projects/{p}/documents/{d}/suggestions/{s}/reset

GET    /projects/{p}/documents/{d}/editor                 агрегат экрана; ?suggestions_limit=&suggestions_offset=
POST   /projects/{p}/documents/{d}/editor/reset           решения по правкам → pending, документ → awaiting_approval

GET    /dashboard                         статистика и тренды пользователя
GET    /documents/attention               до 4 документов awaiting_approval с наибольшим числом pending
GET    /documents/recent                  5 последних открытых
GET    /events/documents                  SSE; ?document_ids= (до 50 UUID)

GET    /system/llm-health                 только admin
GET    /system/capabilities               без авторизации: форматы и лимиты загрузки для UI
GET    /health                            без префикса
```

### Соглашения

- **Пагинация**: `limit` / `offset`, ответ `{"items": [...], "total", "limit", "offset"}`; срез выполняется в SQL.
- **Ошибки** отдаются как `{"detail": "..."}`. Основные коды: `404` — ресурс не найден или чужой; `409` — недопустимо в текущем статусе, анализ уже идёт, правка уже обработана, ревью не завершено; `412` — устаревший `If-Match`; `413` — файл или тело больше лимита; `415` — неподдерживаемый формат; `422` — валидация, невалидный пароль, документ не разбирается; `423` — источники заблокированы на время анализа и ревью; `429` — rate limit или блокировка входа. Необработанное `DomainError` → `400`.
- **Загрузка файлов**: тело ограничено `BodySizeLimitMiddleware` — по `Content-Length` сразу и по фактически прочитанным байтам для chunked-запросов (лимит файла + 1 МБ на служебные поля multipart). Файл не читается в память целиком: Starlette держит его во временном файле, хранилище получает поток. Точный лимит файла проверяется до записи в хранилище; ключ объекта — `projects/{p}/documents/{id}.ext` или `projects/{p}/sources/{id}.ext`, исходное имя хранится в БД и отдаётся через `Content-Disposition`.
- **Ревью**: `PUT /suggestions/review` атомарно сохраняет решения и инкрементирует `review_version` (CAS). При расхождении версии — `412`, если передан `If-Match`, иначе `409`. С `finalize=true` и без оставшихся `pending` документ переходит в `ready`; итоговый файл собирается при запросе `/export`. `PATCH /suggestions` статус документа не меняет.
- **Запуск анализа**: из `draft` и `awaiting_approval` — сразу; из `ready` — только с `force=true` (иначе `409`, прежнее ревью будет сброшено). Повторный запрос с тем же `Idempotency-Key` возвращает ту же задачу. На документ допускается одна активная задача.
- **Экспорт**: только для `ready` (иначе `409`) и только в исходном формате документа; другой `export_format` → `400`.
- **Редактор** (`GET /editor`): метаданные документа с `view_mode` (`original` / `suggested` / `clean`), содержимое, `original_content`, страница правок текущего анализа, `suggestions_total`, счётчики по статусам (агрегатным запросом) и `permissions` (`can_analyze`, `can_review`, `can_export`, `can_delete`, `sources_is_editable`) из `DocumentLifecycle`. Сбой чтения содержимого из хранилища отдаёт ошибку, а не пустой контент.
- **SSE** (`GET /events/documents`): событие `document_status_changed` `{document_id, project_id, status, current_analysis_job_id}` получает только владелец проекта; `ping` каждые 25 с. События публикуются после commit каждой смены статуса документа — из API и из воркера (через канал Redis). Невалидный или лишний `document_ids` → `422`. Без Redis брокер работает in-memory и события воркера не доходят.

## Жизненный цикл

**Документ** — 4 статуса:

```
draft ──анализ──▶ in_progress ──правки есть──▶ awaiting_approval ──ревью завершено──▶ ready
  ▲                    │ └────правок нет──────────────────────────────────────────────▶ │
  └──сбой / отмена─────┘                                                               │
awaiting_approval, ready ──повторный анализ──▶ draft → in_progress                      │
ready ──POST /editor/reset──▶ awaiting_approval ◀───────────────────────────────────────┘
```

Источники заблокированы в `in_progress` и `awaiting_approval`; удалить документ нельзя в `in_progress`.

**Задача анализа**: `pending` (создана) → `dispatched` (в очереди) → `processing` (воркер начал) → `success` / `partial_success` / `failed` / `cancelled`. Статус документа следует за его текущей задачей (`current_analysis_job_id`); завершение старой задачи документ не трогает. Если очередь недоступна, задача сразу становится `failed` (`QUEUE_UNAVAILABLE`), документ возвращается в `draft`.

## Пайплайн анализа

1. API создаёт задачу, переводит её в `dispatched` до отправки и ставит Celery chord: `process_source_for_analysis_job` на каждый источник документа и проекта → `finalize_analysis_job`.
2. Первая подзадача переводит задачу в `processing` (если её не успели отменить). Текст документа разбирается один раз и кэшируется в Redis на час (`parsed_doc:{job_id}`).
3. Подзадача получает текст источника (файл из хранилища или URL с защитой от SSRF), вызывает LLM и сохраняет правки, если задача всё ещё активна.
4. Промпт (`infrastructure/llm/prompt.py`) отделяет инструкцию от данных: документ и источник обёрнуты в `<document>` / `<source>`, совпадающие теги внутри текста экранируются. Если документ + источник длиннее `LLM_MAX_INPUT_CHARS`, источник завершается ошибкой `LLM_INPUT_TOO_LARGE` без обрезки.
5. HTTP-клиент LLM повторяет обрыв соединения, таймаут, `429` и `5xx` до `LLM_MAX_RETRIES` раз с задержкой 1, 2, 4… с (до 30 с, `Retry-After` важнее); прочие `4xx` не повторяются.
6. Ошибки подзадач: `LLM_INPUT_TOO_LARGE`, `LLM_UNAVAILABLE`, `LLM_INVALID_RESPONSE`, `DOCUMENT_PARSE_ERROR`, `GENERATION_ERROR`.
7. Финализация: все источники успешны — `success`, часть — `partial_success`, ни одного — `failed`; уже завершённая (например, отменённая) задача не перезаписывается. Документ: `awaiting_approval` при наличии `pending`-правок, иначе `ready`; при `failed` / `cancelled` — `draft`. После commit публикуется SSE-событие.

## База данных

PostgreSQL, миграции Alembic (`alembic/versions`, одна голова — `0025`). CI применяет миграции на чистой БД и сверяет схему с моделями (`alembic check`).

| Таблица | Назначение | Ключевое |
|---|---|---|
| `users` | Пользователи, роль `admin` / `user` | уникальный `email` |
| `projects` | Проекты пользователя | `owner_id` → users (CASCADE) |
| `documents` | Документы проекта (`docx`, `txt`, `markdown`; `doc` в enum, но не разбирается) | `status`, `current_analysis_job_id` → analysis_jobs (SET NULL), `review_version`, `storage_key`, `original_storage_key`, `exported_storage_key`; trigram-индекс по `name` для поиска |
| `sources` | Источники: `file` (файл или заметка) и `url` | `scope` (`project` / `document`), CHECK согласованности `type` и `storage_key` / `url` |
| `document_sources` | M:N документ ↔ источник | составной PK |
| `analysis_jobs` | Запуски анализа | частичный уникальный индекс «одна активная задача на документ» (`pending`, `dispatched`, `processing`); уникальность `(document_id, idempotency_key)`; `error_code`, `error_message`, `partial_success` |
| `suggestions` | Правки анализа | `analysis_job_id`, `document_id`, `change_type`, `status`, `original_text`, `suggested_text`, `decided_by` / `decided_at`; индексы по (job, status) и (document, status) |
| `audit_logs` | Журнал действий: accept/reject/bulk/reset/finalize/download | ссылка на `suggestion_id` или `document_id` (CHECK) |
| `document_opens` | Последнее открытие документа пользователем | PK `(user_id, document_id)`, upsert |
| `dashboard_snapshots` | Ежедневные метрики пользователя для трендов | уникальность `(owner_id, snapshot_date)`, upsert |
| `document_blocks` | Структурные блоки документа | таблица есть в схеме, приложением пока не заполняется |

Нюансы:

- Связь `documents` ↔ `analysis_jobs` взаимная (`analysis_jobs.document_id` и `documents.current_analysis_job_id`), поэтому SQLAlchemy предупреждает о цикле при сортировке таблиц — это ожидаемо.
- Счётчики правок в списках и редакторе считаются только по текущему анализу документа; правки прежних запусков остаются в БД, но не показываются.
- `sources.scope` — метка контекста создания; реальный набор источников документа задаётся `document_sources` плюс источники проекта со `scope=project`.
- API использует пул соединений (`AsyncAdaptedQueuePool` с `pre_ping`), Celery-воркер — отдельный движок с `NullPool` на каждый вызов, потому что каждая задача запускает свой event loop.
- `analysis_jobs.retry_count` пока не заполняется: повторы выполняются внутри HTTP-клиента LLM, Celery-ретраев нет.

## Безопасность

- Пароли — bcrypt (в отдельном потоке, не блокирует event loop); политика сложности пароля при регистрации.
- Access-JWT с обязательным сроком жизни; refresh-токены с `jti` хранятся в Redis и ротируются, logout отзывает токен.
- Блокировка входа по (email, IP) и rate limit `/auth/*`; адрес клиента берётся из `X-Forwarded-For` только от `TRUSTED_PROXY_HOSTS`.
- Источники по URL: только http/https, запрет приватных, loopback и link-local адресов с проверкой после DNS-резолва и на каждом редиректе, лимиты размера и таймаута.
- Приватный бакет в SeaweedFS, скачивание — presigned URL с TTL и `response-content-disposition` (поддерживается SeaweedFS с 4.01); приложение работает под единственным S3-пользователем с правами Read/Write/List на один бакет, админских учёток в хранилище нет. Внутри сети compose у SeaweedFS открыты служебные порты (master 9333, filer 8888, gRPC admin-воркера без mTLS) — наружу они не публикуются.
- CORS без `*`; `X-Request-ID` санитизируется; логи структурированы, секреты вырезаются рекурсивно.

## Ограничения и известные нюансы

- Хранится только текущее состояние документа и результат последнего анализа; версий нет.
- Ролей внутри проекта нет — только владелец и глобальный `admin`.
- Правки применяются заменой `old_text → new_text` (txt/markdown) или текста абзаца (docx); пересекающиеся правки в одном абзаце могут не примениться.
- Контракт LLM в `infrastructure/llm/schemas.py` — плейсхолдер до выбора провайдера; по умолчанию `StubLLMClient`.
- Промпт с разделителями снижает риск prompt injection, но не исключает его; ответ модели проходит валидацию схемы и ручное ревью.
- `audit_logs` растёт без retention-политики; при росте стоит партиционировать по `created_at`.

## Структура репозитория

```
SyncBack/
├── docker/                  Dockerfile backend и worker, seaweedfs-start.sh
├── docker-compose.yml       + docker-compose.dev.yml (порты на 127.0.0.1)
├── alembic/versions/        миграции 0001…0025
├── scripts/                 проверки границ слоёв
├── app/
│   ├── main.py              приложение, middleware, обработчики ошибок, lifespan
│   ├── core/                config, dependencies, limiter, middleware, logging
│   ├── api/                 deps, upload_utils, schemas/, v1/routers/
│   ├── domain/              lifecycle, value_objects, policies, events, exceptions,
│   │                        storage_keys, source_url, interfaces/, services/
│   ├── infrastructure/      db/, storage/, cache/, events/, queue/, llm/, parsers/,
│   │                        exporters/, source_connectors/, security/
│   └── workers/             celery_app, tasks/analysis_tasks, pipeline/suggestion_mapper
└── tests/                   unit/, contract/, integration/
```
