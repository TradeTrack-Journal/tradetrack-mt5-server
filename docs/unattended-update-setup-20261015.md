# Protected MT5 candidate staging — 15 October 2026

This supersedes the installer-preparation section of the October 14 readiness report. It does not claim that new builds are automatically approved for production.

## Delivered and verified

- Worker commit `10ec874` defers staged LiveUpdate packages before API-fenced managed restarts. It preserves the package and holds exclusive file handles while moving it. Active or inaccessible installers, changing process identities and unsafe paths block recovery. UAC remains enabled.
- A real demo-10 restart with a staged 6259 package started the installed build 6231 without a LiveUpdate handoff and collected 46 deals at 11:34:57 UTC. This verifies the managed restart path, not every possible vendor self-update path.
- The Windows task package stages new vendor versions in a protected directory, without production account credentials. It uses the documented `/auto /path` installation arguments.
- Scripts parse and embedded C# compiles in Windows PowerShell 5.1.26100.9444. A child-process fixture verified the job object's kill-on-close behavior. The official installer downloaded on October 15 had a valid MetaQuotes Ltd. Authenticode signature. Its SHA-256 was `C8C0A73F86C58974DF62FB48F8256A099D6698518CF2DEFC2B3F169773729E22`.

Actual vendor installation under SYSTEM still requires the first elevated setup/run. Compilation and the process fixture do not establish installer success or build compatibility.

### First SYSTEM run, 19:13–19:14 UTC

The administrator installed the task and its protected helper successfully. The task downloaded and unpacked signed build **6266** into its isolated directory. All three executable signatures were valid. The original helper recorded `failed` at `install-candidate` with a RuntimeException; its code rejected a nonzero installer exit but failed to retain the numeric value. Therefore the exact original exit code and its meaning are not established, and this is not a successful compatibility validation.

A subsequent standard-user installer probe exited 0 without creating its requested terminal folder; a user-reported password prompt coincided with that probe. The installer has an `asInvoker` manifest, but this does not rule out an explicit elevation request. Do not repeat standard-user vendor installer probes. No production terminal PID changed during this probe, and the live inventory showed 10 healthy slots at 19:14 UTC.

On October 16 the user confirmed the prompt said **MetaTrader 5 Setup** and that they entered the administrator password. The standard-user probe must therefore not be counted as an unattended installation success. At 12:05–12:06 UTC all ten production slots were healthy and had recent successful collections, still on 6246/6231. The protected helper retained its original SHA-256 (`4FD77B99...`), and status still showed the October 15 19:14 failure: the r2 repair had not been applied. No installer or consent process was visible during this check. The probe's requested terminal directory was still absent; the existing `C:\Program Files\MetaTrader 5` binary remained build 6190 dated September 10. These observations do not establish the full outcome of the elevated probe.

The revised helper records the actual installer exit, failing phase and line. It verifies and records signed candidate files even on nonzero exit, labeling them `candidate_requires_review` with task exit 2 and `approvedForProduction: false`. It does not reinterpret undocumented nonzero exits as success. A further correction grants the worker read-only task inspection, without execute or modification rights; the original task's default ACL hid it from the standard user.

After reviewing the revised package, update the installed helper from an administrator PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "C:\TradeTrack\handoff\mt5-maintenance-20261015-r2\Install-MT5Maintenance.ps1" -Repair
```

Repair verifies the exact existing SYSTEM action, inactive task, protected owner/ACL and known helper hash, retains a backup, installs the newly pinned helper and starts one new isolated run. It does not change the Sunday schedule or production configuration. This elevated repair is necessary because the helper is deliberately not writable by the collector user.

## One-time setup

Review `scripts/Install-MT5Maintenance.ps1` and `scripts/Stage-MT5Update.ps1`. Keep them together. The installer pins the helper's SHA-256 and verifies it again after copying it into the protected directory.

On the VPS, open Windows PowerShell **as Administrator**, then run the prepared package:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "C:\TradeTrack\handoff\mt5-maintenance-20261015\Install-MT5Maintenance.ps1"
```

Windows requires administrator privileges to register a SYSTEM task. No password is read, stored or entered by these scripts. The task uses SYSTEM's built-in service-account context. The installer deliberately refuses to overwrite an existing task or directory; inspect partial failures before attempting installation again.

Created task: **TradeTrack MT5 Candidate Staging**. Schedule: **Sunday 10:00 UTC**, with one immediate staging run during setup. There is no late catch-up run. Repeated invocations cannot overlap. It runs independently of Codex and an RDP session.

Protected root: `C:\Program Files\TradeTrack MT5 Maintenance`. Only SYSTEM and Administrators can write there; ordinary users can inspect results. Its executable script never accepts worker-provided paths, arbitrary commands or production credentials. Downloads require HTTPS without redirects, a size/time limit and a valid MetaQuotes signature. The installer runs in a kill-on-close job with a ten-minute limit; the task has a twenty-minute limit. A five-GB free-space guard prevents staging from exhausting the disk. Previous candidates are retained for inspection; disk usage needs monitoring.

## Verify the first run

```powershell
Get-ScheduledTaskInfo -TaskName 'TradeTrack MT5 Candidate Staging'
Get-Content 'C:\Program Files\TradeTrack MT5 Maintenance\status.json'
Get-Content 'C:\Program Files\TradeTrack MT5 Maintenance\latest-candidate.json'
```

Success requires `state: candidate_ready`, task result 0, and three signed executable hashes with matching versions in the candidate manifest. `approvedForProduction` remains false. `candidate_requires_review` and task result 2 preserve a nonzero vendor outcome for investigation, even when all signed binaries are present. A failed download, signature, install, timeout or disk check leaves production terminal files untouched. `status.json` records the failing phase, exception type, installer exit when available, and source line. A successful candidate is not evidence of collector compatibility.

## Production rollout gate

The production worker remains a standard-user process. Do not launch production terminals as SYSTEM. Before promotion, validate authenticated IPC, account identity, read-only access, server catalogue handling and actual collection on one fenced canary. Permit the specific build in both API and collector only after validation, then roll out one idle slot at a time with backups, existing restart budgets and collection checks. Stop and roll back on failure.

Build 6259 is **not approved** by this package. A credential-free initialize probe timed out, which is inconclusive rather than proof of incompatibility. Automatically allowing every signed new build would skip the required checks. Fully automatic production rollout is therefore not activated by the staging task.

## Incident evidence

At about 02:26 UTC on October 15 the VPS logged API_UNAVAILABLE; expired leases subsequently quarantined slots. Controlled restarts at approximately 02:37–02:47 triggered pending LiveUpdate on the next start. The user confirmed entering an administrator password around the daytime recovery. demo-05 and demo-04 reached unapproved build 6259. This recovery was not wholly automatic. The prior forced-stop-only mitigation did not cover update installation on the following start; the new deferral covers that managed path. The root cause of the earlier API reachability failure is not established by these observations.

## References

- https://www.metatrader5.com/en/terminal/help/start_advanced/installation
- https://www.metatrader5.com/en/terminal/help/start_advanced/autoupdate
- https://www.metatrader5.com/en/download
- https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks
