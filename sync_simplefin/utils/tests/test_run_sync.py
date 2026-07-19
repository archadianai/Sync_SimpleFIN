# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Integration tests for run_sync — the sync entry point.

These exercise the state machine and failure handling that had no coverage,
using a mocked SimpleFINClient. run_sync commits, so each test cleans up the
connection, its sync logs, and any Bank Transactions it created.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sync_simplefin.utils.simplefin_client import (
	SimpleFINAuthError,
	SimpleFINNetworkError,
)
from sync_simplefin.utils.sync import run_sync


def _ensure_bank_account() -> str:
	"""Return a Bank Account name usable for test transactions.

	Reuses an existing Bank Account when the site has one (dev sites);
	otherwise provisions a minimal Company/Bank/Bank Account fixture (fresh
	CI sites have none). Fixtures are left in place — CI sites are ephemeral.
	"""
	existing = frappe.get_all("Bank Account", pluck="name", limit=1)
	if existing:
		return existing[0]

	company = frappe.get_all("Company", pluck="name", limit=1)
	if company:
		company = company[0]
	else:
		company = frappe.get_doc({
			"doctype": "Company",
			"company_name": "SFIN Test Company",
			"abbr": "STC",
			"country": "United States",
			"default_currency": "USD",
			"create_chart_of_accounts_based_on": "Standard Template",
			"chart_of_accounts": "Standard",
		}).insert(ignore_permissions=True).name

	if not frappe.db.exists("Bank", "SFIN Test Bank"):
		frappe.get_doc({"doctype": "Bank", "bank_name": "SFIN Test Bank"}).insert(
			ignore_permissions=True
		)

	ba = frappe.get_doc({
		"doctype": "Bank Account",
		"account_name": "SFIN Test Checking",
		"bank": "SFIN Test Bank",
		"company": company,
		"is_company_account": 1,
	}).insert(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- fixture must survive run_sync's commits/rollbacks
	return ba.name


def _accounts_response(account_id, transactions=None, errors=None, currency="USD"):
	"""Build a SimpleFIN /accounts response with one account."""
	return {
		"errors": errors or [],
		"accounts": [
			{
				"org": {"domain": "test.example", "name": "Test Bank"},
				"id": account_id,
				"name": "Test Checking",
				"currency": currency,
				"balance": "1000.00",
				"available-balance": "1000.00",
				"balance-date": 1700000000,
				"transactions": transactions or [],
			}
		],
	}


class _RunSyncTestBase(FrappeTestCase):
	"""Shared connection factory and cleanup for run_sync tests."""

	def setUp(self):
		self._connections = []
		self._account_ids = []
		self.bank_account = _ensure_bank_account()

	def tearDown(self):
		# Cancel + delete any Bank Transactions created under the test accounts.
		for acct_id in self._account_ids:
			for bt in frappe.get_all(
				"Bank Transaction", filters={"simplefin_account_id": acct_id}, pluck="name"
			):
				doc = frappe.get_doc("Bank Transaction", bt)
				if doc.docstatus == 1:
					doc.cancel()
				doc.delete()
		for name in self._connections:
			for log in frappe.get_all(
				"SimpleFIN Sync Log", filters={"connection": name}, pluck="name"
			):
				frappe.delete_doc("SimpleFIN Sync Log", log, force=True)
			if frappe.db.exists("SimpleFIN Connection", name):
				frappe.delete_doc("SimpleFIN Connection", name, force=True)
		frappe.db.commit()  # nosemgrep: frappe-manual-commit -- run_sync commits; persist cleanup so the dev DB is not polluted

	def _make_connection(self, mappings=None, **overrides):
		hash6 = frappe.generate_hash(length=6)
		conn = frappe.get_doc({
			"doctype": "SimpleFIN Connection",
			"connection_name": f"RunSyncTest_{hash6}",
			"is_registered": 1,
			"enabled": 1,
			"sync_frequency": "Daily",
			"on_sync_failure": "Log Only",
			"on_empty_account": "Log Only",
			"on_record_mismatch": "Log Only",
			"retry_count": 0,
			"initial_history_days": 90,
			"rolling_window_days": 14,
			**overrides,
		})
		for m in (mappings or []):
			conn.append("account_mappings", m)
		conn.insert(ignore_permissions=True)
		conn.access_url = "https://user:pass@bridge.example/simplefin"
		conn.save(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep: frappe-manual-commit -- persist so run_sync's own commits build on a committed base
		self._connections.append(conn.name)
		return conn


class TestRateLimitPausePersists(_RunSyncTestBase):
	"""Finding 1: a rate-limit pause must survive to the end of run_sync."""

	@patch("sync_simplefin.utils.sync.SimpleFINClient")
	def test_pause_survives_successful_run(self, mock_cls):
		conn = self._make_connection()
		mock_cls.return_value.get_accounts.return_value = _accounts_response(
			"unmapped-acct", errors=["Rate limit exceeded — slow down"]
		)

		run_sync(conn.name)

		conn.reload()
		self.assertIsNotNone(
			conn.rate_limit_paused_until,
			"rate-limit pause must not be cleared by the success path",
		)
		self.assertEqual(conn.connection_status, "Rate Limited")


class TestFirstChunkFailure(_RunSyncTestBase):
	"""Finding 2: a first-chunk failure must record a Failed log, not crash."""

	@patch("sync_simplefin.utils.sync.SimpleFINClient")
	def test_network_error_records_failed_log(self, mock_cls):
		conn = self._make_connection()
		mock_cls.return_value.get_accounts.side_effect = SimpleFINNetworkError("boom")

		run_sync(conn.name)

		conn.reload()
		self.assertEqual(conn.last_sync_status, "Failed")
		self.assertEqual(conn.sync_state, "Failed")  # retry_count=0 → straight to Failed
		logs = frappe.get_all(
			"SimpleFIN Sync Log",
			filters={"connection": conn.name, "status": "Failed"},
			pluck="name",
		)
		self.assertTrue(logs, "a Failed sync log must exist (not be rolled away)")

	@patch("sync_simplefin.utils.sync.SimpleFINClient")
	def test_auth_error_marks_revoked_and_disabled(self, mock_cls):
		conn = self._make_connection()
		mock_cls.return_value.get_accounts.side_effect = SimpleFINAuthError("revoked")

		run_sync(conn.name)

		conn.reload()
		self.assertEqual(conn.connection_status, "Revoked")
		self.assertEqual(conn.enabled, 0)
		self.assertTrue(
			frappe.get_all(
				"SimpleFIN Sync Log",
				filters={"connection": conn.name, "status": "Failed"},
				pluck="name",
			)
		)


class TestSyncFullBackfill(_RunSyncTestBase):
	"""Finding 9 (+6): Sync Full walks past an all-duplicate newest chunk, and
	created transactions use their own account's currency."""

	@patch("sync_simplefin.utils.sync.SimpleFINClient")
	def test_full_sync_reaches_older_gap(self, mock_cls):
		acct_id = f"TEST-ACCT-{frappe.generate_hash(length=6)}"
		self._account_ids.append(acct_id)

		# account_mappings[0] is an inactive EUR row; the active USD row is [1].
		# The old code stamped every txn with mappings[0].currency (EUR).
		conn = self._make_connection(
			last_sync_end_date=0,  # Sync Full
			mappings=[
				{
					"simplefin_account_id": f"OTHER-{frappe.generate_hash(length=4)}",
					"simplefin_account_name": "Euro Savings",
					"simplefin_currency": "EUR",
					"is_active": 0,
				},
				{
					"simplefin_account_id": acct_id,
					"simplefin_account_name": "Test Checking",
					"simplefin_currency": "USD",
					"erpnext_bank_account": self.bank_account,
					"is_active": 1,
				},
			],
		)

		# Pre-insert the newest-chunk transaction as an existing duplicate.
		dup = frappe.get_doc({
			"doctype": "Bank Transaction",
			"date": "2026-06-15",
			"bank_account": self.bank_account,
			"withdrawal": 150.0,
			"deposit": 0,
			"currency": "USD",
			"status": "Unreconciled",
			"unallocated_amount": 150.0,
			"allocated_amount": 0,
			"simplefin_account_id": acct_id,
			"simplefin_transaction_id": "DUP-1",
		})
		dup.insert(ignore_permissions=True)
		dup.submit()
		frappe.db.commit()  # nosemgrep: frappe-manual-commit -- persist the pre-existing duplicate before run_sync

		# Chunk 0 (newest) returns the duplicate; chunk 1 (older) returns a new txn.
		mock_cls.return_value.get_accounts.side_effect = [
			_accounts_response(acct_id, transactions=[{
				"id": "DUP-1", "posted": 1718452800, "amount": "-150.00",
				"description": "Existing payment", "pending": False,
			}]),
			_accounts_response(acct_id, transactions=[{
				"id": "NEW-1", "posted": 1710000000, "amount": "-42.00",
				"description": "Older gap payment", "pending": False,
			}]),
		]

		run_sync(conn.name, sync_type="Manual", full=True)

		# Both chunks were requested — the all-duplicate stop did not fire.
		self.assertEqual(mock_cls.return_value.get_accounts.call_count, 2)

		created = frappe.get_all(
			"Bank Transaction",
			filters={"simplefin_account_id": acct_id, "simplefin_transaction_id": "NEW-1"},
			fields=["name", "currency"],
		)
		self.assertEqual(len(created), 1, "the older-gap transaction must be imported")
		# Finding 6: currency is the active account's (USD), not mappings[0] (EUR).
		self.assertEqual(created[0].currency, "USD")


class TestRateLimitedFullSyncPreservesRange(_RunSyncTestBase):
	"""Review fix: a rate-limit abort with chunks remaining must NOT advance
	last_sync_end_date — advancing would orphan the unfetched older range."""

	@patch("sync_simplefin.utils.sync.SimpleFINClient")
	def test_interrupted_full_sync_keeps_zero_end_date(self, mock_cls):
		conn = self._make_connection(
			last_sync_end_date=0,
			initial_history_days=90,  # 2 chunks
		)
		mock_cls.return_value.get_accounts.return_value = _accounts_response(
			"unmapped-acct", errors=["Rate limit exceeded — slow down"]
		)

		run_sync(conn.name, sync_type="Manual", full=True)

		conn.reload()
		# Only chunk 0 was fetched; the range must remain un-advanced so the
		# next run re-covers the whole window.
		self.assertEqual(mock_cls.return_value.get_accounts.call_count, 1)
		self.assertFalse(conn.last_sync_end_date)


class TestPendingWithoutPosted(_RunSyncTestBase):
	"""Review fix: with include_pending on, a pending transaction that has no
	posted date yet is counted as pending — not as an error that forces
	Partial Success forever."""

	@patch("sync_simplefin.utils.sync.SimpleFINClient")
	def test_pending_no_posted_is_not_an_error(self, mock_cls):
		acct_id = f"TEST-ACCT-{frappe.generate_hash(length=6)}"
		self._account_ids.append(acct_id)
		conn = self._make_connection(
			include_pending=1,
			initial_history_days=30,  # single chunk — keep counts unambiguous
			mappings=[{
				"simplefin_account_id": acct_id,
				"simplefin_account_name": "Test Checking",
				"simplefin_currency": "USD",
				"erpnext_bank_account": self.bank_account,
				"is_active": 1,
			}],
		)
		mock_cls.return_value.get_accounts.return_value = _accounts_response(
			acct_id,
			transactions=[
				{"id": "PEND-1", "pending": True, "amount": "-10.00",
				 "description": "Card hold, not posted yet"},
				{"id": "POSTED-1", "posted": 1718452800, "amount": "-25.00",
				 "description": "Posted payment", "pending": False},
			],
		)

		run_sync(conn.name, sync_type="Manual")

		conn.reload()
		self.assertEqual(conn.last_sync_status, "Success")
		log_name = frappe.get_all(
			"SimpleFIN Sync Log", filters={"connection": conn.name}, pluck="name"
		)[0]
		log = frappe.get_doc("SimpleFIN Sync Log", log_name)
		self.assertEqual(log.status, "Success")
		self.assertEqual(log.transactions_skipped_error, 0)
		self.assertEqual(log.transactions_skipped_pending, 1)
		self.assertEqual(log.transactions_created, 1)


class TestPartialSuccessOnConnection(_RunSyncTestBase):
	"""Review fix: import errors must surface on the connection's
	last_sync_status (Partial Success), not just inside the sync log."""

	@patch("sync_simplefin.utils.sync.SimpleFINClient")
	def test_malformed_transaction_marks_connection_partial(self, mock_cls):
		acct_id = f"TEST-ACCT-{frappe.generate_hash(length=6)}"
		self._account_ids.append(acct_id)
		conn = self._make_connection(
			initial_history_days=30,  # single chunk — keep counts unambiguous
			mappings=[{
				"simplefin_account_id": acct_id,
				"simplefin_account_name": "Test Checking",
				"simplefin_currency": "USD",
				"erpnext_bank_account": self.bank_account,
				"is_active": 1,
			}],
		)
		mock_cls.return_value.get_accounts.return_value = _accounts_response(
			acct_id,
			transactions=[
				{"id": "BAD-1", "posted": 1718452800, "amount": "not-a-number",
				 "description": "Malformed", "pending": False},
				{"id": "GOOD-1", "posted": 1718452800, "amount": "-25.00",
				 "description": "Fine", "pending": False},
			],
		)

		run_sync(conn.name, sync_type="Manual")

		conn.reload()
		self.assertEqual(conn.last_sync_status, "Partial Success")
		log_name = frappe.get_all(
			"SimpleFIN Sync Log", filters={"connection": conn.name}, pluck="name"
		)[0]
		log = frappe.get_doc("SimpleFIN Sync Log", log_name)
		self.assertEqual(log.status, "Partial Success")
		self.assertEqual(log.transactions_skipped_error, 1)
		self.assertEqual(log.transactions_created, 1)
