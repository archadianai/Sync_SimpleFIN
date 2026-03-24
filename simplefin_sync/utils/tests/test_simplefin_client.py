# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Unit tests for the SimpleFIN HTTP client.

All external HTTP calls are mocked — these tests never hit the real API.
"""

import base64
import json
import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from simplefin_sync.utils.simplefin_client import (
	SimpleFINAuthError,
	SimpleFINClient,
	SimpleFINError,
	SimpleFINHTTPError,
	SimpleFINNetworkError,
	SimpleFINPaymentRequired,
)

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

DEMO_ACCESS_URL = "https://demouser:demopass@beta-bridge.simplefin.org/simplefin"

DEMO_CLAIM_URL = "https://beta-bridge.simplefin.org/simplefin/claim/DEMOTOKEN"
DEMO_SETUP_TOKEN = base64.b64encode(DEMO_CLAIM_URL.encode()).decode()

DEMO_ACCOUNTS_RESPONSE = {
	"errors": [],
	"accounts": [
		{
			"org": {
				"domain": "mybank.com",
				"name": "My Bank",
				"sfin-url": "https://sfin.mybank.com",
				"url": "https://mybank.com",
			},
			"id": "ACT-001",
			"name": "Business Checking",
			"currency": "USD",
			"balance": "12345.67",
			"available-balance": "12000.00",
			"balance-date": 1700000000,
			"transactions": [
				{
					"id": "TXN-001",
					"posted": 1699900000,
					"amount": "-150.00",
					"description": "ACH Payment - Vendor",
					"transacted_at": 1699890000,
					"pending": False,
					"extra": {},
				}
			],
		}
	],
}


def _mock_response(status_code: int, text: str = "", json_data=None):
	"""Create a mock requests.Response."""
	resp = MagicMock()
	resp.status_code = status_code
	resp.text = text
	if json_data is not None:
		resp.json.return_value = json_data
		resp.text = json.dumps(json_data)
	else:
		resp.json.side_effect = ValueError("No JSON")
	return resp


# ---------------------------------------------------------------------------
# Token exchange tests
# ---------------------------------------------------------------------------

class TestClaimAccessURL(FrappeTestCase):
	"""Tests for SimpleFINClient.claim_access_url()."""

	@patch("simplefin_sync.utils.simplefin_client.requests.post")
	def test_successful_exchange(self, mock_post):
		"""200 response with a valid access URL."""
		mock_post.return_value = _mock_response(200, text=DEMO_ACCESS_URL)

		result = SimpleFINClient.claim_access_url(DEMO_SETUP_TOKEN)

		self.assertEqual(result, DEMO_ACCESS_URL)
		mock_post.assert_called_once()
		call_args = mock_post.call_args
		self.assertEqual(call_args[0][0], DEMO_CLAIM_URL)
		self.assertTrue(call_args[1]["verify"])

	@patch("simplefin_sync.utils.simplefin_client.requests.post")
	def test_403_already_claimed(self, mock_post):
		"""403 means token was already used or is compromised."""
		mock_post.return_value = _mock_response(403, text="Forbidden")

		with self.assertRaises(SimpleFINAuthError):
			SimpleFINClient.claim_access_url(DEMO_SETUP_TOKEN)

	@patch("simplefin_sync.utils.simplefin_client.requests.post")
	def test_500_server_error(self, mock_post):
		"""Unexpected HTTP status raises SimpleFINHTTPError."""
		mock_post.return_value = _mock_response(500, text="Internal Server Error")

		with self.assertRaises(SimpleFINHTTPError) as ctx:
			SimpleFINClient.claim_access_url(DEMO_SETUP_TOKEN)
		self.assertEqual(ctx.exception.status_code, 500)

	@patch("simplefin_sync.utils.simplefin_client.requests.post")
	def test_network_timeout(self, mock_post):
		"""Timeout raises SimpleFINNetworkError."""
		import requests as _req

		mock_post.side_effect = _req.exceptions.Timeout("timed out")

		with self.assertRaises(SimpleFINNetworkError):
			SimpleFINClient.claim_access_url(DEMO_SETUP_TOKEN)

	@patch("simplefin_sync.utils.simplefin_client.requests.post")
	def test_connection_error(self, mock_post):
		"""Connection failure raises SimpleFINNetworkError."""
		import requests as _req

		mock_post.side_effect = _req.exceptions.ConnectionError("refused")

		with self.assertRaises(SimpleFINNetworkError):
			SimpleFINClient.claim_access_url(DEMO_SETUP_TOKEN)

	def test_non_https_claim_url_rejected(self):
		"""HTTP claim URL is rejected immediately."""
		http_claim = "http://bridge.simplefin.org/simplefin/claim/TOKEN"
		token = base64.b64encode(http_claim.encode()).decode()

		with self.assertRaises(SimpleFINError):
			SimpleFINClient.claim_access_url(token)

	def test_invalid_base64_token(self):
		"""Garbage token raises SimpleFINError."""
		with self.assertRaises(SimpleFINError):
			SimpleFINClient.claim_access_url("not-valid-base64!!!")

	@patch("simplefin_sync.utils.simplefin_client.requests.post")
	def test_empty_response_body(self, mock_post):
		"""200 but empty body raises SimpleFINError."""
		mock_post.return_value = _mock_response(200, text="")

		with self.assertRaises(SimpleFINError):
			SimpleFINClient.claim_access_url(DEMO_SETUP_TOKEN)

	@patch("simplefin_sync.utils.simplefin_client.requests.post")
	def test_non_https_access_url_rejected(self, mock_post):
		"""200 but returned access URL is HTTP — rejected."""
		mock_post.return_value = _mock_response(
			200, text="http://user:pass@bridge.simplefin.org/simplefin"
		)

		with self.assertRaises(SimpleFINError):
			SimpleFINClient.claim_access_url(DEMO_SETUP_TOKEN)


# ---------------------------------------------------------------------------
# Client constructor tests
# ---------------------------------------------------------------------------

class TestClientInit(FrappeTestCase):
	"""Tests for SimpleFINClient.__init__()."""

	def test_valid_access_url(self):
		"""Parses credentials and base URL correctly."""
		client = SimpleFINClient(DEMO_ACCESS_URL)
		self.assertEqual(client._auth, ("demouser", "demopass"))
		self.assertEqual(
			client._base_url,
			"https://beta-bridge.simplefin.org/simplefin",
		)

	def test_non_https_rejected(self):
		"""HTTP access URL is rejected."""
		with self.assertRaises(SimpleFINError):
			SimpleFINClient("http://user:pass@host/path")

	def test_missing_credentials_rejected(self):
		"""URL without user:pass is rejected."""
		with self.assertRaises(SimpleFINError):
			SimpleFINClient("https://host/path")

	def test_url_with_port(self):
		"""Port is preserved in base URL."""
		client = SimpleFINClient("https://user:pass@host:8443/api")
		self.assertEqual(client._base_url, "https://host:8443/api")


# ---------------------------------------------------------------------------
# get_accounts tests
# ---------------------------------------------------------------------------

class TestGetAccounts(FrappeTestCase):
	"""Tests for SimpleFINClient.get_accounts()."""

	def setUp(self):
		super().setUp()
		self.client = SimpleFINClient(DEMO_ACCESS_URL)

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_successful_fetch(self, mock_get):
		"""200 with valid JSON returns parsed data."""
		mock_get.return_value = _mock_response(200, json_data=DEMO_ACCOUNTS_RESPONSE)

		data = self.client.get_accounts(start_date=1699800000, end_date=1700000000)

		self.assertEqual(len(data["accounts"]), 1)
		self.assertEqual(data["accounts"][0]["id"], "ACT-001")
		self.assertEqual(len(data["accounts"][0]["transactions"]), 1)

		# Verify request was made with correct params
		call_kwargs = mock_get.call_args[1]
		self.assertEqual(call_kwargs["auth"], ("demouser", "demopass"))
		self.assertTrue(call_kwargs["verify"])

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_balances_only(self, mock_get):
		"""balances-only param is sent correctly."""
		mock_get.return_value = _mock_response(200, json_data={"errors": [], "accounts": []})

		self.client.get_accounts(balances_only=True)

		call_args = mock_get.call_args
		params = dict(call_args[1]["params"])
		self.assertEqual(params["balances-only"], "1")

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_account_ids_filter(self, mock_get):
		"""account IDs are sent as repeated query params."""
		mock_get.return_value = _mock_response(200, json_data={"errors": [], "accounts": []})

		self.client.get_accounts(account_ids=["ACT-001", "ACT-002"])

		call_args = mock_get.call_args
		params = call_args[1]["params"]
		# params is a list of tuples for repeated keys
		account_params = [v for k, v in params if k == "account"]
		self.assertEqual(account_params, ["ACT-001", "ACT-002"])

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_403_revoked(self, mock_get):
		"""403 raises SimpleFINAuthError."""
		mock_get.return_value = _mock_response(403)

		with self.assertRaises(SimpleFINAuthError):
			self.client.get_accounts()

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_402_payment_required(self, mock_get):
		"""402 raises SimpleFINPaymentRequired."""
		mock_get.return_value = _mock_response(402)

		with self.assertRaises(SimpleFINPaymentRequired):
			self.client.get_accounts()

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_network_timeout(self, mock_get):
		"""Timeout raises SimpleFINNetworkError."""
		import requests as _req

		mock_get.side_effect = _req.exceptions.Timeout("timed out")

		with self.assertRaises(SimpleFINNetworkError):
			self.client.get_accounts()

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_connection_error(self, mock_get):
		"""Connection failure raises SimpleFINNetworkError."""
		import requests as _req

		mock_get.side_effect = _req.exceptions.ConnectionError("refused")

		with self.assertRaises(SimpleFINNetworkError):
			self.client.get_accounts()

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_invalid_json_response(self, mock_get):
		"""200 but non-JSON body raises SimpleFINError."""
		mock_get.return_value = _mock_response(200, text="not json")

		with self.assertRaises(SimpleFINError):
			self.client.get_accounts()

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_500_server_error(self, mock_get):
		"""500 raises SimpleFINHTTPError."""
		mock_get.return_value = _mock_response(500, text="Internal Server Error")

		with self.assertRaises(SimpleFINHTTPError) as ctx:
			self.client.get_accounts()
		self.assertEqual(ctx.exception.status_code, 500)

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_pending_param(self, mock_get):
		"""pending=1 is sent when include_pending is True."""
		mock_get.return_value = _mock_response(200, json_data={"errors": [], "accounts": []})

		self.client.get_accounts(include_pending=True)

		call_args = mock_get.call_args
		params = dict(call_args[1]["params"])
		self.assertEqual(params["pending"], "1")


# ---------------------------------------------------------------------------
# test_connection tests
# ---------------------------------------------------------------------------

class TestTestConnection(FrappeTestCase):
	"""Tests for SimpleFINClient.test_connection()."""

	@patch("simplefin_sync.utils.simplefin_client.requests.get")
	def test_delegates_to_get_accounts(self, mock_get):
		"""test_connection calls get_accounts with balances_only=True."""
		mock_get.return_value = _mock_response(200, json_data={"errors": [], "accounts": []})

		client = SimpleFINClient(DEMO_ACCESS_URL)
		result = client.test_connection()

		self.assertIn("accounts", result)
		call_args = mock_get.call_args
		params = dict(call_args[1]["params"])
		self.assertEqual(params["balances-only"], "1")
