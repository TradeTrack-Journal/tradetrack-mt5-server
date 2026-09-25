# Production quarantine recovery

## Incident and evidence

On 2026-09-21 around 19:31 UTC, demo-01 and demo-02 stopped with
API_HTTP_500. The supervisor restarted the controller at 19:33:18. On September
25 their API inventory still had the September 21 heartbeat, LEASE_EXPIRED,
and the original process identities in quarantine. The other three slots were
still collecting. The backend exception behind those historical HTTP 500s is
not available in the local worker log, so its underlying cause is unconfirmed.

The controller only loaded quarantineIdentity in connect(). Server-side lease
expiry can happen after that call. A later quarantine was therefore not adopted
as restart intent; inventory errors were retried indefinitely without another
config refresh. The worker now refreshes that durable state every 60 seconds
between collections. The controller was replaced at 11:19 UTC after acquiring
all five slot mutexes and confirming no BUSY API slots. The supervisor started
the updated controller at 11:20; working terminals were not restarted.

## Live independent recovery

Windows task `TradeTrack MT5 Health Watchdog` runs
`scripts/Start-HealthWatchdog.ps1` under TradeTrackWorker's interactive session.
It polls every 45 seconds and uses the existing DPAPI node credential. It does
not fetch account credentials, claim jobs, change consent or retry paused
accounts. It is separate from the controller, so the recovery is already active
without disrupting current jobs or restarting the working controller.

During verification, demo-02 resumed successful collection after its 11:09
restart. Demo-01 did not finish graceful shutdown; the verified fallback
completed its restart at 11:10. MT5 then launched a silent updater and exited.
The watchdog independently observed unchanged updater files for 600 seconds,
recovered it at 11:22:35, and preserved `liveupdate-held-1790335355380039900`.
Demo-01 rejoined the normal claim loop at 11:23:17 and completed collection
(21 deals) at 11:23:56. All five slots have successful collected results after
the controller rollout; API readiness reports ok/db up.

The production `Start-Worker.ps1` also now uses `start_terminal` rather than a
direct launch, so supervisor startup respects the same updater guard.

For each quarantined slot, it:

1. Acquires the same executable-specific mutex used across the collector job.
2. Refreshes API config under the lock and validates configured paths.
3. Restarts only the process whose identity equals the server quarantine.
4. Attempts graceful close first. If it times out, refreshes the server fence
   again and terminates only a pinned, verified process handle.
5. Leaves an already-replaced process for the controller to report. It never
   manually clears the database fence.
6. Defers to the existing updater guard: a missing terminal's silent updater
   must have unchanged identity and file fingerprints for 600 seconds before
   termination. A visible updater or file progress prevents recovery. Update
   cache is preserved, never deleted.

The API atomically releases a lease when setting quarantine and prevents further
claims on that process. The mutex also prevents interference with a local
native child. Recovery attempts back off, and the task restarts after failures
with a one-minute delay. A named launcher mutex and IgnoreNew task setting
prevent duplicate watchdogs.

For a new installation, run `scripts/Install-HealthWatchdog.ps1` as the same
Windows user that owns the existing DPAPI token and MT5 desktop session. The
installer refuses to replace an existing task.

## Diagnostics and limitations

`C:/TradeTrack/worker/production/health-watchdog.jsonl` is directly appended,
with UTC timestamps, snapshots and recovery outcomes. It remains observable
even while PowerShell's redirected native stdout file is empty. Future
controller processes also write daily `worker-YYYY-MM-DD.jsonl` directly.

HTTP 500 now gets the same bounded, identical-payload retry as 502/503/504.
Failures retain a sanitized operation name (e.g. complete) without logging
response bodies, tokens, account passwords or arbitrary request URLs.

An unresolved API exception still needs backend/Sentry evidence. This recovery
does not prove every user's journal import or restart paused connections.
The tasks need an interactive Windows logon after a reboot, like the existing
MT5 supervisor. Unknown dialogs remain fail-closed; the watchdog does not
dismiss arbitrary dialogs or kill healthy terminals.

For maintenance, disable future runs of `TradeTrack MT5 Health Watchdog` and
stop only its verified Python controller while it is between recovery attempts.
Do not kill its entire descendant tree: a recovered terminal may have been
launched by it. Do not stop the production worker or all terminal64 processes.

Validation: 142 Python tests pass, including late quarantine, expired process
identity, busy mutex, changed API fence, bounded HTTP 500 retry and existing
silent-updater safeguards. Both new PowerShell scripts parse successfully.
