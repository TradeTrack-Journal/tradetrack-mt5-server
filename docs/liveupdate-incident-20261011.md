# MT5 outage recovery — 2026-10-11

## Incident
At 21:39 UTC all ten managed terminal processes were absent. The worker and watchdog remained alive, but every slot was quarantined after TERMINAL_IPC_UNAVAILABLE. Native journals show terminal exits during October 9–10, followed by LiveUpdate handoffs. The last terminal stopped on October 10 at approximately 19:27 UTC. The initial IPC failure cause is not yet established.

The October 8 recovery guard introduced a regression: an orphaned liveupdate/terminal64.exe blocked launch indefinitely when its updater process was absent. Repeated UPDATE_PROCESS_UNVERIFIED alerts did not remove this deadlock.

## Immediate recovery
Verified the managed origin mapping, process absence, package age, and exclusive access. Preserved orphaned packages in sibling liveupdate-orphan-held-* directories. Restarted all ten installed terminals at 21:40–21:41 UTC. Successful production imports resumed. No credentials, account data, consent gates, or database statuses were altered.

## Permanent correction
Under the existing per-slot mutex, require absent main/updater processes, two identical file fingerprints at least 60 seconds apart, and files at least ten minutes old. Acquire Windows handles denying read/write sharing to the installed executable and all package files, recheck process absence and fingerprints, then archive package files while keeping handles held. Move the updater executable last so interrupted archival remains blocked safely. Preserve the installed executable and all downloaded bytes. Locked, changing, inaccessible, or unsafe paths remain blocked and observable. Unreadable packages no longer fail open.

## Verification
232 unit tests passed, including new orphan, active updater, locked file, changing file, late process, and partial archive cases. A separate Windows integration check used real CreateFileW handles and verified archival plus byte preservation. Windows file rename works with DELETE sharing; directory rename with open children does not, hence the per-file archival.

## Limits
This repairs the observed automatic-recovery deadlock; it does not establish the original cause of every IPC failure. Visible or inaccessible live updater processes remain protected. New MT5 builds still require compatibility validation. A restart cannot fix invalid credentials or missing owner access confirmation.
