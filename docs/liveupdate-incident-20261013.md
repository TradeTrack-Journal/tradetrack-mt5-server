# MT5 recovery and unattended LiveUpdate handoff — 2026-10-13

## Confirmed observations (UTC)
The worker and watchdog were still running release mt5-70448c1. Five terminals exited on October 12 after IPC quarantine, followed by LiveUpdate /update handoffs. The remaining five exited around October 13 18:18 and also handed off to LiveUpdate. Repeated UPDATE_FILES_BUSY prevented relaunch. The user reported administrator-password prompts. By 19:04, locks began clearing and recovery resumed. The user subsequently confirmed entering the administrator password immediately before this recovery. This was not a fully unattended recovery. No UAC window remained when inspected, so the exact prompt process was not captured.

MetaQuotes documents that pending updates run on the next platform restart and can request administrator credentials with UAC enabled: https://www.metatrader5.com/en/terminal/help/start_advanced/autoupdate

## Immediate recovery
Verified running-process paths and old orphan-package files. Retried the four still-missing slots through the existing guarded start path, retaining the real 60-second observation interval and exclusive-access checks. All ten slots recovered, and every slot performed successful collection. No administrator credential or UAC setting was changed.

## Root defect corrected
Scope correction (October 14): the fenced-stop change removes one handoff path; it does not prevent all vendor update launches. A later demo-02 journal records another LiveUpdate handoff at 08:23:04. Do not represent the October 13 canary as proof that UAC recurrence has been eliminated.

Both worker quarantine recovery and watchdog recovery used normal WM_CLOSE. That intentionally enters MetaTrader's shutdown sequence, which can launch a pending updater and leave the terminal absent behind an unattended UAC prompt. Catalogue maintenance used the same path.

Managed stops now require the slot mutex, a fresh API /config quarantine identity match, the exact live PID/creation identity, and the existing durable restart budget. A pinned process handle terminates only that fenced process without invoking the normal shutdown/update sequence. API fencing prevents active collections; new processes still require fresh inventory. All three managed maintenance paths use this stop. The separate credential-free builder defers graceful close when a staged package exists.

The change does not disable Windows UAC, add administrator rights, suppress credential prompts, or disable broker/software update checks. Full MT5 build upgrades require a separately reviewed maintenance rollout. Manual terminal closure can still invoke vendor update behavior.

## Diagnostics and verification
239 tests passed, including API fence loss, PID reuse, wrong-slot fence, restart cooldown, API failure, builder update guard and existing catalogue preservation tests. Native initialization/login failures now retain only the numeric native error and operation in local worker JSONL; no native error text or credentials are emitted. This supports investigation of the initial IPC failures without claiming an unproven cause.

A production canary on demo-10 at 19:12:52 used the new fenced stop, restarted build 6231 with a new PID, and returned to fresh STARTING inventory without launching an updater or consent.exe. No other slot was interrupted by that canary.

## Deployment verification
Both scheduled supervisors now run release mt5-1dfc9a7 (started 19:16:26 UTC). The old worker drained and exited successfully before replacement. All ten terminal slots have fresh, error-free inventory and no quarantine. Every slot completed real collection after deployment; 38 collections were observed by 19:19 with no new worker errors, and both stderr logs were empty. Nine accounts have recent successful imports.

Admin retries restored MetaQuotes accounts 5057038278 and 5057062152. Exness 252747356 was requeued but is correctly not claimed because its owner's last activity was October 5, beyond the seven-day policy. GoatFunded 451568887 has two native Invalid account rejections and remains AUTH_FAILED. FTMO 531360730 collects successfully but has an independent MISSING_OPEN_HISTORY projection block; no historical data or access safeguards were bypassed.
