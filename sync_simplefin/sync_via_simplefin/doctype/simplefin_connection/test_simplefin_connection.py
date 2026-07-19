# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Unit tests for the SimpleFIN Connection doctype."""

import frappe
from frappe.tests.utils import FrappeTestCase

# Stop the test-record dependency walk at Bank Account: these tests provision
# everything they need themselves, and following the link chain into ERPNext
# reaches doctypes from the optional `payments` app (Payment Gateway), which
# crashes `bench run-tests` on sites without it (e.g. CI).
IGNORE_TEST_RECORD_DEPENDENCIES = ["Bank Account"]


class TestConnectionDeletionDetachesRecords(FrappeTestCase):
	"""Deleting a connection must clear Link fields on retained records.

	Bank Transactions and Sync Logs survive connection deletion
	(``ignore_links_on_delete`` in hooks.py), but a dangling
	``simplefin_connection`` link would make Frappe's link validation
	reject any later save of the transaction — which blocks bank
	reconciliation, since ``reconcile_vouchers`` calls
	``transaction.save()`` on the submitted document.
	"""

	def test_delete_clears_links_on_bank_transaction_and_sync_log(self):
		"""on_trash nulls simplefin_connection / connection on linked records."""
		conn = frappe.get_doc({
			"doctype": "SimpleFIN Connection",
			"connection_name": f"TestTrash_{frappe.generate_hash(length=6)}",
			"is_registered": 0,
			"enabled": 0,
			"sync_frequency": "Daily",
			"on_sync_failure": "Log Only",
			"on_empty_account": "Log Only",
			"on_record_mismatch": "Log Only",
		})
		conn.insert(ignore_permissions=True)

		bt = frappe.get_doc({
			"doctype": "Bank Transaction",
			"date": frappe.utils.today(),
			"status": "Unreconciled",
			"deposit": 100,
			"currency": "USD",
			"description": "connection deletion detach test",
			"simplefin_connection": conn.name,
			"simplefin_account_id": "acct-trash-test",
			"simplefin_transaction_id": f"txn-{frappe.generate_hash(length=8)}",
		})
		# Skip ERPNext validation and mandatory checks so no Bank Account
		# fixture is needed — only the Link field behaviour is under test.
		bt.flags.ignore_validate = True
		bt.flags.ignore_mandatory = True
		bt.insert(ignore_permissions=True)

		log = frappe.get_doc({
			"doctype": "SimpleFIN Sync Log",
			"connection": conn.name,
			"sync_type": "Manual",
			"status": "In Progress",
			"started_at": frappe.utils.now_datetime(),
		})
		log.insert(ignore_permissions=True)

		conn.delete()

		# Both records survive, with their link fields cleared and
		# dedup/traceability fields intact.
		bt.reload()
		self.assertIsNone(bt.simplefin_connection)
		self.assertEqual(bt.simplefin_account_id, "acct-trash-test")
		log.reload()
		self.assertIsNone(log.connection)

		# Link validation passes again. This is the check reconciliation's
		# transaction.save() runs; with a dangling link it raises
		# LinkValidationError ("Could not find SimpleFIN Connection: ...").
		bt._action = "save"
		bt._validate_links()

		log.delete()
		bt.delete()


class TestAccountMappingRegexValidation(FrappeTestCase):
	"""Finding 10: custom regexes on child mapping rows must be validated on
	parent save. Frappe does not run child validate() automatically, so the
	connection controller must drive it."""

	def _new_connection(self, regex):
		conn = frappe.get_doc({
			"doctype": "SimpleFIN Connection",
			"connection_name": f"RegexTest_{frappe.generate_hash(length=6)}",
			"is_registered": 0,
			"enabled": 0,
			"sync_frequency": "Daily",
			"on_sync_failure": "Log Only",
			"on_empty_account": "Log Only",
			"on_record_mismatch": "Log Only",
		})
		conn.append("account_mappings", {
			"simplefin_account_id": "acct-regex-test",
			"simplefin_account_name": "Regex Test",
			"custom_reference_regex": regex,
		})
		return conn

	def test_invalid_regex_rejected_on_save(self):
		"""A non-compiling child regex raises on connection save."""
		conn = self._new_connection(regex="(unbalanced")
		with self.assertRaises(frappe.ValidationError):
			conn.insert(ignore_permissions=True)

	def test_wrong_capture_group_count_rejected(self):
		"""A regex without exactly one capture group is rejected."""
		conn = self._new_connection(regex=r"\d+")  # zero capture groups
		with self.assertRaises(frappe.ValidationError):
			conn.insert(ignore_permissions=True)

	def test_valid_regex_accepted(self):
		"""A valid single-group regex saves cleanly."""
		conn = self._new_connection(regex=r"Ref:\s*(\w+)")
		conn.insert(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("SimpleFIN Connection", conn.name))
		conn.delete()


class TestEndpointPermissions(FrappeTestCase):
	"""Finding 3: whitelisted endpoints must enforce document permission, not
	rely on ignore_permissions writes reachable by any logged-in user."""

	def setUp(self):
		self.conn = frappe.get_doc({
			"doctype": "SimpleFIN Connection",
			"connection_name": f"PermTest_{frappe.generate_hash(length=6)}",
			"is_registered": 1,
			"enabled": 1,
			"sync_frequency": "Daily",
			"on_sync_failure": "Log Only",
			"on_empty_account": "Log Only",
			"on_record_mismatch": "Log Only",
		})
		self.conn.insert(ignore_permissions=True)

		self.user = frappe.get_doc({
			"doctype": "User",
			"email": f"lowpriv_{frappe.generate_hash(length=6)}@example.com",
			"first_name": "Low Priv",
			"send_welcome_email": 0,
			"roles": [],  # no role on SimpleFIN Connection
		})
		self.user.insert(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep: frappe-manual-commit -- persist user/conn before switching session user

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.delete_doc("SimpleFIN Connection", self.conn.name, force=True)
		frappe.delete_doc("User", self.user.name, force=True)
		frappe.db.commit()  # nosemgrep: frappe-manual-commit -- persist cleanup

	def test_sync_now_denied_for_unprivileged_user(self):
		from sync_simplefin.sync_via_simplefin.doctype.simplefin_connection.simplefin_connection import (
			sync_now,
		)

		frappe.set_user(self.user.name)
		with self.assertRaises(frappe.PermissionError):
			sync_now(self.conn.name)

	def test_test_connection_denied_for_unprivileged_user(self):
		from sync_simplefin.sync_via_simplefin.doctype.simplefin_connection.simplefin_connection import (
			test_connection,
		)

		frappe.set_user(self.user.name)
		with self.assertRaises(frappe.PermissionError):
			test_connection(self.conn.name)
