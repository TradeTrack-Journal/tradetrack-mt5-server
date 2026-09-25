# Імпорт журналу та UI — локальна реалізація

10.09.2026. Production не розгорнуто; доступ для звичайних користувачів вимкнений.

## Підключення

У Next є `mt5Collector` tRPC router: capabilities/searchServers/getStatus/configure/revoke.
Форма доступна в AddTradesFlow та вкладці інтеграції рахунку через Mt5ConnectionPanel.
`MT5_CONNECTION_UI_ENABLED=true` відкриває налаштування нового способу; revoke доступний
і після вимкнення прапорця. Усі звичайні операції перевіряють власника рахунку.
Пошук читає тільки власний каталог, не більше 20 результатів. Однакова назва під
різними брокерами вимагає уточнення каталогу, автоматичного вибору немає.

Пароль не повертається у статусі, не потрапляє в development error input або
Prisma error cause. Поле очищується після submit; mutation cache прибирається після відповіді.
Інтерфейс розрізняє чергу, отримані дані, імпорт у журнал і потребу перевірки.
Баланс/equity береться зі snapshot, номінал рахунку не змінюється.

## Перевірений перехід у журнал

Міграція в Next: `20260910183907_mt5_journal_projection` (149 міграцій локально).
Nest отримує тільки копію канонічної Prisma-схеми.

Після отримання історії адміністратор викликає `mt5Collector.approveJournal` з:

- tradingAccountId;
- evidence — джерело перевіреної політики часу;
- intervals — непересічні напіввідкриті інтервали broker numeric time:
  `{ fromMs, toMs, offsetMinutes }`;
- links — явні перевірені відповідності `{ tradeId, positionId, openingTicket }`.

Для нового журналу links порожній. Для старого журналу потрібна відповідність
кожного legacy MT5 Trade. Якщо є кілька старих рядків на один lifecycle, відкриті
legacy рядки чи незрозумілі відповідності, approval відхиляється; нічого не видаляється.
Ключі рахунків/позицій зберігаються рядками, без втрати точності понад 2^53.
Адміністратор має порівняти рядки з raw-історією до схвалення. Окремий візуальний
екран адміністративного порівняння поки не реалізований.

Після approval новий writer володіє журналом, старий EA отримує 409.
Account lock серіалізує approval, legacy import і commit нової черги.
Зміна terminal у Next атомарно відкликає пароль/доступ collector.

## Облік угод

Nest `src/mt5/journal/reducer.ts` збирає повністю закритий lifecycle з усіх його
deal rows. Часткові закриття агрегуються; netting INOUT закриває старий lifecycle
і відкриває новий; OUT_BY підтримується. Profit/swap розвороту належать закритій
частині; commission/fee пропорційно розподіляються між частинами.
Decimal використовується до межі канонічної Float-схеми.

Формула: pnl = grossProfit + commission + swap - openFee - closeFee.
Raw MT5 fee є підписаною величиною, тому closeFee записується з протилежним знаком.
Депозити/кредити/бонуси не стають Trade. Окремі комісії без зв'язку з позицією
потребують перевірки, щоб не приписати їх довільній угоді.

Стабільний ключ: `mt5c:<connectionId>:<positionId>:<openingTicket>`.
Повторне читання не створює дублікатів. Broker fields оновлюються пакетами,
користувацькі notes/tags/risk/setup/attachments зберігаються.
Зниклий ticket у counted window або зміна, яка робить старий lifecycle невалідним,
блокує подальшу проєкцію до перевірки, без автоматичного видалення історії.

## Backfill і межі

Worker рахує записи через history_deals_total до/після фінального читання.
Backfill і recent jobs чергуються; cursor зберігається тільки в прийнятому commit.
Вікно максимум 31 день, щільне історичне вікно звужується без пропуску верхньої межі.
Два нульові лічильники давнішої історії дозволяють завершити сканування доступних
терміналу даних. Це не гарантує повну брокерську архівну історію.

Невідомий час не перетворюється на UTC. Не можна застосовувати сьогоднішній offset
до всіх років; непідтверджені/пересічні/неповні інтервали блокують імпорт.
В одному native response до 5000 deals+positions; canonical projection до 50000
raw rows/account. Більший обсяг повертає явний стан, не обрізається мовчки.
Велика поточна вибірка ще може вимагати додаткового розбиття recent history.

## Перевірки

- 80 Python tests passed, включно з count mismatch, bounded dense history і cursor.
- Next lifecycle та journal selftests passed на окремій локальній PostgreSQL.
- Journal tests: partial close, INOUT, OUT_BY, fees, cashflows, великі IDs,
  time gaps/overlap, idempotency, correction, notes/risk preservation,
  review-required migration та exclusive writer; rollback не запускає blob cleanup.
- Nest lint/build passed. 12 локальних API перевірок пройшли, включно з counted backfill.
- Живий уже запущений demo terminal з новим worker: 5 deals, 2 closes,
  прибуток 84.45, баланс/equity 100084.45 USD. Закритий original terminal пропущено.
- Жива перевірка лишалася shadow: canonicalTradeCount=0, бо історичний timezone
  цього demo ще не підтверджений. Canonical writer перевірено на синтетичних даних.
- Next TypeScript passed; eslint має 17 попередніх errors/106 warnings поза змінами.

До production: підтвердити broker/investor/master UI режими, часові політики,
2-account native isolation, навантаження і supervisor/reboot/RDP lifecycle на VPS.
