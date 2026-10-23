# Remaining four MT5 incidents — recovery verification, 23 October 2026

## Deployed

- API `76d8cba`, Fly workflow https://github.com/TradeTrack-Journal/tradetrack-api/actions/runs/39941088462 completed successfully.
- Worker release `C:\TradeTrack\releases\mt5-8b7ccd1`; branch `fix/mt5-recovery-stage1` pushed.
- Worker and watchdog drained with exit code 0 at 10:44:41 / 10:44:54 UTC. Restarted at 10:45:19 / 10:45:18. Task action working directories changed; credentials, terminal builds and privilege settings were not changed. Previous release `mt5-94a2403`; task XML backups under C:\TradeTrack\runtime\20261023-TradeTrack-*.xml.
- A supervisor check-only attempt while its production mutex was held returned SLOT_BUSY; no drain or task change occurred until the independent read-only maintenance preflight passed (10/10 healthy).

## RoboForex production evidence

Private full before/after exports: C:\TradeTrack\runtime\roboforex-recovery-{before,after}-20261023.json. These are outside git and contain no connection secrets.

- Before: 3,454 statement trades; native projection 917 trades, 865 overlapping, 2,589 older positions unavailable in raw history, 52 new.
- After first successful projection at 10:45:31 UTC: IMPORTED, failureCount=0, 3,506 trades.
- All original 3,454 trade IDs remain. All 2,589 archived rows are structurally identical to their full original records. Notes, tags, attachments, setup, user/account ownership and user risk preferences were preserved on mapped trades too.
- Exactly 865 rows linked to native keys; 52 created; zero duplicate terminal keys and no overlap between archived position IDs and native position IDs.
- Next successful collection/import at 10:46:40 UTC: still 3,506 trades, failureCount=0. Compiled full-projector repeat verification also produced zero creates, mappings and updates.
- Older unavailable broker history remains preserved from the statement. This does not assert the broker verified that older history or identify its retention policy. Later returning history must reconcile to the original IDs.

## FTMO and diagnostics

- One guarded retry of the previously importing, active FTMO-Server3 connection recovered import at 10:33:13 UTC. Repeated fresh imports, including 10:46:18 UTC, have zero failures.
- Confirmed broker service-unavailable failures had consumed the generic counter before an ambiguous native -6 caused pause. API now separates that ambiguous budget and offers at most three extra six-hour recovery probes for eligible previously importing accounts. Explicit access/identity failures remain blocking.
- Five inactive, never-imported connections with historical CONNECTION_FAILED remain paused for owner verification; they were not declared working or blindly retried.
- Historical WORKER_FAILED events were correlated to UNSUPPORTED_TERMINAL_UI and UPDATE_STOP_FAILED on 17 October. Both now keep their specific identity and safe explanation. Unexpected errors remain enabled. This is not a claim that terminal UI/update faults can never recur.

## Health and checks

- API lint/build and compiled migration, full projector, unsafe-case and retry-budget validation passed. Worker: 268 tests passed.
- 10:46:55 UTC read-only preflight: 10/10 fresh, supported and unquarantined slots.
- By 10:47 UTC: 43 successful collections after worker restart, with every slot represented; no collection failures in that observation window. Nine catalogue-refresh maintenance records were followed by successful collection, not outages.
- /health/ready HTTP 200, database up. /health/mt5 snapshot 10:46:42 UTC: status ok, blockedImports=0, lateCollections=0, lateImports=0. Brief deployment-drain delay cleared.

## Sentry disposition

Resolved and visibly verified: **13** worker CONNECTION_FAILED, **14** sync CONNECTION_FAILED, **22** MIGRATION_REVIEW_REQUIRED, **24** WORKER_FAILED. Last CONNECTION_FAILED sync event refers to the recovered FTMO connection; the worker event matches its 20 October initialization failure. Historical issue resolution does not erase account-level attention states or disable regression detection.

Proof screenshot: C:\TradeTrack\runtime\sentry-mt5-after-20261023.png.
API design and primary references: docs/mt5-recovery-20261023.md in tradetrack-api.

## Additional incidents discovered after refreshing the issue list

The list also exposed four incidents from 22 October, distinct from the four originally investigated. Worker logs confirm a genuine API/progress interruption around 18:08–18:12 UTC: only two successful collections at 18:08, none at 18:09/18:11, one at 18:10, then twenty at 18:12 and normal throughput afterward. Automatic recovery worked; this was not a new incident caused by today's deployment.

- **2E MT5_PROGRESS_CHECK_FAILED** (18:09:19 UTC, one event) and **1T MT5_COLLECTION_STALLED** (last 18:12:19) resolved as recovered incidents after current progress checks passed. Monitoring remains enabled.
- **2F**, one expired interactive transaction at 18:13:28 (5,000 ms budget, 5,536 ms elapsed), and **1F**, unable to start a transaction (last 18:13:30), remain open. Both affect jobs/claim. The expired transaction was detected in lockSlot before the admission advisory lock. The shared Prisma pool has max=5. Available evidence does not distinguish upstream DB/network latency, pool saturation, event-loop delay or row-lock contention. No blanket filtering, timeout increase or unverified pool resizing was applied. Root-cause investigation of these two separate issues remains outstanding.
