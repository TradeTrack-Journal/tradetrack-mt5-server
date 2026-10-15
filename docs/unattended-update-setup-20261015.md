# Protected MT5 candidate staging — 15 October 2026

This supersedes the installer-preparation section of the October 14 readiness report. It does not claim that new builds are automatically approved for production.

## Delivered and verified

- Worker commit `10ec874` defers staged LiveUpdate packages before API-fenced managed restarts. It preserves the package and holds exclusive file handles while moving it. Active or inaccessible installers, changing process identities and unsafe paths block recovery. UAC remains enabled.
- A real demo-10 restart with a staged 6259 package started the installed build 6231 without a LiveUpdate handoff and collected 46 deals at 11:34:57 UTC. This verifies the managed restart path, not every possible vendor self-update path.
- The Windows task package stages new vendor versions in a protected directory, without production account credentials. It uses the documented `/auto /path` installation arguments.
- Scripts parse and embedded C# compiles in Windows PowerShell 5.1.26100.9444. A child-process fixture verified the job object's kill-on-close behavior. The official installer downloaded on October 15 had a valid MetaQuotes Ltd. Authenticode signature. Its SHA-256 was `C8C0A73F86C58974DF62FB48F8256A099D6698518CF2DEFC2B3F169773729E22`.

Actual vendor installation under SYSTEM still requires the first elevated setup/run. Compilation and the process fixture do not establish installer success or build compatibility.

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

Success requires `state: candidate_ready`, task result 0, and three signed executable hashes in the candidate manifest. `approvedForProduction` remains false. A failed download, signature, install, timeout or disk check leaves production terminal files untouched. `status.json` records the failing phase and exception type. A successful candidate is not evidence of collector compatibility.

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
