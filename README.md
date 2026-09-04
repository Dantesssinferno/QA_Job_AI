# QA Job Scout

Локальный AI-агент для поиска удалённых QA-вакансий, опубликованных не более пяти дней назад. Он собирает объявления, отсеивает неподходящие условия, сопоставляет требования с профилем кандидата и готовит честные сопроводительные письма.

## Что он делает

- Открывает сайты вакансий через Playwright (так лучше работают JavaScript-сайты и сохранённые сессии).
- Учитывает только QA Engineer / Manual QA / Тестировщик / QA/QC вакансии, remote и возраст до 5 дней.
- Исключает вакансии, где английский или automation явно обязательны.
- Дедуплицирует результаты в SQLite, оценивает соответствие и создаёт отчёт в `out/report.md`.
- Для подходящих вакансий пишет черновики писем на русском (или на языке вакансии) по фактам из `candidate_profile.json`.

Агент **не отправляет отклики самостоятельно**. Отклик - существенное внешнее действие, а многие перечисленные сайты требуют личную авторизованную сессию и/или CAPTCHA. Команда `review` открывает конкретную вакансию и печатает письмо: пользователь проверяет текст, прикладывает CV и сам нажимает финальную кнопку отправки.

## Установка и запуск

Требуется Python 3.11+.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
python -m qa_job_scout scan
python -m qa_job_scout report
python -m qa_job_scout review <vacancy-id>
```

`OPENAI_API_KEY` в `.env` необязателен. Без него агент использует прозрачную эвристику сопоставления и шаблон письма; с ключом - улучшает оценку и письмо через OpenAI API. Ключ никогда не пишется в отчёт или базу.

## VS Code и GitHub

Откройте именно папку проекта: `code "C:\Users\Maxim Starostenco\QA_Job_AI"`. Файл `qa_jobs.sqlite3` появляется после первой команды `report` или `scan`; в VS Code откройте его расширением **SQLite Viewer** (проект предлагает его автоматически). Таблица `vacancies` содержит результаты, статусы, score и полный JSON вакансии.

Результаты работы (`qa_jobs.sqlite3`, `out/`, `.browser-profile/`) исключены из Git, поэтому в GitHub попадёт только код и безопасный пример настроек. Для первой фиксации локально:

```powershell
git add .
git commit -m "Initial QA job scout"
```

После создания пустого репозитория на GitHub добавьте его адрес и выполните:

```powershell
git remote add origin https://github.com/<ваш-логин>/qa-job-scout.git
git push -u origin main
```

Чтобы сайты с личным кабинетом были доступны, один раз запустите браузер с `HEADLESS=false`, войдите вручную и закройте его. Playwright сохраняет cookies в `.browser-profile/`, который игнорируется Git.

## Источники

В `qa_job_scout/adapters.py` есть отдельный адаптер для каждого источника: HireHi, RocketHunt, DreamJob, Hirify, Taylor, JobRocket, Talanto, GetMatch, GeekJob, RVC и LinkedIn. Каждый адаптер ждёт селектор карточки, выбирает ссылку вакансии, открывает её страницу и извлекает заголовок, требования и дату. LinkedIn требует авторизованную сессию; `hh.ru/applicant/negotiations` не сканируется, потому что это личная страница, а не поиск вакансий.

После каждого `scan` таблица `source_runs` в `qa_jobs.sqlite3` содержит проверяемый журнал: число найденных карточек, открытых страниц, сохранённых вакансий, результаты фильтрации и ошибки конкретной площадки. Таблица `vacancies` сохраняет все собранные вакансии, включая `rejected` и `needs_review`; в `out/report.md` выводятся только рекомендованные вакансии, каждая со ссылкой и сопроводительным письмом.

Из-за защиты от ботов DOM разных сайтов меняется. Для каждого источника сохраните селектор карточки вакансии в переменной `SOURCE_*_CARD` из `.env`, если универсальный поиск ссылок не нашёл карточки. Сборщик не обходит CAPTCHA и соблюдает задержку между сайтами.

## Безопасность и качество

- Не подделывает опыт, уровень английского или навыки автоматизации.
- Не откликается на вакансии с неясной датой публикации: они помечаются `needs_review`.
- Не отправляет формы, сообщения или CV без вашего финального действия.
- Не храните резюме или ключи в Git; `.gitignore` уже настроен.

## HH.ru API

HH.ru is integrated through the official API, so this source does not use browser scraping. The adapter searches for recent remote QA vacancies, deduplicates them by HH vacancy ID, loads the full vacancy details, strips HTML from `description`, preserves publication timestamps and feeds the normalized `Vacancy` model into the existing deterministic `core.py` filter.

The current API documentation states that HH.ru uses OAuth 2.0, requires `HH-User-Agent`, and exposes `GET /vacancies` for vacancy search plus `GET /vacancies/{vacancy_id}` for detailed vacancy data. It also supports `period` for limiting the publication window, `order_by=publication_time`, and `work_format=REMOTE`.

### HH.ru setup

1. Register an application at `https://dev.hh.ru/admin` and obtain `HH_CLIENT_ID` and `HH_CLIENT_SECRET`, or provide an existing `HH_ACCESS_TOKEN`.
2. Copy the HH variables from `.env.example` into `.env`.
3. Set a real contact email in `HH_USER_AGENT`.
4. Install dependencies:

```powershell
pip install -r requirements.txt
```

5. Run the existing scanner:

```powershell
python -m qa_job_scout.cli scan
```

The default HH query set is:

- `QA`
- `QA Engineer`
- `Manual QA`
- `QA Tester`
- `Тестировщик`
- `Инженер по тестированию`
- `Функциональный тестировщик`

The adapter requests only `REMOTE` vacancies from the last 5 days by default and then applies the same project-level QA/Manual/Fullstack/AQA/Automation/English/date rules in `core.py`. Search behavior can be changed with `HH_SEARCH_QUERIES`, `HH_PERIOD_DAYS`, `HH_MAX_PAGES`, `HH_PER_PAGE` and `HH_WORK_FORMAT`.
