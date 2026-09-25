# Demo-збір і підготовка серверів — 10.09.2026

Повний локальний шлях Node API → lease → credentials → explicit Python login →
raw result → Nest/PostgreSQL пройдено на двох MetaQuotes-Demo. Рахунок 112447298
має 5 raw records, баланс/equity 100084.45 USD; 5055771845 має 1 неторговий запис,
баланс/equity 100000 USD. Повторний збір залишив кількість 5/1 без дублікатів.
Canonical import вимкнений; це не доказ повноти історії або broker timezone.

Перевірки: 86 Python tests; Next collector lifecycle/readiness selftest на
локальній БД; Next TypeScript; паритет MT5 ключів uk/en/ru/es. Повний Next lint
має 14 errors/105 warnings поза зміненими MT5 файлами; MT5 diagnostics немає.

Після успішного native login MT5 може оновити caption із затримкою. Початкова
перевірка чекає до 2 секунд лише на investor caption, повторюючи guards процесу,
рахунку, шляхів і заборони торгівлі. До успішної перевірки history не читається.
Після початку збору будь-яка втрата evidence одразу відхиляє результат.

Слоти — спільний пул підготовлених терміналів, не довічна прив'язка до рахунку.
Перші jobs поміняли рахунки між demo-01/demo-02. Це очікувана поведінка scheduler;
credentials/result завжди звіряються за точними login/server. Старі локальні
cached-session diagnostic scripts мають фіксовані expected login і після такого
перерозподілу можуть відхиляти коректний слот. Джерело істини — worker result і БД.

## Два каталоги

- Next `Mt5Broker/Mt5Server/Mt5ServerObservation` — власний центральний каталог
  для пошуку й вибору в UI; broker+exact server, provenance, DIRECTORY_ONLY.
- `Mt5TerminalServerState` — доказ доступності в конкретному slot. Python читає
  Server ComboBox саме цього процесу, не password controls. Nest прив'язує evidence
  до generation, catalogHash і часу. Claim вимагає PRESENT зі свіжістю 120 секунд,
  підготовлений STARTING слот, відсутність quarantine/активного job і точний server.

Next getStatus тепер показує readiness окремо від загальної черги:
SERVER_UNRESOLVED, WAITING_FOR_TERMINAL, SERVER_PREPARATION_REQUIRED,
WAITING_FOR_SLOT або READY. Це advisory status, не дозвіл обходити Nest claim.
UI переклади оновлені для uk/en/ru/es. Schema/migrations для зміни не потрібні.
На живій інвентаризації MetaQuotes-Demo дає READY, FTMO-Server —
SERVER_PREPARATION_REQUIRED. За застарілої інвентаризації — WAITING_FOR_TERMINAL.

Шість FTMO server names отримано name-only запитом до незалежного MTAPI adapter
і внесено лише в локальну demo-БД через існуючий Next importer. Ніяких credentials
у directory API не передавали. API/токени конкурента не використовували.
Поточні MT5 не мають FTMO в інвентаризації; directory entry не є servers.dat.

## Що ще не реалізовано / не доведено

Автоматичного terminal catalogue updater в переданому пакеті немає: у schema є
provisioningStatus/desiredCatalogVersion/appliedCatalogVersion, але робочий механізм
builder → drain → перенесення → restart → inventory описаний лише у плані
server-provisioning.uk.md. Цей етап не слід називати завершеним.

Для наступної перевірки потрібен окремий broker builder/новий слот. У ньому через
штатний пошук брокера або офіційний broker installer додається точний сервер;
після цього перевіряються local server presence та investor login саме цього брокера.
Працюючі terminal data files не перезаписуються. З однієї server name або directory
access points робочий servers.dat не синтезується. Незнайомий caption/build/account
mode залишається заблокованим, доки не перевірено investor/master на цьому форматі.

Офіційні довідки: [MT5 broker search](https://www.metatrader5.com/en/terminal/help/startworking/acc_open),
[FTMO: точний сервер із Credentials](https://ftmo.com/en/faq/how-do-i-log-in-to-mt5/).
