# MT5 LiveUpdate incident and recovery plan — 2026-10-08

## Evidence (UTC)

- 14:26: MetaQuotes-Demo returned `Service is not available` for two jobs.
- 14:28–14:29: IPC-fenced demo-01/05/06/07 restarted. Their native journals show LiveUpdate handoff immediately after a clean shutdown.
- Repeated launch attempts then logged `failed to create copy ... [32]`. Windows defines 32 as a sharing violation. The exact process holding the file was not captured during the incident; do not attribute it to RDP or a password.
- 15:19: all four processes reappeared; demo-01 had build 6246, while the other three remained on 6231. We cannot establish from these logs whether a person resolved an updater prompt.
- The other six slots completed 580 collections during 14:28–15:19. This was partial capacity loss, not a complete import outage.
- Build 6246 was correctly rejected by the previous allowlist pending review.

## Plan and implementation

1. Review the signed 6246 binary on one existing canary. Verify exact process/path, readable server selector and read-only caption, then validate real collection with the existing native identity/access gates. Do not accept arbitrary future builds.
2. Make launch idempotent under the same named slot mutex used by worker and watchdog. Recheck a live terminal before launching. Persist a 60-second launch grace period so startup races survive a caller restart.
3. Treat an unfinished `liveupdate/terminal64.exe` as blocking evidence even if process enumeration cannot verify its PID. This closes a fail-open path; it does not prove that path caused every incident retry.
4. Keep existing bounded recovery only for an unchanged, silent, positively identified updater. Never terminate an unknown process or click an arbitrary update/UAC dialog. Persistent visible or unverifiable updater state raises a specific diagnostic after ten minutes of unchanged evidence.
5. Apply a durable shared restart budget to graceful and forced recovery: at least 120 seconds between different terminal restarts and 900 seconds between replacements of the same slot. Rechecking the same fenced process is idempotent. Corrupt budget state fails closed.
6. Deploy worker and watchdog from the same immutable release. Both use the Python supervisor and DPAPI-loaded credentials; no PowerShell startup dependency. Watchdog respects the production drain marker and inherits existing Sentry configuration. Existing telemetry deduplication applies to the new diagnostic codes.
7. Deploy API build support before the worker. The first canary completion exposed the API's independent build allowlist (HTTP 400). API config now advertises `terminalBuilds` directly from its result schema; the worker reports incompatible slots as unready before claiming a job. Older APIs without this capability remain backward compatible. Native access and account identity checks still apply.

## Validation and operational limits

225 unit tests passed before deployment, including updater visibility, orphaned update files, startup race, durable cooldown, corrupt state and exact read-only build checks. Canary UI: MetaQuotes signature valid, 459 server names, FundingPips read-only Netting caption. Record production collection evidence after rollout.

Unknown future builds still require compatibility review. An interactive/elevated updater may require operator action; the controller must alert and preserve other slots instead of bypassing it. These changes bound recovery and prevent the identified relaunch path; they cannot guarantee that third-party MT5 updates never fail. Never copy an executable over a live terminal or delete account configuration to resolve an update.

## Primary references

- [MetaQuotes Live Update](https://www.metatrader5.com/en/terminal/help/start_advanced/autoupdate): built-in updates cannot be disabled; deferred updates apply on restart.
- [MetaQuotes Python initialize](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py): initialization can launch the terminal, so process/path checks are still necessary around native calls.
- [Microsoft error codes](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-): error 32 is `ERROR_SHARING_VIOLATION`.
