# QA Job Scout

Локальный AI-агент для поиска и предварительного отбора QA-вакансий. Проект собирает вакансии с подключённых площадок, нормализует данные, применяет детерминированные правила под профиль кандидата, при необходимости использует OpenAI для дополнительного анализа и готовит Markdown-отчёт и черновики сопроводительных писем.

Проект **не отправляет отклики автоматически**. Финальный просмотр вакансии, прикрепление CV и отправка отклика выполняются пользователем вручную.

---

## 1. Что умеет проект

### Сбор вакансий

Активные источники:

| Ключ | Источник | Способ сбора |
|---|---|---|
| `hirehi` | HireHi | Playwright |
| `rockethunt` | RocketHunt | Playwright |
| `dreamjob` | DreamJob | Playwright |
| `hirify` | Hirify | Playwright |
| `taylor` | Taylor | Playwright |
| `jobrocket` | JobRocket | Playwright |
| `talanto` | Talanto | Playwright |
| `getmatch` | GetMatch | Playwright |
| `geekjob` | GeekJob | Playwright |
| `rvc` | RVC | Playwright |
| `hh` | HH.ru | официальный HH API |

LinkedIn-адаптер сохранён в коде для совместимости/развития, но **не входит в `enabled_adapters()` и текущим `scan` не запускается**.

### Обработка вакансий

Для каждой вакансии проект:

1. извлекает название, URL, описание и дату;
2. нормализует текст;
3. определяет, относится ли вакансия к QA;
4. проверяет удалённый формат;
5. анализирует обязательность английского языка;
6. анализирует обязательность automation;
7. учитывает возраст вакансии;
8. оценивает соответствие профилю кандидата;
9. сохраняет вакансию и её статус в SQLite;
10. для подходящих вакансий создаёт сопроводительное письмо.

Основные статусы:

- `recommended` — вакансия прошла детерминированные фильтры;
- `needs_review` — есть неоднозначность, требующая проверки;
- `rejected` — вакансия не соответствует заданным условиям.

AI используется только после детерминированных проверок для вакансий, которые прошли основной фильтр. Если `OPENAI_API_KEY` не задан, используется локальная эвристика и шаблон письма.

---

## 2. Требования

- Windows / Linux / macOS;
- Python **3.11+**;
- доступ в интернет для сайтов вакансий и HH API;
- Chromium для Playwright.

Для разработки дополнительно используются `pytest`, `pytest-cov`, `pytest-html` и `ruff`.

---

## 3. Установка с нуля

Открыть терминал в корне проекта.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
```

### Linux/macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

После установки проект запускается из корня репозитория:

```text
QA_Job_AI/
├── .env
├── candidate_profile.json
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── qa_job_scout/
└── tests/
```

---

## 4. Конфигурация `.env`

Файл `.env` создаётся из `.env.example` и **не должен попадать в Git**.

### Общие настройки

```dotenv
HEADLESS=false
SOURCE_CONCURRENCY=6
DETAIL_CONCURRENCY=12
PAGE_TIMEOUT_MS=20000
SELECTOR_TIMEOUT_MS=7000
DETAIL_RETRIES=3
```

- `HEADLESS=false` — удобно для первого запуска и отладки;
- `HEADLESS=true` — запуск браузера без интерфейса;
- `SOURCE_CONCURRENCY` — сколько источников обрабатывать параллельно;
- `DETAIL_CONCURRENCY` — параллелизм при открытии страниц вакансий;
- `PAGE_TIMEOUT_MS` / `SELECTOR_TIMEOUT_MS` — таймауты Playwright.

### OpenAI

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
```

`OPENAI_API_KEY` необязателен.

Без него проект работает локально без AI API. С ключом OpenAI дополнительно формирует оценку/письмо в предусмотренном pipeline.

---

## 5. Профиль кандидата

Файл:

```text
candidate_profile.json
```

В нём хранятся:

- имя и контакты;
- целевые роли;
- профессиональное summary;
- навыки;
- automation-навыки;
- подтверждённый опыт;
- опыт по компаниям/проектам;
- ограничения (`hard_no`);
- язык сопроводительных писем.

**Это источник фактов для оценки и писем.** Проект не должен придумывать отсутствующий опыт.

Если меняется резюме или целевая позиция, сначала обновляется `candidate_profile.json`.

---

# 6. Основные команды

## Полный скан всех источников

```powershell
python -m qa_job_scout scan
```

Это основной рабочий сценарий. Он запускает все адаптеры из `enabled_adapters()` одновременно с ограничением `SOURCE_CONCURRENCY`.

В конце выводится статистика отдельно по каждому источнику и общий результат.

---

## Скан только одного источника

Поддерживается выбор источников через `--sources`.

### Только HH.ru

```powershell
python -m qa_job_scout scan --sources hh
```

### Только HireHi

```powershell
python -m qa_job_scout scan --sources hirehi
```

### Несколько конкретных источников

```powershell
python -m qa_job_scout scan --sources hh hirehi rockethunt
```

Без `--sources` сканируются **все включённые источники**.

Доступные ключи:

```text
hirehi
rockethunt
dreamjob
hirify
taylor
jobrocket
talanto
getmatch
geekjob
rvc
hh
```

Если передан неизвестный ключ, сканирование завершается с понятной ошибкой вместо молчаливого пропуска.

---

## Пересоздать отчёт из SQLite

```powershell
python -m qa_job_scout report
```

Команда не сканирует сайты. Она использует уже сохранённые данные из `qa_jobs.sqlite3` и создаёт/обновляет:

```text
out/report.md
```

---

## Открыть вакансию для ручной проверки

```powershell
python -m qa_job_scout review <vacancy-id>
```

Например:

```powershell
python -m qa_job_scout review 136592381
```

Playwright открывает страницу вакансии в браузере. Пользователь сам проверяет вакансию, прикладывает CV и отправляет отклик.

---

## Отклонить вакансию вручную

```powershell
python -m qa_job_scout reject <vacancy-id> "Причина"
```

Например:

```powershell
python -m qa_job_scout reject 136592381 "Не подходит уровень позиции"
```

Решение сохраняется в таблице `manual_decisions` и учитывается при последующих сохранениях.

---

# 7. HH.ru: официальная API-интеграция

HH.ru подключён отдельным API-адаптером и **не парсится через HTML для получения вакансий**.

Используются официальные endpoints HH API для поиска и получения деталей вакансии.

## Рекомендуемая авторизация вакансий

Для текущего сборщика используется application access token:

```dotenv
HH_VACANCY_AUTH_MODE=application
HH_APPLICATION_TOKEN=
HH_APPLICATION_TOKEN_FILE=.hh_app_token.json
```

Application token не нужно получать перед каждым запросом. Если токен хранится в `.hh_app_token.json`, клиент использует его повторно.

**Не коммитьте `.hh_app_token.json` и `.env`.**

---

## 8. Настройка HH.ru

В `.env` задаются параметры приложения HH:

```dotenv
HH_CLIENT_ID=
HH_CLIENT_SECRET=
HH_REDIRECT_URI=http://localhost:8000/oauth/callback
HH_USER_AGENT=QA_Job_AI/0.2 (contact: your-email@example.com)
HH_HOST=hh.ru
HH_LOCALE=RU
```

### User OAuth / PKCE

Для пользовательской OAuth-авторизации существует команда:

```powershell
python -m qa_job_scout hh-auth
```

Она запускает локальный callback на `localhost`, открывает HH.ru, получает authorization code и сохраняет user access/refresh token в:

```text
.hh_tokens.json
```

Файл не коммитится.

User OAuth нужен для пользовательских HH API-операций и диагностики. Для текущего сбора вакансий рекомендуется `HH_VACANCY_AUTH_MODE=application`.

---

# 9. HH.ru: поиск до 1000 вакансий за один `scan`

Для HH API настроены:

```dotenv
HH_SEARCH_TEXTS=QA Engineer,Manual QA,QA,Тестировщик
HH_PERIOD_DAYS=5
HH_PER_PAGE=100
HH_MAX_VACANCIES=1000
HH_DETAIL_CONCURRENCY=10
HH_API_TIMEOUT_SECONDS=30
HH_API_RETRIES=3
```

HH API отдаёт результаты страницами. Клиент автоматически выполняет пагинацию.

При запуске:

```powershell
python -m qa_job_scout scan --sources hh
```

логика выглядит так:

```text
страница 1 → до 100 вакансий
страница 2 → до 100
страница 3 → до 100
...
страница 10 → до 100
                 ↓
           максимум 1000
```

`HH_MAX_VACANCIES=1000` — верхний предел, а не гарантия, что HH всегда вернёт ровно 1000 уникальных вакансий. Если по поисковым запросам и периоду доступно меньше, будет собрано меньше.

После получения карточек клиент запрашивает детали каждой вакансии через `GET /vacancies/{id}`. Поэтому при 1000 результатах это может быть до 1000 detail-запросов.

---

# 10. Дата публикации HH-вакансии

HH API предоставляет точное поле:

```text
published_at
```

Адаптер HH сохраняет его в `Vacancy.published_at` и также сохраняет исходное значение в `published_text`.

Например:

```text
published_at = 2026-09-12T09:36:19+00:00
```

Если точная API-дата есть, pipeline не пытается повторно распознавать её как текстовую относительную дату.

Для HTML-источников используется обычный механизм извлечения даты из `time`, `[datetime]` и текста страницы/карточки.

---

# 11. CAPTCHA и ошибки HH

Клиент различает несколько классов ошибок:

- OAuth authentication error;
- API access/forbidden error;
- CAPTCHA required;
- ошибки application token;
- сетевые/HTTP ошибки.

Если HH возвращает настоящий `captcha_url`, клиент может открыть официальную CAPTCHA-страницу и дождаться ручного решения.

Клиент **не придумывает CAPTCHA URL** по обычному `403` и не маскирует произвольный `403` под OAuth.

---

# 12. Остальные сайты

Все HTML-источники используют Playwright и отдельные адаптеры в:

```text
qa_job_scout/adapters.py
```

У каждого адаптера есть `AdapterSpec`, содержащий URL, селекторы карточек/деталей, правила извлечения даты и дополнительные параметры.

Большинство источников используют общую логику `BaseAdapter`:

```text
страница поиска
      ↓
карточки вакансий
      ↓
ссылки
      ↓
страница вакансии
      ↓
title / text / date
      ↓
Vacancy
```

Если сайт поменял DOM, конкретный адаптер можно обновить независимо от остальных.

Для источников, где нужен логин, Playwright использует persistent browser profile:

```text
.browser-profile/
```

Эта директория локальная и исключена из Git.

---

# 13. Browser profile и ручная авторизация

Если источнику нужна авторизованная браузерная сессия:

1. временно установите:

```dotenv
HEADLESS=false
```

2. запустите нужный скан;
3. войдите на сайте вручную;
4. закройте браузер после сохранения сессии.

Cookies/session state сохраняются в:

```text
.browser-profile/
```

Не переносите эту директорию в Git или публичные архивы.

---

# 14. Архитектура проекта

```text
qa_job_scout/
│
├── __main__.py       # точка входа: python -m qa_job_scout
├── cli.py             # CLI-команды
├── crawler.py         # параллельный запуск адаптеров
├── adapters.py        # адаптеры сайтов и HH API
├── hh_api.py          # официальный HH API client, OAuth/PKCE, tokens
├── core.py            # Vacancy, фильтры, scoring, даты, письма
├── ai.py              # дополнительное AI-enrichment
├── storage.py         # SQLite persistence
└── __init__.py

candidate_profile.json # профиль кандидата
.env.example           # безопасный шаблон настроек
pyproject.toml         # package/test/ruff configuration
requirements.txt       # runtime dependencies
requirements-dev.txt   # development/test dependencies
tests/                 # automated tests
```

---

# 15. SQLite

После запуска появляется:

```text
qa_jobs.sqlite3
```

Основные таблицы:

### `vacancies`

Содержит сохранённые вакансии, статус, score и полный сериализованный объект вакансии.

### `manual_decisions`

Содержит ручные решения пользователя по вакансиям.

### `source_runs`

Журнал каждого запуска источника:

- сколько карточек найдено;
- сколько деталей открыто;
- сколько вакансий собрано;
- сколько рекомендовано;
- сколько отправлено в `needs_review`;
- сколько отклонено;
- статус источника;
- ошибки.

SQLite-файл является локальным рабочим состоянием и исключён из Git.

---

# 16. Отчёт

Основной результат:

```text
out/report.md
```

В отчёте отражаются результаты фильтрации и сведения о вакансиях, включая URL и дату, если она была получена.

Для проблемных вакансий CLI также показывает причины `rejected` / `needs_review`.

---

# 17. Тесты

Установить development dependencies:

```powershell
pip install -r requirements-dev.txt
```

Запустить тесты:

```powershell
python -m pytest -q
```

Запустить с coverage:

```powershell
pytest --cov=qa_job_scout --cov-report=term-missing
```

HTML-результаты pytest при необходимости:

```powershell
pytest --html=test-report.html --self-contained-html
```

---

# 18. Ruff

Проверка стиля и ошибок:

```powershell
ruff check .
```

Автоматические исправления Ruff:

```powershell
ruff check . --fix
```

Конфигурация Ruff находится в `pyproject.toml`:

```toml
[tool.ruff]
line-length = 120
target-version = "py311"
```

---

# 19. CI

GitHub Actions workflow находится в:

```text
.github/workflows/ci.yml
```

CI предназначен для автоматической проверки проекта после push/PR.

Секреты (`.env`, HH tokens, application tokens, API keys) в репозиторий не добавляются.

---

# 20. Git и безопасность

В Git должны попадать только исходники и безопасные конфигурационные шаблоны.

Не коммитить:

```text
.env
.hh_tokens.json
.hh_app_token.json
qa_jobs.sqlite3
.browser-profile/
.venv/
__pycache__/
.pytest_cache/
.coverage
```

Особенно чувствительны:

- `HH_CLIENT_SECRET`;
- `HH_APPLICATION_TOKEN`;
- `access_token`;
- `refresh_token`;
- `OPENAI_API_KEY`;
- cookies из `.browser-profile`.

Если секрет был опубликован в чате, issue, GitHub или другом месте, его следует заменить/ротировать в соответствующем сервисе.

---

# 21. Типовой рабочий процесс

После первоначальной установки:

```powershell
.\.venv\Scripts\Activate.ps1
python -m qa_job_scout scan
```

Если нужен только HH.ru:

```powershell
python -m qa_job_scout scan --sources hh
```

Если нужно проверить только несколько площадок:

```powershell
python -m qa_job_scout scan --sources hh hirehi getmatch
```

После сканирования:

```powershell
python -m qa_job_scout report
```

Затем конкретную вакансию можно открыть:

```powershell
python -m qa_job_scout review <vacancy-id>
```

---

# 22. Быстрая проверка установки

Если проект развёрнут впервые, выполните:

```powershell
python --version
python -m pytest -q
python -m qa_job_scout --help
python -m qa_job_scout scan --help
```

После этого можно запускать полный скан:

```powershell
python -m qa_job_scout scan
```

Для проверки именно HH:

```powershell
python -m qa_job_scout scan --sources hh
```

---

## Лицензия / назначение

Проект предназначен для личного использования как локальный помощник при поиске работы. При работе с внешними площадками необходимо соблюдать их правила использования, ограничения API и требования к авторизации.
