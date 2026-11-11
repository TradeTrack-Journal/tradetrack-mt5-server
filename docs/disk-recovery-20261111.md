# Disk exhaustion and lossless archive maintenance — 11 November 2026

## Confirmed cause

C: had 17,760,256 free bytes at the initial check and hit zero again during
the investigation. The watchdog traceback at 10:09 contains Errno 28 at its
log append. This is evidence of a real production recovery-loop failure.

Deferred vendor update archives in the worker user's Roaming MetaQuotes
directory contained 1,061 files / 45,946,196,077 logical bytes. The recovery
code intentionally moved each pending package into a new timestamped archive
before a fenced terminal restart, but never coalesced repeated packages.
Copies accumulated across slots and repeat downloads of the same build.
For example, archives created Nov 4–9 added approximately 2.10, 1.83, 1.28,
1.40, 2.40 and 0.98 GiB respectively. These are installer packages, not deals.
MT5 account/market bases occupy about 24 GiB separately and were not cleaned.

## Preservation and cleanup

The allowed scope is only 32-hex profile directories, dated
liveupdate-held / liveupdate-orphan-held / liveupdate-deferred directories,
and the explicit vendor package filenames or terminal64.exe within those
archives (including their temp subfolder). New archives/files are left alone
for at least one hour. Active liveupdate, all bases, credentials, configuration,
trade data, unknown files and junction/symlink paths are excluded.

Each eligible file is hashed with SHA-256 while a Windows handle denies
writes. A content-addressed object is retained; exact duplicate paths become
NTFS hard links to it. Originals are held by an additional link until the
replacement is fully rehashed and verified. Unique versions are retained,
all original paths remain readable, and archive objects become read-only.
**Copy an archived file to a separate installation directory before changing
or using it for an update.** Hard links share content and attributes.

Evidence outside Git:
- `C:\TradeTrack\runtime\mt5-archive-inventory-20261111.json`: initial path/size inventory.
- `C:\TradeTrack\worker\update-archives\manifest.jsonl`: replacement hashes and original timestamps.
- `C:\TradeTrack\worker\update-archives\objects`: canonical archived content.
- `C:\TradeTrack\worker\update-archives\status.json`: last completed housekeeping pass.
- `C:\TradeTrack\runtime\mt5-archive-dedup-20261111.log`: initial pass progress.

The npm cache deletion command was rejected by automatic approval policy.
It was not retried through another deletion mechanism. Instead, NTFS
compression preserved all cache files and reduced allocated storage by about
395 MB. Four closed worker journals were also compressed. Compression of
API dependency files was stopped to reduce competing disk load; installed
dependencies remain intact. No database or trading record was modified.

## Automatic prevention and deployment

Task **TradeTrack MT5 Archive Maintenance** runs every 15 minutes under
TradeTrackWorker at Limited privilege. It uses a below-normal-priority Python
process, a shared process mutex, a four-minute work budget and an eight-minute
task limit. Overlapping scheduled runs are ignored. It resumes through the
existing archive index without repeatedly hashing already linked paths.
The task uses the same logged-in worker session as production; disconnecting
RDP is supported, logging out is not a substitute for a service deployment.

The installer script records this configuration for reconstruction. Unique
new versions still consume storage; no lossless scheme can retain unlimited
unique content in a fixed disk. The status file records free bytes and flags
capacity below 5 GiB. Include this task's last result/status freshness and disk
space in the existing weekly maintenance review; do not describe registration
alone as a successful scheduled run.

Worker release **5560b07** also prevents diagnostic log/stdout ENOSPC from
aborting worker dispatch, watchdog recovery or the supervisor's monitoring
loop. This does not make MT5 or its data writes safe on a full disk; keeping
free space remains necessary. 286 tests passed, including native Windows
sharing, post-replacement content preservation, busy files, corrupt objects,
idempotence, recovery despite failed logging, and the worker stdout failure.

The first rollout canary (demo-04) encountered an existing TERMINAL_UI_BUSY;
rollout stopped and the previous worker resumed. A subsequent drained canary
on demo-01 read all 459 server names successfully. Both standard-user
supervisors started 5560b07 at approximately 10:53:44–47 UTC. No terminal build,
restart budget, UAC setting, account credential or access guard was changed.

Reference: [Microsoft hard-link semantics](https://learn.microsoft.com/en-us/windows/win32/fileio/hard-links-and-junctions).

## Runtime verification

At 11:06 UTC all 1,061 paths in the initial inventory still existed with their
original byte lengths (zero missing or size mismatches). Replacement content
is checked with SHA-256 by the housekeeping process before releasing the
duplicate allocation. At 11:09 UTC free space exceeded 12 GiB; the initial
bounded bulk pass was still running and had not yet processed the whole
backlog. Do not describe its progress counters as the final total.

Every one of the ten slots had successful collections after the 10:54 rollout.
The API check at 11:06:44 UTC reported collection/import OK, zero blocked
imports and zero late collections/imports. Intermittent UI_DIALOG_UNAVAILABLE
still occurred between successful collections; this storage fix is not proof
that those UI errors or every cause of disconnection have been eliminated.
Both new supervisor stderr files remained empty. No trading records, account
databases or terminal builds were changed during this recovery.

Initial pass completed at 11:13:46 UTC, exit 0: 456 examined, 366 duplicate
paths linked, 90 unique objects retained, 15,788,941,839 duplicate logical
bytes released, zero busy skips. Actual free space was 14,960,791,552 bytes.

The actual Windows task was manually started at 11:14:08 UTC and completed
at 11:18:29 UTC. Scheduler final result **0**, state Ready. It resumed past
456 existing linked paths, linked another 23 duplicates, retained four new
unique objects, and skipped zero busy files. Free space reached
15,771,418,624 bytes (14.69 GiB). No pending-original files remained. The
11:15 trigger correctly did not create an overlapping process; its transient
already-running result was replaced by final success. Next trigger 11:30:33
UTC. The older backlog is not fully processed; bounded periodic runs continue
it automatically without removing unique content.

Health remained variable during observation: the API briefly reported two
blocked imports and later one late collection/import, while worker logs
contained UI_DIALOG_UNAVAILABLE and INVESTOR_UNVERIFIED. Those observations
must not be described as fixed by storage recovery or bypassed by disabling
access checks. The disk exhaustion and duplicate accumulation fixes are
verified separately from complete MT5 service health.
