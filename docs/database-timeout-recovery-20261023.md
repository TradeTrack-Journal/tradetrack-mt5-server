# MT5 database timeouts — deployed verification, 23 October 2026

## Outcome

API commit `3d7a984` deployed successfully by Fly workflow https://github.com/TradeTrack-Journal/tradetrack-api/actions/runs/39967333143. No worker/terminal restart, database migration, credential change or pool-size increase was needed.

## Cause and correction

Inventory reports and session registration held an exclusive lock on the shared Mt5Node row while processing an entire slot catalogue. A slow report therefore blocked unrelated claim/completion transactions that require a shared node lock. Those waiting transactions consumed the five-connection pool. The isolated PostgreSQL reproduction generated both observed P2028 variants: expired transaction and inability to start a transaction. The initiating upstream delay in the historical October 22 incident remains unproven; the code-level amplification mechanism was reproduced and removed.

Reports now share-lock the node for disable fencing and exclusively lock only their slot. Node heartbeat refresh runs separately after commit with SKIP LOCKED and a 30-second cadence; it cannot revive a disabled node. Lease recovery also uses a shared node lock. Pool establishment/acquisition has a finite timeout, and progress logs now include safe pool counts and failure duration/code. Genuine Sentry timeout reporting remains enabled.

## Verification

- API lint/build passed.
- Real isolated Neon branch test: a 7.5-second report previously caused one pool-acquisition failure at 2,013 ms and four transaction-expired failures around 7.3–7.5 seconds.
- After correction: nine independent slot operations completed in 132–239 ms while the slow report remained open; heartbeat skipped the occupied row in 28 ms. Ten concurrent complete claim calls finished in 100–384 ms, assigning exactly one eligible job.
- Exact claim receipt replay, duplicate report acknowledgement, monotonic sequence, session fencing, disabled-node fencing and no resurrection all passed. Pool returned to 5 idle / 0 waiting; disconnect closed all clients.
- Isolated branch br-wild-cherry-ag6sp61i expires October 24 at 14:00 UTC, with 300-second idle suspension. Local DPAPI test credential was removed after verification. Production data was not used for synthetic mutation or lock testing.
- Production at 14:12:45: 10/10 fresh supported unquarantined slots. New `tradetrack-api` application_name visible on production DB sessions.
- Observation from 14:12 through approximately 14:15 UTC: 58 successful collections, all ten slots represented, zero worker API errors. Two transient UI errors on demo-03/demo-09 subsequently collected successfully; they were not database timeout recurrences.
- /health/ready: HTTP 200, database up. /health/mt5 at 14:14:42: ok, blockedImports=0, lateCollections=0, lateImports=0. Neon stalled-queries check found zero active queries older than 30 seconds.
- RoboForex remains IMPORTED with 3,506 trades and zero failures. FTMO remains IMPORTED with fresh progress and zero failures; its trade count advanced from six to seven.

Sentry 1F (147951204) and 2F (155541397) resolved after verification; both last occurred about 20 hours earlier. Resolution leaves regression detection enabled. Screenshot: C:\TradeTrack\runtime\sentry-mt5-db-timeouts-resolved-20261023.png.

Implementation details, references and experiment limits: tradetrack-api/docs/mt5-database-timeouts-20261023.md.
