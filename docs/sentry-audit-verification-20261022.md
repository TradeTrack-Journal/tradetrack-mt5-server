# Sentry MT5 audit — production verification, 22 October 2026

## Deployed

- API `added9b`: nonblocking claim admission after receipt replay; IEEE-754-scale normal-close tolerance; no per-attempt infrastructure diagnostic duplicates; narrowly filtered aborted completion uploads; standard Sentry logentry.
- API `3df3717`: blocked first imports included in health counts. Fly workflow 39799968089 completed successfully.
- Worker release `C:\TradeTrack\releases\mt5-94a2403`: sustained-fault telemetry gating for transport/IPC/history/catalogue/timeouts; successful watchdog health resets its transient episode.
- Worker and watchdog drained with exit code 0; both restarted at 14:53:10 UTC. No terminal build changes or global terminal restart. Rollback release: mt5-28bafad; task XML backups in C:\TradeTrack\runtime\20261022-TradeTrack-*.xml.

## Verification

- 267 worker tests passed. API lint/build and compiled checks passed (numeric artifacts versus material over-close, partial/reversal accounting, receipt replay under contention, scoped filtering and critical alert preservation).
- 15:03 UTC: 10/10 fresh, unquarantined supported slots; all ten collected after rollout. 182 successful collections since worker restart.
- Five brief UI inspection/cleanup failures occurred during the observation window; subsequent collection and fresh heartbeat recovered. They are not proof of a persistent outage, and this change does not claim to eliminate every UI failure.
- Ten actively importing connections had fresh collection/import timestamps 9–37 seconds old and zero current failures. RoboForex collected 14 seconds ago but remains blocked as described below.
- API /health/ready: HTTP 200, database up. /health/mt5 now correctly reports MT5_IMPORT_BLOCKED, blockedImports=1, collectionStatus=ok, zero late collections/imports.
- Sentry received the new genuine migration-review event after deployment; delivery is working. No artificial test error was sent.

## Sentry disposition

Resolved and visibly verified 16 issues: 2B, 2C, 2D, 1P, 1S, 20, 1T, 27, 26, 1Q, 23, 1W, 2A, 29, 28, 25.
These are fixed code paths, duplicate transient events or recovered incidents. Persistent-fault and integrity monitoring remain enabled; resolving an incident does not prevent regression detection.

Four remain unresolved:

- **22 MIGRATION_REVIEW_REQUIRED — RoboForex-Pro.** The INVALID_CLOSE_VOLUME artifact is fixed and the reducer now reaches legacy identity mapping. The existing journal contains 3,454 statement rows; 2,589 position IDs are absent from available raw broker history. Missing rows span 27 April–17 July; 865 matching positions span 20 July–21 October. Raw evidence starts 20 July and the completed backfill has an older-history-empty count check. This proves missing evidence for migration, not the reason the broker/native API omitted it. Keep original statement rows and annotations unchanged. Obtain/recheck older broker history and validate identity mapping before enabling projection; do not manufacture missing deals or silently bypass the mapping gate. No production trade rows were manually edited.
- **14 and 13 CONNECTION_FAILED.** Generic failure does not identify bad credentials versus broker restrictions versus transport. Account-level paused failures still exist; do not resolve them as noise or label them suspended without authoritative evidence.
- **24 WORKER_FAILED.** Two historical events in release 42ce019, last seen five days earlier; the sampled correlated local records do not establish the exception cause. No new WORKER_FAILED observed in the subsequent daily worker logs. Keep open rather than assert an unproven fix or suppress unexpected exceptions.

Proof: C:\TradeTrack\runtime\sentry-mt5-after-20261022.png (four remaining issues).
Implementation plan and primary references: API docs/mt5-sentry-audit-20261022.md.
