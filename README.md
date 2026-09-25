# MT5 Backend (FastAPI)

[Повний demo-збір та два каталоги серверів](docs/vps-demo-collection-and-servers.uk.md):
explicit Python login і повторний raw-збір пройдено на двох demo; додано статуси
готовності сервера. Автоматичний catalogue updater і login пропфірми ще не перевірені.

[VPS demo build 6190](docs/vps-build-6190.uk.md): локально перевірено investor →
master → investor і два окремі demo-слоти. English Demo/Hedge 6190 додано до gate;
explicit Python login та наскрізний demo collector ще потребують перевірки.

## Модернізація — локальний етап

[Імпорт журналу та UI](docs/journal-ui.uk.md): реалізовано counted backfill,
канонічний reducer, захищену форму інвесторського підключення та перевірений
перехід зі старого EA. Увімкнення потребує підтвердженої політики broker time;
production лишається вимкненим. Цей документ описує актуальний стан етапу.

[План реалізації та критерії готовності](docs/python-server-plan.uk.md) описує
новий Python collector, ізоляцію MT5, investor-only перевірку та інтеграцію з бекендом.
Початок нового ядра — `app/collector/`: нормалізація всіх deals і positions,
64-бітні IDs у JSON-рядках, UTC-вікна й явні помилки замість неповного успіху.
Новий worker встановлює MT5-сесії через окремі дочірні процеси; старі API/RQ routes залишаються окремими.

[Черга й worker — реалізований локальний shadow pilot](docs/queue-worker.uk.md):
PostgreSQL jobs, lease/fencing, encrypted credentials, retries/quarantine та
atomic raw storage через Nest. На реальному demo отримано 5 deals, 2 закриття,
profit 84.45. Закритий термінал пропускається. Поточний investor gate підтримує
лише перевірений English build 6182 / Demo Account; це ще не production rollout.

[Результат першої живої перевірки](docs/live-probe-2026-09-10.uk.md):
окрема дослідна `scripts/probe_mt5.py` підключила деморахунок через Python;
холодний запуск і production guards ще потребують роботи. Інструкція проби,
перевірені залежності та обмеження наведені у звіті.

[Виправлення часової вибірки й запуск на вже відкритому MT5](docs/history-clock-and-attach-2026-09-10.uk.md):
додано `--attach-pid`/`--data-path`; підтверджено дві закриті угоди деморахунку.
Raw broker timestamps зберігаються без непідтвердженого перетворення в UTC.

[Агент інвентаризації та протокол Nest](docs/node-agent.uk.md):
перевірено два незалежні MT5, читання Server ComboBox і збереження звітів
через захищені node routes. `scripts/report_mt5_inventory.py` не запускає
термінали й не виконує login. Інвентаризація ще не є worker синхронізації.

Перевірка ядра без MT5, Redis, мережі та паролів (лише стандартна бібліотека Python):

```powershell
python -m unittest discover -s tests -v
```

Нижче збережено опис **legacy-сервера**. Він містить відомі проблеми,
зокрема неперевірений discovery та незахищені прямі маршрути; ці інструкції
не є інструкцією розгортання модернізованої production-версії.

Простий Python-сервер для підключення до MetaTrader 5 з VPS.

## Локальний запуск

1. Встанови Python 3.11+ на Windows.
2. Встанови залежності:

```bash
pip install -r requirements.txt
```

3. Переконайся, що на цій самій машині встановлено MetaTrader 5 і він може логінитись до потрібного брокера.

4. **Broker discovery (проп-фірми):** якщо сервер не в списку компаній MT5, Python API не зможе підключитись. Бекенд автоматично запускає `terminal64.exe /portable /login /password /server` — MT5 робить discovery, додає сервер у `servers.dat`. Для MetaQuotes-Demo discovery пропускається (вже в дефолтному списку). Це додає ~10 с при першому підключенні до нового брокера. Якщо виникають IPC-помилки або конфлікти з інстансами — вимкни: `MT5_SKIP_DISCOVERY=1`.

5. Запусти сервер:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Тест без Redis і воркерів (тільки API)

Можна тестувати **локально без інстансів** (без Redis, без RQ workers):

1. Запусти лише Uvicorn (крок 4 вище).
2. Відкрий Swagger: http://localhost:8000/docs
3. **Перевірка конекту:** `POST /mt5/test-connect` — тіло: `{"login": 12345, "password": "пароль", "server": "Broker-Server"}`
4. **Звичайний синк (угоди):** `POST /mt5/get-trades` — ті ж login/password/server; опційно `from_timestamp`, `to_timestamp` (ISO). Повертає лише **закриті** угоди (фільтр entry in 1,2,3, один запис на позицію, без дублікатів по ticket).

Ці два ендпоінти підключаються до MT5 напряму в поточному процессі (без черги). Для `enqueue-trades` та `enqueue-connect` потрібні Redis і воркери.

## Основні ендпоінти

- `POST /mt5/test-connect` — разовий конект до MT5 та повернення базової інформації про угоди.
- `POST /mt5/get-trades` — повернення списку угод за останній період (MVP).

Обидва ендпоінти виконують коротке підключення: `discovery (якщо MT5_PATH задано) → initialize → login → отримання даних → shutdown`.

## Черга задач (Redis + RQ)

Для асинхронної обробки запитів до MT5 використовуються Redis і RQ. Є кілька черг (`mt5_trades_0`, `mt5_trades_1`, …) — по одній на термінал. Бекенд **запам’ятовує**, який термінал обслуговував акаунт (login+server): наступні connect/trades для цього акаунта йдуть у ту саму чергу, щоб той самий MT5 був «прив’язаний» до акаунта.

1. Запусти Redis (локально або в Docker):

```bash
docker run -d --name redis-mt5 -p 6379:6379 redis:7
```

2. Запусти RQ-воркер. **На Windows** обовʼязково використовуй наш клас. Кожен воркер слухає **одну** чергу і має свій індекс (`MT5_QUEUE_INDEX`) і свій `MT5_PATH`:

```powershell
$env:MT5_QUEUE_INDEX="0"
$env:MT5_PATH="C:\Program Files\mt5-instance1\terminal64.exe"
python -m rq.cli worker --worker-class app.run_worker.WindowsSimpleWorker mt5_trades_0 --url redis://localhost:6379/0
```

3. Запусти FastAPI-сервер:

```bash
hypercorn app.main:app --bind 0.0.0.0:8000
```

4. Тестування черги через Swagger (`/docs`):
- `POST /mt5/enqueue-connect` — перевірка креденшіалів; після успіху акаунт прив’язується до цього воркера.
- `POST /mt5/enqueue-trades` — поставити задачу в чергу, повертає `job_id`.
- `GET /mt5/job-status/{job_id}` — перевірити статус задачі й забрати результат, коли `status = finished`.

### Декілька терміналів (воркерів)

За замовчуванням одна черга (`MT5_QUEUES_NUM=1`). Щоб використовувати кілька MT5, встанови `MT5_QUEUES_NUM=4` (або скільки воркерів є). Кожен воркер слухає свою чергу і має свій `MT5_QUEUE_INDEX` та `MT5_PATH`:

**Термінал 1 (індекс 0):**
```powershell
$env:MT5_QUEUE_INDEX="0"
$env:MT5_PATH="C:\Program Files\mt5-instance1\terminal64.exe"
python -m rq.cli worker --worker-class app.run_worker.WindowsSimpleWorker mt5_trades_0 --url redis://localhost:6379/0
```

**Термінал 2 (індекс 1):**
```powershell
$env:MT5_QUEUE_INDEX="1"
$env:MT5_PATH="C:\Program Files\mt5-instance2\terminal64.exe"
python -m rq.cli worker --worker-class app.run_worker.WindowsSimpleWorker mt5_trades_1 --url redis://localhost:6379/0
```

Після першого успішного connect або get-trades для акаунта бекенд зберігає прив’язку «акаунт → черга (термінал)» в Redis (TTL 1 доба). Наступні запити для цього акаунта йдуть у ту саму чергу.

**Один воркер:** встанови змінну середовища `MT5_QUEUES_NUM=1`. Тоді всі завдання йдуть у чергу `mt5_trades_0`; достатньо одного воркера з `MT5_QUEUE_INDEX=0`, що слухає `mt5_trades_0`.

## Перезапуск інстансів

**Скрипт (рекомендовано):** з каталогу проєкту:

```powershell
.\scripts\restart.ps1
```

Скрипт: зупиняє всі процеси `python.exe` (API та RQ workers), потім запускає API та **N воркерів** (N = `MT5_QUEUES_NUM` з `.env`, за замовчуванням 3). Кожен воркер відкривається в окремому вікні зі своїми змінними: індекс черги 0, 1, 2, … і шлях до MT5 з `MT5_PATH_0`, `MT5_PATH_1`, `MT5_PATH_2` у `.env`. Якщо в `.env` задано **`EXTRA_SERVER_CMD`** (наприклад команда запуску іншого сервера — CAD тощо), скрипт після воркерів відкриває ще одне вікно і виконує цю команду. API при старті завантажує `.env` (через python-dotenv), тому `MT5_QUEUES_NUM` і `REDIS_URL` потрібно задати саме там. Щоб лише запустити без зупинки попередніх процесів: `.\scripts\restart.ps1 -SkipKill`.

**Вручну:**

1. Зупинити: закрити вікна консолі з API та воркерами або виконати `taskkill /F /IM python.exe` (зупинить усі Python-процеси на машині).
2. Запустити знову: спочатку API (`hypercorn app.main:app --bind 0.0.0.0:8000`), потім кожен воркер у своєму вікні (команди з розділу «Черга задач» вище).
