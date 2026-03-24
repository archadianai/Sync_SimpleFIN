# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Notification helpers for SimpleFIN Sync events."""

from __future__ import annotations

import frappe
from frappe import _


def send_notification(
	connection,
	setting_field: str,
	subject: str,
	message: str,
) -> None:
	"""Dispatch a notification according to the connection's preference.

	Args:
		connection: SimpleFIN Connection doc (or dict).
		setting_field: Name of the connection field that controls behaviour
			(``on_sync_failure``, ``on_empty_account``, ``on_record_mismatch``).
		subject: Email / notification subject line.
		message: Body text (plain text or simple HTML).
	"""
	mode = connection.get(setting_field, "Log Only")

	# Always log
	frappe.logger(__name__).info(f"[{connection.get('name')}] {subject}: {message}")

	if mode == "Email":
		recipients = _parse_recipients(connection.get("notification_recipients"))
		if recipients:
			frappe.sendmail(
				recipients=recipients,
				subject=subject,
				message=message,
			)

	elif mode == "System Notification":
		frappe.publish_realtime(
			"msgprint",
			{"message": f"<b>{subject}</b><br>{message}", "alert": True},
			user=frappe.session.user,
		)


def notify_sync_failure(connection, error_message: str, sync_log_name: str = "") -> None:
	"""Notify about a sync failure."""
	subject = _("SimpleFIN Sync Failed: {0}").format(
		connection.get("connection_name") or connection.get("name")
	)
	msg = error_message
	if sync_log_name:
		msg += f"<br><br>{_('Sync Log')}: {sync_log_name}"

	send_notification(connection, "on_sync_failure", subject, msg)


def notify_empty_account(connection, account_name: str) -> None:
	"""Notify when a mapped account returns no transactions."""
	subject = _("SimpleFIN: No Transactions for {0}").format(account_name)
	msg = _(
		"No transactions returned for mapped account '{0}' on connection '{1}'."
	).format(account_name, connection.get("connection_name") or connection.get("name"))

	send_notification(connection, "on_empty_account", subject, msg)


def notify_record_mismatch(
	connection,
	transaction_id: str,
	account_id: str,
	differences: str,
) -> None:
	"""Notify about a transaction data mismatch (same ID, different data)."""
	subject = _("SimpleFIN: Transaction Mismatch Detected")
	msg = _(
		"Transaction {0} (account {1}) on connection '{2}' has different data "
		"than the stored record.<br><br>{3}"
	).format(
		transaction_id,
		account_id,
		connection.get("connection_name") or connection.get("name"),
		differences,
	)

	send_notification(connection, "on_record_mismatch", subject, msg)


def notify_connection_revoked(connection) -> None:
	"""Notify about a revoked connection (always sends both email and system notification)."""
	subject = _("SimpleFIN Connection Revoked: {0}").format(
		connection.get("connection_name") or connection.get("name")
	)
	msg = _(
		"The SimpleFIN access token for '{0}' has been revoked (HTTP 403). "
		"Please re-register with a new setup token from SimpleFIN Bridge."
	).format(connection.get("connection_name") or connection.get("name"))

	# Always log
	frappe.logger(__name__).warning(f"[{connection.get('name')}] {subject}")

	# Always send system notification
	frappe.publish_realtime(
		"msgprint",
		{"message": f"<b>{subject}</b><br>{msg}", "alert": True},
		user=frappe.session.user,
	)

	# Always email recipients if configured
	recipients = _parse_recipients(connection.get("notification_recipients"))
	if recipients:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=msg,
		)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_recipients(recipients_str: str | None) -> list[str]:
	"""Parse a comma-separated string of email addresses."""
	if not recipients_str:
		return []
	return [r.strip() for r in recipients_str.split(",") if r.strip()]
