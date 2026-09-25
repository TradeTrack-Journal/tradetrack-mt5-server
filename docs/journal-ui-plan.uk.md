# Journal and investor connection implementation

Planning briefing reviewed: existing EA uses position aggregation, account-unsafe legacy IDs,
and deletes duplicate rows. New collector must never invoke that deletion path.

## Atomic tasks (parent implementation)

1. Add protected connection API and bounded directory search.
   target_files: E:/projects/New folder/traders-notetaker/src/server/services/mt5-collector/connections.ts;
   E:/projects/New folder/traders-notetaker/src/server/services/mt5-collector/directory-search.ts;
   E:/projects/New folder/traders-notetaker/src/server/services/mt5-collector/index.ts;
   E:/projects/New folder/traders-notetaker/src/server/api/routers/mt5-collector.ts;
   E:/projects/New folder/traders-notetaker/src/server/api/routers/_app.ts;
   E:/projects/New folder/traders-notetaker/src/app/api/trpc/[trpc]/route.ts.
   change_type: add/modify. Acceptance: owner-only setup/status/revoke; exact server selection;
   no credentials in logs/errors/status. Forbidden: external directory credentials, production writes.
   edge_cases: missing connection returns disconnected only after account ownership check;
   ambiguous exact server names reject setup; directory presence does not prove worker readiness;
   disabled rollout refuses setup; revoke remains available; archived/wrong owner reject;
   setup is serialized; password input never included in diagnostic data.

2. Add investor UI alongside existing EA entry points.
   target_files: E:/projects/New folder/traders-notetaker/src/feature/AddTradesFlow/components/Mt5BrokerConnect/Mt5BrokerConnect.tsx;
   E:/projects/New folder/traders-notetaker/src/feature/AddTradesFlow/components/Mt5BrokerConnect/index.ts;
   E:/projects/New folder/traders-notetaker/src/feature/AddTradesFlow/components/Mt5ConnectionPanel/Mt5ConnectionPanel.tsx;
   E:/projects/New folder/traders-notetaker/src/feature/AddTradesFlow/components/Mt5ConnectionPanel/index.ts;
   E:/projects/New folder/traders-notetaker/src/feature/AddTradesFlow/components/Step3AccountAndPanel/Step3AccountAndPanel.tsx;
   E:/projects/New folder/traders-notetaker/src/feature/TradingAccounts/components/AccountDetailPage/AccountDetailPage.tsx;
   E:/projects/New folder/traders-notetaker/messages/en.json;
   E:/projects/New folder/traders-notetaker/messages/uk.json;
   E:/projects/New folder/traders-notetaker/messages/ru.json;
   E:/projects/New folder/traders-notetaker/messages/es.json.
   change_type: add/modify. Acceptance: keyboard accessible exact server selection, investor form,
   queued/collected/failed status and balance; four locales. Forbidden: claiming queued means imported,
   passwords in storage/URL/analytics, nominal overwritten by balance.
   edge_cases: empty/loading/error searches, query debounce, clear previous selection on editing;
   keyed account form prevents credentials leaking across account switches; buttons locked during mutation;
   clear password immediately on submit and clear mutation cache after settlement; zero/negative balance valid;
   use theme tokens and responsive controls; invalidate status and account reads after mutation;
   existing EA view preserved; directory result count bounded to 20.

3. Add canonical journal projection and resumable history collection (critical).
   target_files: E:/projects/New folder/traders-notetaker/prisma/schema.prisma;
   E:/projects/New folder/traders-notetaker/prisma/migrations/ (Prisma generated only);
   E:/projects/New folder/tradetrack-api/prisma/schema.prisma (canonical schema copy only);
   E:/projects/New folder/tradetrack-api/src/mt5/;
   E:/projects/New folder/tradetrack-mt5-server/app/collector/;
   E:/projects/New folder/tradetrack-mt5-server/tests/;
   E:/projects/New folder/traders-notetaker/scripts/mt5-collector-selftest.ts.
   change_type: add/modify/scaffold. Acceptance: partial closes aggregate with exact decimal costs;
   stable account-scoped IDs and corrections preserve notes/risk; raw cash flows excluded from trades;
   bounded persisted backfill and verified time conversion before canonical writes.
   Forbidden: legacy duplicate deletion, interpreting unknown server timestamps as UTC,
   rewriting nominal, trading calls, production migration/deploy.
   edge_cases: missing open history blocks position; netting reversal splits lifecycle;
   unknown cash/deal enums retained raw and flagged; cost conservation; duplicate tickets conflict;
   offset intervals must be explicit and cover each timestamp, gaps/overlaps block import;
   adoption of existing EA rows requires reviewed matches (user confirmed); no two active writers;
   large histories bounded, cursor advances only on accepted result; retries idempotent;
   account revocation/ownership/lease must still hold at commit; correction never deletes annotations.

4. Verify and document.
   target_files: E:/projects/New folder/traders-notetaker/scripts/mt5-collector-selftest.ts;
   E:/projects/New folder/tradetrack-mt5-server/tests/;
   E:/projects/New folder/tradetrack-mt5-server/docs/;
   E:/projects/New folder/tradetrack-api/src/mt5/README.md.
   change_type: add/modify. Acceptance: Next type/lint and focused tests, Nest lint/build/runtime,
   Python regression tests; truthful readiness and live-broker limitations. Forbidden: Nest test files,
   production DB, reopening user's closed terminal. edge_cases: absent live terminal does not spawn;
   unrelated lint failures reported separately; migrations run only against explicit local loopback DB.

## Edge-case audit

17/17 categories considered; 16 relevant, 1 n/a (deep links: existing routes retained).
Empty/zero, loading, errors, auth, i18n (4), theme, responsive, concurrency, cache, bounded lists,
time/DST, money, idempotency, accessibility, integration ownership and Prisma constraints have
explicit decisions above. No open product decisions: legacy adoption requires match review.
Server historical offset evidence is operational configuration, never guessed by the importer.
