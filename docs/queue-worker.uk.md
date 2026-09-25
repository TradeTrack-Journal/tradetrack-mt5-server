# PostgreSQL queue → Python → raw storage

Стан: локальний shadow pilot, 10.09.2026. Новий шлях успішно зібрав реальний
деморахунок через уже запущений `inventory-02`: 5 raw deals, 2 закриття,
сума trading profit 84.45, відкритих позицій 0. Жива перевірка балансу: balance=100084.45 USD, equity=100084.45 USD. Snapshot також зберігає balance, equity, currency з account_info() при першому підключенні та наступних зборах; фінансові значення — рядки, nominal рахунку не змінюється. Основний термінал користувач
закрив: агент повідомив OFFLINE і не запустив його. Звіт: `queue-live-2026-09-10.json`.

## Що реалізовано

- Next володіє схемою й міграціями `20260910172748_mt5_shadow_collection_queue`
  та `20260910173034_mt5_claim_idempotency`. Застосовано тільки на локальному
  `127.0.0.1:55432/mt5_catalogue_test`. Production не змінювався.
- `configureCollector` / `revokeCollector` / `getCollectorStatus` — внутрішні server-side функції Next:
  перевірка власника, незмінна пара login/server, перевірка каталогу, AES-GCM,
  credentialVersion, видалення зашифрованого пароля при відкликанні. Публічного
  користувацького маршруту або UI підключення цього етапу ще немає.
- Nest видає jobs тільки автентифікованому node, для свіжого inventory конкретного
  slot і підтвердженої назви сервера. Job не містить пароля; credentials видаються
  окремим no-store endpoint після перевірки активного lease та credentialVersion.
- Один recurrent job на connection, один активний job на slot. `FOR UPDATE …
  SKIP LOCKED`, node/slot/job locks, UUID lease + monotonic fence. Claim receipt
  зберігає також порожню відповідь, тому повтор HTTP не запускає інше завдання.
- Lease фіксований 180 секунд. Heartbeat перевіряє чинність, але не продовжує його
  нескінченно. Reaper кожні 15 секунд обробляє до 25 прострочених jobs. Transient
  failures: до трьох спроб, backoff+jitter; auth/read-only/invalid data — PAUSED.
- Успіх атомарно upsert-ить raw deals за connection+ticket і замінює останній
  snapshot позицій. Повтор ACK не створює дублікати; змінений повтор відхиляється.
  Виправлений deal у наступній спробі оновлюється. Пропущені tickets не видаляються.
- Після успіху наступна спроба призначається через 240 секунд; фактична свіжість
  залежить від тривалості job та черги, гарантії 5 хвилин ще немає.
- Windows: один дочірній Python-процес на slot, спільний із inventory named mutex,
  перевірки PID+creation time, шляхів, login+server до/після читань. Дочірній процес
  має watchdog до 125 секунд і запас до завершення lease. Повільний HTTP heartbeat
  не блокує watchdog. Завершується тільки створений child, не весь Python/MT5.
- Timeout/crash/identity drift/lease expiry карантинять slot у БД. Перезапуск агента
  карантин не скидає: потрібен інший процес MT5. Автоматичного restart MT5 ще немає.

## Протокол

База: `/mt5/nodes/:nodeId/slots/:slotId/jobs`, існуючий Bearer node token.

| POST | Вхід | Результат |
| --- | --- | --- |
| `/claim` | generation, claimId, requestedAt | job id/fence/token/deadline або null |
| `/:jobId/credentials` | generation, leaseToken, fence | login, serverName, investorPassword |
| `/:jobId/heartbeat` | той самий lease | підтвердження до фіксованого deadline |
| `/:jobId/complete` | lease + strict raw DTO v1 | atomic shadow commit |
| `/:jobId/fail` | lease + дозволений errorCode | bounded retry або pause |

4 MiB на HTTP body, до 5000 deals+positions загалом. IDs та фінансові значення —
рядки. Broker `time_msc` зберігається числом/BigInt без перетворення часового поясу.
History/positions=None, invalid record, конфліктний duplicate або завеликий batch
ніколи не означають успішну порожню історію. Завеликий batch потребує поділу вікна
на наступному етапі: цього поділу поки немає.

## Investor gate і живі висновки

Перші два нові прогони зупинилися з INVESTOR_UNVERIFIED, не записавши даних.
Перевірка тільки свіжого journal-файла непридатна: MT5 буферизує записи,
а повторний login у вже активний рахунок може не дати нового запису.
[Офіційний опис журналу](https://www.metatrader5.com/en/terminal/help/start_advanced/journal)
та [буферизації](https://www.mql5.com/en/book/common/output/output_print).

Поточний gate підтримує **лише перевірений англійський build 6182 та формат
Demo Account**: точний login/server і структурний `Read Only` у поточному заголовку
саме цього процесу, account.trade_allowed=false, terminal.tradeapi_disabled=true,
успішні initialize/login. Перевірка повторюється після native читань. Компанія з
текстом Read Only у назві не проходить перевірку як інвесторський режим.
Wire evidence: investorVerificationMethod=terminal_title_read_only, terminalBuild=6182.

Це перевірка поточного режиму термінала, **не криптографічна атестація пароля**.
Невідомі build/мова/Real Account відхиляються. Перевірка на справжньому master
паролі та окремо на заблокованому broker рахунку ще потрібна перед production.
Старий journal helper залишений для діагностики, він не є fallback дозволом worker.

У тестовій копії через Options увімкнено Disable algorithmic trading via external
Python API. Нативний API не має attach-only switch: якщо MT5 помре всередині
initialize, бібліотека може запустити його сама; зміна процесу відкидає результат
і карантинить slot. [initialize](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py).

## Запуск після локальної конфігурації

Nest: `MT5_AGENT_ENABLED=true`, `MT5_COLLECTOR_ENABLED=true`, node token hashes,
стабільний `ENCRYPTION_KEY`, однаковий із Next. Обидва flags за замовчуванням false.
Паролі/токени не додаються до git або CLI args. Worker отримує `MT5_AGENT_TOKEN`
через локальне керування секретами та забирає його з environment перед spawn child.

```powershell
.\.venv\Scripts\python.exe -m scripts.run_mt5_worker --config C:\TradeTrack\agent.json --once
```

Без `--once` — повторний poll кожні 10 секунд на slot. Серверний inventory читається
через login dialog між jobs не частіше ніж раз на 90 секунд; worker цей діалог
закриває. Не запускати окремий inventory reporter паралельно для тих самих slots.
Термінали мають бути заздалегідь запущені з окремими data directories. Сеанси
воркерів/каталогів розраховані на одного Windows-користувача: mutex має Local namespace.
Для VPS потрібен окремий Windows user і відсутність ручного перемикання accounts
у worker terminals під час читання. Native identity checks не усувають довільне
ручне перемикання A→B→A посеред одного native виклику.

## Перевірки й наступна межа

Оновлення 10.09: актуальний наступний етап реалізовано в [journal-ui.uk.md](journal-ui.uk.md).
Нижче збережено результати початкової shadow-перевірки.

- 77 Python tests, включно з реальним завислим child та повільним heartbeat.
- Next lifecycle test: ownership, identity, concurrent setup, encryption/version,
  revocation, незмінні EA налаштування. TypeScript passed; lint має 17 попередніх
  помилок поза MT5 змінами, у нових файлах diagnostics немає.
- Nest lint/build пройшли; локальний API перевірений із двома штучними slots,
  concurrent claim, fence/slot binding, lost ACK, duplicate conflict, correction,
  revocation, expiry та quarantine. `queue-api-local-2026-09-10.json`.
- Живий end-to-end на одному demo terminal: 5 deals, 2 closes, profit 84.45.
  Це не доказ паралельного native login двох різних accounts або capacity 100 users.

Далі: верифікація investor/master і live broker UI formats, canonical reducer
(partial closes, INOUT, OUT_BY, costs/cashflows), broker timezone/DST policy,
backfill/chunking, захищений користувацький lifecycle/UI, production writer switch,
Windows supervision/ACL/cache/авторестарт, 2-account native isolation і load/soak.
Повнота history не підтверджена; UTC cursor не пересувається, Trade/EA/lastSyncAt
не змінюються. Тестові account rows і шифровані credentials з локальної БД видалено;
термінал може зберігати свій account cache, shutdown IPC його не очищає.
