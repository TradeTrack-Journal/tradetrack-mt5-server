"""Offline capacity estimate from sanitized worker durations; never contacts MT5/API.

This is a queueing model, NOT a load test of real broker accounts. Inventory,
broker mix, CPU scaling and first-import durations must be validated separately.
"""
import argparse
import heapq
import json
import random
import statistics
from pathlib import Path


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[int((len(ordered) - 1) * fraction)], 3) if ordered else None


def simulate(durations, accounts, slots, cadence, cooldown, initial, seed, minutes):
    rng = random.Random(seed)
    claimed, read_started = {}, {}
    # Last service time first: equivalent for this one-account-per-owner scenario.
    due = [(0.0, i) for i in range(accounts)]
    heapq.heapify(due)
    last = [None] * accounts
    events = [(0.0, i, 'claim', None) for i in range(slots)]
    heapq.heapify(events)
    active_initial = 0
    remaining_initial = initial
    waits, gaps, visibility_bounds = [], [], []
    finish = minutes * 60
    warmup = 120
    completed = 0
    while events:
        now, slot, kind, account = heapq.heappop(events)
        if now > finish:
            break
        if kind == 'initial_done':
            active_initial -= 1
            heapq.heappush(events, (now + cooldown, slot, 'claim', None))
        elif kind == 'complete':
            previous, started = last[account], claimed[account]
            if previous is not None and now >= warmup:
                gaps.append(now - previous)
                # Worst phase: a deal arrives just after the preceding read begins.
                # Collection's actual read point is unknown, so this is conservative.
                visibility_bounds.append(now - read_started[account])
            last[account] = now
            read_started[account] = started
            completed += 1
            heapq.heappush(due, (now + cadence, account))
            heapq.heappush(events, (now + cooldown, slot, 'claim', None))
        else:
            if remaining_initial and active_initial < 2:
                # Hypothetical thirty-second first import, not a measured promise.
                remaining_initial -= 1
                active_initial += 1
                heapq.heappush(events, (now + 30, slot, 'initial_done', None))
            elif due and due[0][0] <= now:
                when, account = heapq.heappop(due)
                if now >= warmup:
                    waits.append(now - when)
                claimed[account] = now
                duration = rng.choice(durations)
                heapq.heappush(events, (now + duration, slot, 'complete', account))
            else:
                # Existing ten-second empty-queue polling, not a busy loop.
                heapq.heappush(events, (now + 10, slot, 'claim', None))
    return {
        'slots': slots, 'cadenceSeconds': cadence, 'successCooldownSeconds': cooldown,
        'syntheticInitialImports': initial, 'seed': seed, 'completed': completed,
        'queueWaitP95Seconds': percentile(waits, .95),
        'completionGapP95Seconds': percentile(gaps, .95),
        'completionGapP99Seconds': percentile(gaps, .99),
        'completionGapMaxSeconds': round(max(gaps), 3),
        'conservativeVisibilityP95Seconds': percentile(visibility_bounds, .95),
        'conservativeVisibilityMaxSeconds': round(max(visibility_bounds), 3),
        'completionGapsOver60Percent': round(100 * sum(g > 60 for g in gaps) / len(gaps), 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', required=True)
    parser.add_argument('--after', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--accounts', type=int, default=50)
    parser.add_argument('--minutes', type=int, default=60)
    args = parser.parse_args()
    durations = []
    for line in Path(args.log).read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        if row.get('at', '') >= args.after and row.get('state') == 'collected':
            durations.append(row['durationMs'] / 1000)
    if len(durations) < 20:
        raise SystemExit('At least twenty successful duration samples are required')
    result = {
        'kind': 'offline_queue_model_not_real_account_load_test',
        'accounts': args.accounts, 'simulationMinutes': args.minutes,
        'sourceAfter': args.after, 'samples': len(durations),
        'meanCollectionSeconds': round(statistics.mean(durations), 3),
        'p95CollectionSeconds': percentile(durations, .95),
        'assumptions': [
            'One account per owner; all brokers present in every slot.',
            'Independent sampled durations; no claim of real CPU scaling.',
            'No additional failures or maintenance beyond delay embedded in samples.',
            'Initial imports consume up to two slots for an assumed 30 seconds each.',
            'Initial accounts are not added to the fifty recurring accounts.',
            'Warmup of 120 seconds excluded from latency statistics.',
            'No browser refresh latency included.',
        ],
        'scenarios': [],
    }
    for slots, cadence, cooldown in [(5, 60, 10), (5, 60, 0), (5, 30, 0), (8, 30, 0), (10, 30, 0)]:
        for initial in [0, 50]:
            for seed in [1, 2, 3]:
                result['scenarios'].append(simulate(durations, args.accounts, slots, cadence, cooldown, initial, seed, args.minutes))
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
