import unittest
from app.collector.windows_inventory import read_only_caption_matches


class ReadOnlyCaptionTests(unittest.TestCase):
    def test_observed_headway_6204_caption(self):
        title = '16933402 - Headway-Real: Read Only - Hedge - Jarocel (Pty) Ltd'
        self.assertTrue(read_only_caption_matches(title, '16933402', 'Headway-Real', 6204))
        self.assertFalse(read_only_caption_matches(title.replace('Read Only - ', ''), '16933402', 'Headway-Real', 6204))

    def test_observed_6230_caption_and_rejected_lookalikes(self):
        title = '252747356 - Exness-MT5Real33: Read Only - Hedge - Exness Technologies Ltd'
        self.assertTrue(read_only_caption_matches(title, '252747356', 'Exness-MT5Real33', 6230))
        for invalid in (title.replace('Read Only - ', ''), title.replace('252747356', '252747357'),
                        title.replace('Exness-MT5Real33', 'Exness-MT5Real21'),
                        title.replace('Hedge', 'Netting')):
            self.assertFalse(read_only_caption_matches(invalid, '252747356', 'Exness-MT5Real33', 6230))
        self.assertFalse(read_only_caption_matches(title, '252747356', 'Exness-MT5Real33', 9999))

    def test_broker_and_demo_formats(self):
        for build in (6182, 6190, 6193):
            for demo in ('', 'Demo Account - '):
                title = f'12345 - BrightFunded-Server: {demo}Read Only - Hedge - BrightFunded Ltd.'
                self.assertTrue(read_only_caption_matches(title, '12345', 'BrightFunded-Server', build))

    def test_read_only_cannot_be_a_company_name_or_another_account(self):
        for title in (
            '12345 - BrightFunded-Server: Hedge - Read Only Ltd.',
            '12345 - BrightFunded-Server: Demo Account - Hedge - Read Only Ltd.',
            '54321 - BrightFunded-Server: Read Only - Hedge - BrightFunded Ltd.',
            '12345 - Other-Server: Read Only - Hedge - BrightFunded Ltd.',
        ):
            self.assertFalse(read_only_caption_matches(title, '12345', 'BrightFunded-Server', 6190))
