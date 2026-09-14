import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from app.collector.broker_directory import MAX_RESPONSE_BYTES, DirectoryError, parse_directory, search_brokers


def company(broker="Example Ltd", server="Example-Live", addresses=None):
    return {"companyName": broker, "results": [{"name": server, "access": addresses or ["example.org:443"]}]}


class DirectoryTests(unittest.TestCase):
    def test_response_preserves_endpoints_but_does_not_claim_verified_login(self):
        row = parse_directory([company()])[0]
        self.assertEqual(row.server, "Example-Live")
        self.assertEqual(row.access_points, ("example.org:443",))
        self.assertEqual(row.verification, "directory_only")

    def test_same_named_servers_at_different_companies_are_not_merged(self):
        rows = parse_directory([company("A"), company("B")])
        self.assertEqual(len(rows), 2)

    def test_duplicate_result_merges_only_matching_broker_and_server(self):
        rows = parse_directory([company(), company(addresses=["another.example.org:443"])])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0].access_points), 2)

    def test_error_or_partial_response_is_not_successful_empty_search(self):
        for payload in ({"error": "provider error"}, None, [company(), {}], [{"companyName": "A", "results": None}]):
            with self.subTest(payload=payload), self.assertRaises(DirectoryError):
                parse_directory(payload)
        self.assertEqual(parse_directory([]), ())

    def test_invalid_query_never_contacts_provider(self):
        for query in ("", "a", "x" * 81, "Broker\nInjected", None):
            with self.subTest(query=query), patch("app.collector.broker_directory.urlopen") as open_url:
                with self.assertRaises(DirectoryError):
                    search_brokers(query)
                open_url.assert_not_called()

    def test_query_is_urlencoded_and_no_authentication_is_sent(self):
        response = MagicMock(status=200)
        response.read.return_value = json.dumps([company()]).encode()
        response.__enter__.return_value = response
        with patch("app.collector.broker_directory.urlopen", return_value=response) as open_url:
            rows = search_brokers("A & B")
        self.assertEqual(len(rows), 1)
        self.assertEqual(open_url.call_args.args, ("https://mt5.mtapi.io/Search?company=A+%26+B",))
        self.assertEqual(open_url.call_args.kwargs, {"timeout": 10})
        response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)

    def test_rate_limit_is_sanitized_and_not_retried(self):
        error = HTTPError("https://example.org", 429, "private provider body", {}, None)
        with patch("app.collector.broker_directory.urlopen", side_effect=error) as open_url:
            with self.assertRaises(DirectoryError) as caught:
                search_brokers("Example")
        self.assertEqual(str(caught.exception), "PROVIDER_RATE_LIMITED")
        open_url.assert_called_once()

    def test_oversized_body_is_rejected_before_json_parsing(self):
        response = MagicMock(status=200)
        response.read.return_value = b" " * (MAX_RESPONSE_BYTES + 1)
        response.__enter__.return_value = response
        with patch("app.collector.broker_directory.urlopen", return_value=response):
            with self.assertRaises(DirectoryError) as caught:
                search_brokers("Example")
        self.assertEqual(str(caught.exception), "PROVIDER_RESPONSE_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
