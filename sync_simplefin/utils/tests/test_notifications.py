# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Tests for notification helpers — XSS escaping and delivery-failure safety."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sync_simplefin.utils.notifications import (
	notify_record_mismatch,
	send_notification,
)

XSS = "<img src=x onerror=alert(1)>"


class TestNotificationEscaping(FrappeTestCase):
	"""Finding 4: SimpleFIN-supplied data rendered via msgprint must be escaped."""

	@patch("sync_simplefin.utils.notifications.frappe.publish_realtime")
	def test_mismatch_escapes_external_ids(self, mock_publish):
		conn = frappe._dict({
			"name": "SFIN-X",
			"connection_name": XSS,
			"on_record_mismatch": "System Notification",
		})

		notify_record_mismatch(conn, transaction_id=XSS, account_id=XSS, differences="amount changed")

		message = mock_publish.call_args[0][1]["message"]
		self.assertNotIn("<img", message)
		self.assertIn("&lt;img", message)


class TestNotificationDeliverySafety(FrappeTestCase):
	"""Finding P3: a delivery failure must never propagate out of a sync."""

	@patch("sync_simplefin.utils.notifications.frappe.sendmail", side_effect=Exception("no email account"))
	def test_email_failure_is_swallowed(self, mock_sendmail):
		conn = frappe._dict({
			"name": "SFIN-X",
			"connection_name": "Test",
			"on_sync_failure": "Email",
			"notification_recipients": "ops@example.com",
		})

		# Must not raise even though sendmail blows up.
		send_notification(conn, "on_sync_failure", "Subject", "Body")
		mock_sendmail.assert_called_once()
