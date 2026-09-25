# Черга та worker: етап shadow collection

Цей етап зберігає сирі deals і останній snapshot позицій. Він не змінює Trade,
EA, lastSyncAt чи UTC cursor. Публічний UI підключення і canonical reducer — наступний етап.

## Atomic tasks

1. **Schema / connection lifecycle** (critical, parent)
   - target_files: `E:/projects/New folder/traders-notetaker/prisma/schema.prisma`, generated `prisma/migrations/*`, `src/server/services/mt5-collector/{index,connections}.ts`, `scripts/mt5-collector-selftest.ts`, `package.json`, `.gitignore`; vendored `E:/projects/New folder/tradetrack-api/prisma/schema.prisma`.
   - change_type: add / modify.
   - acceptance: one recurrent job per connection; encrypted password outside job; owner check and revocation invalidate old credential version.
   - forbidden: production DB, migrations in Nest/Python, changes to EA or Trade, secret output.
   - edge_cases: duplicate setup serialized on account; login/server immutable after creation; disabled/archived/changed-owner accounts cannot be claimed; rotation increments version; no random encryption-key fallback.
2. **Nest durable protocol** (critical, parent)
   - target_files: `E:/projects/New folder/tradetrack-api/src/mt5/**`, `src/config/env.validation.ts`, `src/main.ts`.
   - change_type: scaffold / add / modify.
   - acceptance: authenticated claim/heartbeat/complete/fail; one job per slot/account; duplicate ACK idempotent; expired token cannot commit; bounded raw persistence transaction.
   - forbidden: public password routes, test files in Nest, deploy, raw exceptions in API.
   - edge_cases: claim UUID reused after timeout returns same lease; fixed 180s lease; max three transient failures with backoff; expired lease quarantines old slot until a new terminal process generation; all operations lock node then slot then job; no password sent before active lease validation; revoked credentials reject completion; nullable history never success; 4MiB HTTP/5000 total record limit fails explicitly, never truncates.
3. **Python process worker** (critical, parent)
   - target_files: `E:/projects/New folder/tradetrack-mt5-server/app/collector/**`, `scripts/run_mt5_worker.py`, `tests/test_worker.py`, docs and README.
   - change_type: add / modify.
   - acceptance: one child per configured running slot; password only stdin/memory; bounded timeout; process/path/account/server/investor guards before and after reads; pure fault tests plus local API smoke.
   - forbidden: order_send, creating/closing arbitrary terminals, credential CLI/files/logs, native library shared between threads.
   - edge_cases: local named mutex shared with inventory; child timeout kills only child and quarantines terminal; fresh investor journal required (English build gate), missing proof fails closed; native initialize may launch if terminal dies during attach race, changed identity discards result and quarantines; no claim on offline or unknown server; inventory maintenance is explicit; restart must not silently clear quarantine.

## Edge-case audit

Relevant categories (11): 1 empty/null (empty valid only after successful native read); 3 errors (safe codes); 4 auth (node token + owner/version); 8 concurrency (row locks + local mutex + lease fencing); 9 cache (new raw snapshot only, no UI consumers); 10 large lists (bounded payload, explicit error); 11 dates (raw unresolved, no cursor); 12 precision (IDs/financial values strings); 13 idempotency (claim/result tokens); 16 integrations (shadow only); 17 Prisma (unique/FK/generated local migration). Count: **11 relevant**, each decided, zero open.

N/A categories (6): 2 loading UI, 5 i18n, 6 theme, 7 responsive, 14 deep links, 15 accessibility — no UI changed.

## Validation

Isolated local PostgreSQL only; generated migration and clients in both repos; Next lifecycle selftest;
Nest lint/build and manual authenticated endpoint fault scenarios; Python normalization/worker tests.
Native live verification is reported separately from simulations. Successful raw collection does not
prove full history, UTC interpretation, VPS capacity or production readiness.

## Висновок реалізації

Journal-only gate замінено на перевірку поточного Read Only заголовка разом із native guards: свіжий journal буферизується, повторний login може не створювати запису. Підтримка навмисно обмежена English demo build 6182. User steering: додано balance/equity/currency у raw snapshot і owner-scoped status helper Next; nominal не змінюється. Див. queue-worker.uk.md.
