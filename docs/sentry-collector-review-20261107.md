# MT5 collector review — 7 November 2026

## Evidence and limits

The user supplied a current Sentry screenshot showing unresolved issues 2H
(Prisma queryRaw, last seen two weeks ago), 1W (history reconciliation, about
28 minutes ago), 1T (collection stalled, about 38 minutes ago), and 14
(connection failed, about 22 hours ago). Both Codex browser and native UI
tools failed to initialize with `failed to write kernel assets`; no Sentry
API token was available. **No issue was resolved or its event details read
in this review.** The screenshot alone cannot identify the affected account
for 14 or the SQL cause for 2H.

Production logs show five quarantined terminals between 11:43 and 11:46 UTC.
Four of their native journals explicitly record `no connection to
MetaQuotes-Demo` immediately before the IPC failure. Recovery was bounded
by the existing restart budget and finished by approximately 11:54. This is
evidence of broker connectivity trouble, not proof of the broker's internal
cause. No matching Windows Application error was returned for 11:30–12:00.

MetaQuotes-Demo account ending 8278 still authorized successfully across
multiple slots, but returned no history. Its checkpoint first appeared at
11:51:47 API UTC. All 17 earlier raw records remain in the database. Three
targeted attempts plus a full replay had not recovered them by 12:28;
the next automatic attempt was scheduled for 13:04. The journal remains
HISTORY_RECONCILIATION_REQUIRED. Do not erase its checkpoint, old deals or
infer that the broker deleted/closed the account. No account or trade data
was modified manually. 1W remains actionable; a healthy collector does not
mean this account's import is healthy.

## Fix and rollout

Worker release **4e37b99** fixes a confirmed diagnostic loss: after a native
failure was acknowledged by `/fail`, the worker replaced its code with
TERMINAL_QUARANTINED and discarded numeric details. It now preserves the
original cause, allowlisted operation and bounded numeric native code through
the scheduler and telemetry. Quarantine, lease fencing, restart budgets,
transient filtering and six-hour event deduplication remain in force. No raw
native message, credentials or account payload is added to telemetry.

277 tests passed, including failure-path fencing/no completion, scheduler
propagation, rejection of unsafe diagnostic values and suppression after
transient recovery. Both production children drained with exit code zero.
The isolated real terminal inventory canary read all 459 names successfully.
Both limited-user scheduled supervisors started release 4e37b99 at 12:29:56
VPS UTC; no terminal build or Windows security setting changed.

At 12:32:45 API UTC, all 10 slots had fresh heartbeat, supported builds and no
quarantine. After activation, 47 successful collections covered all ten slots
with no worker errors in the observation window. API collection health was
OK with zero late collections/imports; one history-blocked import remained.
This is a short verification window, not a promise against future outages.

## Additional resource finding

Disk C had only about 1.5 GiB free. Closed, date-named worker/terminal logs
were selected for native NTFS compression without removal or renaming.
The per-file SHA-256 manifest and completion result are kept outside Git at
`C:\TradeTrack\runtime\mt5-closed-log-compression-20261107.json`.
Active logs and account-history databases are excluded. Compression completed
for 559 files; every SHA-256 matched. Logical size 1,989,513,710 bytes now
occupies 609,144,832 bytes. Overall free space nevertheless fell to about
1.0 GiB during the session, so compression does not resolve disk capacity.
A metadata-only scan excluding dependency folders found 30 GiB in worker
data, but no newly modified file above 50 MiB besides the existing watchdog
log; the source of concurrent disk consumption is not established.

Windows clock remains approximately eight seconds behind API time, a known
separate finding. No unprivileged workaround changed clock or lease limits.

Reference: [MetaQuotes native error codes](https://www.mql5.com/en/docs/python_metatrader5/mt5lasterror_py)
distinguish internal IPC errors from authorization failures.
