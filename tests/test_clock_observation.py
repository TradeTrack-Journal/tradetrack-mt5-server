import unittest
from app.collector.clock_observation import compare


class ClockObservationTests(unittest.TestCase):
    def samples(self, offset=180):
        utc = 1789000000000
        first = {'utcMs': utc, 'monotonicMs': 1000, 'ticks': {s: utc + offset * 60000 - 100 for s in ['EURUSD', 'USDJPY']}}
        second = {'utcMs': utc + 1000, 'monotonicMs': 2000, 'ticks': {s: utc + offset * 60000 + 900 for s in ['EURUSD', 'USDJPY']}}
        return first, second

    def test_offsets(self):
        for offset in [-720, 0, 180, 330, 345, 840]:
            with self.subTest(offset=offset):
                result = compare(*self.samples(offset))
                self.assertEqual(result['status'], 'OBSERVED')
                self.assertEqual(result['offsetMinutes'], offset)

    def test_weekend_ticks(self):
        a, b = self.samples()
        b['ticks'] = a['ticks'].copy()
        self.assertEqual(compare(a, b)['status'], 'FRESH_QUOTES_REQUIRED')

    def test_clock_jump(self):
        a, b = self.samples()
        b['utcMs'] += 3600000
        self.assertEqual(compare(a, b)['status'], 'CLOCK_UNSTABLE')

    def test_disagreement(self):
        a, b = self.samples()
        for item in [a, b]:
            item['ticks']['USDJPY'] += 3600000
        self.assertEqual(compare(a, b)['status'], 'CLOCK_DISAGREEMENT')

    def test_one_quote_is_insufficient(self):
        a, b = self.samples()
        del b['ticks']['USDJPY']
        self.assertEqual(compare(a, b)['status'], 'FRESH_QUOTES_REQUIRED')

    def test_delayed_quotes(self):
        a, b = self.samples()
        for item in [a, b]:
            for symbol in item['ticks']:
                item['ticks'][symbol] -= 10000
        self.assertEqual(compare(a, b)['status'], 'FRESH_QUOTES_REQUIRED')
