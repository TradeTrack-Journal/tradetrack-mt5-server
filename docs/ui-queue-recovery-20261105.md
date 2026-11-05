# 2026-11-05: MT5 inventory under Windows message-queue pressure

At 15:12 UTC only four of ten production slots passed preflight, despite ten
running MT5 processes. From 15:10 to 15:20 there were 67 successful collections
and 55 UI errors (23 cleanup failures, 19 timeouts, eight unavailable dialogs,
four command failures, one busy terminal). The MT5 builds remained 6246/6231.

## What was reproduced

- A harmless WM_NULL using PostMessageW failed with Win32 1816 on demo-04 and
  demo-07. SendNotifyMessageW succeeded against the same HWNDs in the same
  probe. Windows documents the per-thread posted-message quota. Acceptance of
  a notification does not prove the terminal has processed the command.
- Under load, SMTO_ABORTIFHUNG on demo-01's verified Server combo returned failure
  in 1 ms with no extended error. A bounded send without that heuristic on the
  same control succeeded in 90 ms. The sequential path then read only 238 of 459
  entries in its 20-second budget. This was a real timeout with valid replies.
- A blocked inventory operation prevented dispatch on another healthy slot in
  the old production scheduler. The new regression test failed on the old release.
- RDP was active and all terminals were in its worker session. A reconnect is
  recorded at 15:06, but UI failures predate it. The source of the flood of
  posted messages has not been identified. TermService had about 5.95 million
  handles; this is a separate observation, not proof that it caused this incident.
  The single-window minimization experiment did not resolve the fault and was reverted.

## Changes and checks

1. Isolate serialized inventory work from dispatch. Reserve slots for inventory
   or broker catalogue work, never claim a slot during maintenance, and finish
   in-flight work during drain. Reuse only a positively identified managed
   Login dialog, preserving exact PID, owner and control checks.
2. Send scalar WM_COMMAND notifications directly to the verified target. Keep
   bounded confirmation of dialog appearance, validated controls and dismissal.
   Never submit login; no change to UAC, queue limits or account permissions.
3. Use SMTO_BLOCK | SMTO_ERRORONEXIT with a three-second timeout for the Server
   combo reads. Read the catalogue with at most four outstanding requests, a
   20-second budget, and per-item length checks. Any lane failure aborts the
   entire snapshot; all readers exit before cancellation. Count, process,
   build, hash and native account checks remain in place.

The complete suite passed (273 tests). A real demo-01 probe with the final
reader obtained all 459 names and closed in 7.28 seconds. Each rollout drained
the production worker and watchdog before changing the existing limited-user
task actions. Final canary demo-04 verified 459 names. Both supervisors started
release 94ac38f at 15:34:39 UTC. No production credential or trading record
was edited by the rollout.

## Live verification and limits

From activation to 15:41:25 worker time: 104 successful collects across all ten
slots. No UI_COMMAND_FAILED, UI_CLEANUP_FAILED or UI_TIMEOUT in that window.
Three UI_DIALOG_UNAVAILABLE events remained; do not suppress them. demo-02 and
demo-10 demonstrably returned to normal scheduling without intervention after
their first two events. Preflight at 15:41:17 API UTC was 10/10. The API at
15:40:35 UTC recorded no blocked/late imports or late collections. This is a
short incident observation, not a guarantee or the 50-account capacity test.

Separately the journal intermittently held Exness and HolaPrime connections in
HISTORY_RECONCILIATION_REQUIRED; both returned to IMPORTED with no outstanding
ticket checkpoint, without changing credentials or rewriting trade history.

The VPS was about 8.77 seconds behind time.windows.com in two independent NTP
samples. Windows Time reported unsynchronized; requesting a service resync
returned Access Denied. The read-only weekly preflight now compares server
heartbeats to the Date header of its own authenticated inventory response and
reports the offset; it keeps its original stale/future checks. This diagnostic
change is used from the maintenance worktree, not a clock or lease override in
production. The suite with this additional diagnostic test passed 274 tests.

References: [posted queue limits](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-postmessagew),
[asynchronous cross-thread notification](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendnotifymessagew),
[timeout flags and hung-thread detection](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw).
