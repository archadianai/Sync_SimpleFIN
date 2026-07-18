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

	# A notification-delivery failure (e.g. no outgoing email account) must
	# never propagate — these run inside the sync transaction and would
	# otherwise abort and roll back the whole sync.
	try:
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
	except Exception:
		frappe.logger(__name__).error(
			f"[{connection.get('name')}] failed to dispatch notification",
			exc_info=True,
		)


def notify_sync_failure(connection, error_message: str, sync_log_name: str = "") -> None:
	"""Notify about a sync failure."""
	esc = frappe.utils.escape_html
	subject = _("SimpleFIN Sync Failed: {0}").format(
		esc(connection.get("connection_name") or connection.get("name"))
	)
	# error_message may include SimpleFIN-supplied text (account names, IDs);
	# escape it before it lands in an HTML notification body.
	msg = esc(error_message)
	if sync_log_name:
		msg += f"<br><br>{_('Sync Log')}: {esc(sync_log_name)}"

	send_notification(connection, "on_sync_failure", subject, msg)


def notify_empty_account(connection, account_name: str) -> None:
	"""Notify when a mapped account returns no transactions."""
	esc = frappe.utils.escape_html
	name = esc(account_name)
	conn_name = esc(connection.get("connection_name") or connection.get("name"))
	subject = _("SimpleFIN: No Transactions for {0}").format(name)
	msg = _(
		"No transactions returned for mapped account '{0}' on connection '{1}'."
	).format(name, conn_name)

	send_notification(connection, "on_empty_account", subject, msg)


def notify_record_mismatch(
	connection,
	transaction_id: str,
	account_id: str,
	differences: str,
) -> None:
	"""Notify about a transaction data mismatch (same ID, different data).

	``differences`` is HTML built by the caller from stored record values and
	is intentionally not escaped here; the externally-supplied identifiers are.
	"""
	esc = frappe.utils.escape_html
	subject = _("SimpleFIN: Transaction Mismatch Detected")
	msg = _(
		"Transaction {0} (account {1}) on connection '{2}' has different data "
		"than the stored record.<br><br>{3}"
	).format(
		esc(transaction_id),
		esc(account_id),
		esc(connection.get("connection_name") or connection.get("name")),
		differences,
	)

	send_notification(connection, "on_record_mismatch", subject, msg)


def notify_connection_revoked(connection) -> None:
	"""Notify about a revoked connection (always sends both email and system notification)."""
	esc = frappe.utils.escape_html
	conn_name = esc(connection.get("connection_name") or connection.get("name"))
	subject = _("SimpleFIN Connection Revoked: {0}").format(conn_name)
	msg = _(
		"The SimpleFIN access token for '{0}' has been revoked (HTTP 403). "
		"Please re-register with a new setup token from SimpleFIN Bridge."
	).format(conn_name)

	# Always log
	frappe.logger(__name__).warning(f"[{connection.get('name')}] {subject}")

	# Each delivery channel is independently guarded: a realtime failure
	# (redis down in a worker) must not suppress the email, and neither
	# failure may propagate into the sync.
	try:
		frappe.publish_realtime(
			"msgprint",
			{"message": f"<b>{subject}</b><br>{msg}", "alert": True},
			user=frappe.session.user,
		)
	except Exception:
		frappe.logger(__name__).error(
			f"[{connection.get('name')}] failed to send revoked system notification",
			exc_info=True,
		)

	try:
		recipients = _parse_recipients(connection.get("notification_recipients"))
		if recipients:
			frappe.sendmail(
				recipients=recipients,
				subject=subject,
				message=msg,
			)
	except Exception:
		frappe.logger(__name__).error(
			f"[{connection.get('name')}] failed to send revoked email",
			exc_info=True,
		)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_recipients(recipients_str: str | None) -> list[str]:
	"""Parse a comma-separated string of email addresses."""
	if not recipients_str:
		return []
	return [r.strip() for r in recipients_str.split(",") if r.strip()]
