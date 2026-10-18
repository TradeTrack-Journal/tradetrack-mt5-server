# Bounded recovery of MT5 transport interruptions

The October 17–18 investigation found terminal reconnects under one second, but
the collector immediately failed a disconnected identity check. The API then
applied the same exponential delay (up to fifteen minutes) to transport flaps and
positive broker service-unavailable responses. This evidence does not establish
whether the underlying disconnect originated at the broker or the VPS network.

Changes:

- After successful credentialed login, allow up to five seconds of local reconnect
  waiting and at most three complete snapshot attempts. Never repeat login in
  this loop. Revalidate process, account, server, paths, build, investor access and
  disabled Python trading. Discard partial history/positions and recount/reread.
  The existing isolated-child deadline and lease fencing remain authoritative.
- Classify exact fresh journal `Service is not available` as
  `BROKER_SERVICE_UNAVAILABLE`; explicit access rejection takes precedence.
  API retries transport after 5–10 seconds, increasing to 55–60 seconds; broker
  service unavailability after 15–25 seconds, increasing to 120–130 seconds.
  Success resets persisted failure count. Unknown/auth/identity policies remain.
- API checks per-account collection and import timestamps every minute. Beyond
  90 seconds, expose delayed freshness. Beyond three minutes and two consecutive
  bad samples, send an incident through the existing deduplicated Sentry/Telegram
  route. Initial collection has ten minutes' grace; valid empty imports count as
  progress. Intentional inactivity and access-paused jobs remain excluded.

Deploy API schema/policy first, frontend error recognition next, then immutable
worker release via graceful drain and both scheduled supervisors. Do not restart
MT5 terminals, change supported builds or touch credentials for this deployment.
Rollback to the prior worker release remains compatible with the expanded API.

Validation: 266 offline Python tests passed, including transient disconnect,
partial-read discard, identity/access change rejection, persistent disconnect,
flapping attempt limit, exact fresh journal classification and child deadline.
API validation uses lint/build and compiled behavior checks, without adding an API
test suite. Production verification must confirm current heartbeat, quarantine,
successful collection and import timestamps after deployment.

References:
- https://www.mql5.com/en/docs/python_metatrader5/mt5terminalinfo_py
- https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_mitigate_interaction_failure_limit_retries.html

These changes bound our recovery delay; they cannot make an unavailable broker
respond or guarantee minute-level freshness throughout an external outage.
