# MT5 regressions, 17 November 2026

## Evidence before the change

- Sentry 2K and 18 identify demo-02. Worker 11:23:55 UTC reports
  UNSUPPORTED_TERMINAL_UI, followed by TERMINAL_UI_BUSY through 11:28:27.
  The watchdog fenced at 11:28:13 and restarted at 11:28:15.
- The VPS is about 9 seconds behind the API. Of 11 UI-error watchdog
  observations on November 17 up to 13:25, two have negative heartbeat age
  using the old local-clock comparison. In particular demo-02 at 11:25:11
  has age -7.65 seconds. This resets the sustained-failure observation window
  and delays recovery. This is a confirmed recovery defect, not proof of the
  initial dialog failure's cause.
- Sentry 1T's latest event is 11:58:41 API UTC, during a separate sequence
  of broker service-unavailable replies and native IPC -10005 failures.
  Restart budgets were active. 1X is November 15 06:31:39, not a new failure
  on November 17. The corresponding logs show widespread IPC failures and
  cooldowns. These capacity/progress alarms represent real delays.
- 2V is demo-02's locked update files, which recovered via existing retries.
  Do not remove update locks or install unverified builds to silence it.
- Temporary UI_DIALOG_UNAVAILABLE observations around 13:20 recovered without
  intervention by 13:22. API health at 13:23 is OK, zero late/blocked imports.
- TermService PID 4180 has about 7 million handles, versus 5.95 million noted
  November 5. Windows Time reports unsynchronized. These host-level findings
  need separate maintenance; a causal link to individual UI failures is not
  established. No service restart, UAC change or OS clock mutation is made.

## Changes

1. Watchdog compares API heartbeat timestamps with the Date of its existing
   authenticated HTTPS inventory response. Duration uses monotonic time.
   The response sample expires after 120 seconds; invalid/missing dates prevent
   UI fencing but do not disable already-quarantined recovery. One second of
   HTTP Date rounding tolerance; stale heartbeats still fail closed. Identity,
   generation, active-job fences and durable restart budgets are unchanged.
2. Give Login controls up to five seconds to finish construction (previously
   one). Missing controls are UI_TIMEOUT, eligible for bounded retry/recovery;
   populated incompatible controls remain UNSUPPORTED_TERMINAL_UI. No partial
   catalogue is accepted, and cancellation/process-identity checks remain.
   Slow construction is a plausible source of 2K, not retrospectively proven
   because the historical event lacks the failed control details.
3. UPDATE_FILES_BUSY uses the existing sustained-error reporting gate: at least
   three observations spanning 180 seconds. A successful restart clears the
   pending event. Persistent failures still alert; short recovered locks stay
   in local logs. Real capacity and collection alarms remain enabled.

## Validation

292 tests pass, including clock-skew recovery, stale/missing response clocks,
delayed and incompatible controls, cancellation, and transient versus persistent
update locks. Production rollout and subsequent observations are recorded in
the task's runtime deployment/verification artifacts. These tests do not prove
50-account capacity or eliminate broker/network outages.

References: [Python monotonic clock](https://docs.python.org/3/library/time.html#time.monotonic),
[Windows timeout semantics](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw).
