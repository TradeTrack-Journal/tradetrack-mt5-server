# Unattended MT5 update readiness — October 14, 2026

## Verified state

At 11:03 UTC all ten production slots had fresh inventory, supported installed builds and no quarantine. demo-01 runs 6246; the other nine run 6231. Nine active staging folders contain package filenames ending in 6259. These filename hints do not establish package authenticity or collector compatibility. The October 14 demo-02 updater handoff recurred despite the October 13 fenced-stop fix.

TradeTrackWorker is a standard Windows user, not an administrator. Both production scheduled supervisors run with Limited rights. The slot folders already grant this user Modify access. Granting more worker privileges is not the missing file-permission fix.

## Implemented part

`scripts.mt5_maintenance_preflight` makes only GET /config and GET /inventory requests, validates the registered slot paths through InventoryAgent, checks fresh inventory, API/local build support and quarantine, and lists active update package version hints. It reads the existing user-scoped DPAPI node token without displaying it. It does not fetch account passwords, execute installers, alter tasks, stop terminals, or approve builds.

Run from this repository with the existing worker virtual environment:

```powershell
& C:\TradeTrack\projects-restored\tradetrack-mt5-server\.venv\Scripts\python.exe -m scripts.mt5_maintenance_preflight --config-directory C:\TradeTrack\worker\production
```

Four regression tests cover missing/duplicate inventory, stale or invalid timestamps, quarantine, unsupported builds, and excluding unexpected private fields. The live read-only check passed for all ten slots. This is readiness monitoring, not an automatic installer.

## Required design before enabling unattended installation

1. Validate a vendor-supported noninteractive update in a credential-free staging terminal. Confirm timeout handling, the final signed binaries, exit state, data paths and process ownership. Do not first experiment on a production account or assume the observed internal `/update` command is a supported unattended installer.
2. If elevation is required, an administrator must provision a narrowly scoped task/helper once. Use a service account that needs no stored personal password (SYSTEM is technically available, but the task's scope must be restricted). Keep its code, manifest, staging directory and executable dependencies in an administrator-owned directory with no worker write access. The helper must not execute arbitrary worker-supplied paths, scripts, command lines, or binaries directly from writable terminal folders. Verify vendor signatures and approved artifact hashes inside protected staging, with bounded execution and no terminal/account secrets.
3. Keep the account collector and production terminals under TradeTrackWorker. Do not restart production terminals as SYSTEM in session 0. Separate privileged staging from ordinary-user rollout.
4. Validate new-build account identity, investor/read-only verification, IPC, catalogue handling and real collection in a canary. Approve only that build in both API and collector after these checks; a valid signature alone does not establish compatibility.
5. Roll out one idle API-fenced slot at a time under its existing mutex and restart budget. Require fresh inventory plus successful collection before proceeding. Stop the rollout on failure; retain the previous signed bundle and account data for a tested rollback. Do not erase quarantine or consent requirements to make readiness pass.
6. Use Sunday 10:00 UTC as the planned window, with notifications only for meaningful change/failure or necessary action. The vendor can also attempt an update at an unscheduled restart; a weekly timer alone cannot prevent that or guarantee no UAC prompts. Define and test this path before calling the system unattended.

## Current blockers and explicit non-changes

The unattended vendor install path and version 6259 are not yet validated. This session cannot provision privileged Windows tasks because it is not administrative. No administrator password has been requested, persisted, typed automatically or passed in arguments. No privileged installer/task has been installed, no UAC setting changed, and no broad build allowlist added. A weekly Codex readiness check is distinct from a Windows maintenance service and depends on the Codex host being available; it must not be advertised as unattended installation.

## Primary references

- https://www.metatrader5.com/en/terminal/help/start_advanced/autoupdate — background download, next-start installation and UAC requests.
- https://www.metatrader5.com/en/terminal/help/start_advanced/start — portable-mode folder permissions.
- https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks — standard users cannot register SYSTEM/elevated tasks.
- https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/register-scheduledtask — service-account tasks do not require storing the personal administrator password.
