# Bybit catalogue preparation, 19 November 2026

Baseline: ten healthy production slots, 459 exact names each, no Bybit names.
Bybit-Live-7 was therefore correctly rejected as unresolved before account
authentication. The user requested all available Bybit servers, with careful
live rollout.

Credential-free discovery in the dedicated 6231 builder returned 469 names:
all 459 previous names plus Bybit-Demo, Bybit-Demo-2, Bybit-Live,
Bybit-Live-2, Bybit-Live-3, Bybit-Live-4, Bybit-Live-5, Bybit-Live-6,
Bybit-Live-7 and BybitX-Live. The latter is a separate regional name and must
not alias Bybit-Live. No account was created or authenticated for discovery.

A second isolated builder using the existing 6246 binary loaded the prepared
public catalogue and independently confirmed all 469 exact names via the
Login server control. The production 6246 slot receives only this separately
verified artifact. No credential/configuration files other than servers.dat
are propagated.

During preparation, transient builder UI errors required an isolated restart.
Production catalogues had not yet been changed. API collection/import health
remained OK, although existing intermittent UI_DIALOG_UNAVAILABLE events were
observed in the production worker. No claim of uninterrupted UI health.

Pre-rollout review found two propagation defects:
- Restart-budget rejection occurred after API quarantine, unnecessarily removing
  a healthy slot from capacity. Reserve the durable budget before fencing;
  stop_fenced_terminal rechecks the same identity idempotently. If the API
  refuses preparation the budget stays conservatively reserved.
- Pair selection attempted a cross-build catalogue copy and failed rather than
  selecting a compatible slot. Skip incompatible pairs; final installation still
  revalidates the actual build and process under the slot mutex.

294 tests pass, including cooldown-before-fence and mixed-build selection.
Rollout uses existing API fencing, exact-process checks, backup, bounded restart
budgets, and fresh positive worker reports. Runtime artifacts and per-slot
verification are under C:\TradeTrack\runtime\bybit-20261119.

Discovery confirms server availability in MT5, not a particular customer's
credentials or authorization. A user whose earlier request was rejected before
credentials were saved must retry Connect after catalogue propagation.
