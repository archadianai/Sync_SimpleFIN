# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

import re

import frappe
from frappe import _
from frappe.model.document import Document
from datetime import datetime, timedelta

from frappe.utils import get_datetime, now_datetime

# Map sync_frequency to minutes for retry-window validation.
FREQUENCY_MINUTES = {
	"Every 2 Hours": 120,
	"4x Daily": 360,
	"Twice Daily": 720,
	"Daily": 1440,
	"Weekly": 10080,
	"Bi-Weekly": 20160,
	"Monthly": 40320,
}


class SimpleFINConnection(Document):
	"""A configured connection to a SimpleFIN Bridge access token."""

	def validate(self) -> None:
		self._validate_enabled_requires_registration()
		self._validate_sync_day_of_month()
		self._validate_retry_window()
		self._validate_custom_regex("custom_reference_regex", _("Custom Reference Regex"))
		self._validate_custom_regex("custom_party_regex", _("Custom Party Regex"))
		self._auto_activate_mapped_accounts()
		self._compute_next_scheduled_sync()

	def _auto_activate_mapped_accounts(self) -> None:
		"""Auto-set is_active=1 when an ERPNext Bank Account is assigned."""
		for m in (self.account_mappings or []):
			if m.erpnext_bank_account and not m.is_active:
				m.is_active = 1

	def _validate_enabled_requires_registration(self) -> None:
		if self.enabled and not self.is_registered:
			frappe.throw(
				_("Connection cannot be enabled until registration is complete.")
			)

	def _validate_sync_day_of_month(self) -> None:
		if self.sync_frequency == "Monthly" and self.sync_day_of_month:
			if not (1 <= self.sync_day_of_month <= 28):
				frappe.throw(
					_("Sync Day of Month must be between 1 and 28.")
				)

	def _validate_retry_window(self) -> None:
		"""Ensure retry_count * retry_interval fits inside the sync interval."""
		max_retry_window = (self.retry_count or 0) * (self.retry_interval_minutes or 0)
		sync_interval = FREQUENCY_MINUTES.get(self.sync_frequency, 0)

		if sync_interval and max_retry_window >= sync_interval:
			frappe.throw(
				_(
					"Retry window ({0} retries × {1} min = {2} min) must be shorter "
					"than sync interval ({3} min). Reduce retry count or interval."
				).format(
					self.retry_count,
					self.retry_interval_minutes,
					max_retry_window,
					sync_interval,
				)
			)

	def _validate_custom_regex(self, field: str, label: str) -> None:
		"""Validate a custom regex pattern compiles and has exactly one capture group."""
		pattern = self.get(field)
		if not pattern:
			return
		try:
			compiled = re.compile(pattern)
		except re.error as e:
			frappe.throw(_("Invalid regex in {0}: {1}").format(label, str(e)))
		if compiled.groups != 1:
			frappe.throw(
				_("{0} must contain exactly one capture group. Found {1}.").format(
					label, compiled.groups
				)
			)

	def _compute_next_scheduled_sync(self) -> None:
		"""Calculate and store the next scheduled sync datetime."""
		if not self.enabled or not self.is_registered:
			self.next_scheduled_sync = None
			return

		if self.rate_limit_paused_until:
			paused = get_datetime(self.rate_limit_paused_until)
			if paused > now_datetime():
				self.next_scheduled_sync = paused
				return

		if self.sync_state == "Retry Pending" and self.next_retry_at:
			self.next_scheduled_sync = get_datetime(self.next_retry_at)
			return

		now = now_datetime()
		last = get_datetime(self.last_sync_attempt) if self.last_sync_attempt else None
		freq = self.sync_frequency

		# Sub-daily: interval-based
		interval_map = {
			"Every 2 Hours": 120,
			"4x Daily": 360,
			"Twice Daily": 720,
		}
		if freq in interval_map:
			if not last:
				self.next_scheduled_sync = now
			else:
				self.next_scheduled_sync = last + timedelta(minutes=interval_map[freq])
			return

		# Daily and longer: clock-based
		hour, minute = _parse_sync_time(self.sync_time)

		if freq == "Daily":
			candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
			if candidate <= now:
				candidate += timedelta(days=1)
			self.next_scheduled_sync = candidate

		elif freq == "Weekly":
			candidate = _next_weekday(now, self.sync_day_of_week, hour, minute)
			self.next_scheduled_sync = candidate

		elif freq == "Bi-Weekly":
			candidate = _next_weekday(now, self.sync_day_of_week, hour, minute)
			if last and (candidate - last).days < 14:
				candidate += timedelta(weeks=1)
			self.next_scheduled_sync = candidate

		elif freq == "Monthly":
			day = self.sync_day_of_month or 1
			candidate = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
			if candidate <= now:
				# Next month
				if now.month == 12:
					candidate = candidate.replace(year=now.year + 1, month=1)
				else:
					candidate = candidate.replace(month=now.month + 1)
			self.next_scheduled_sync = candidate


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _parse_sync_time(time_val) -> tuple[int, int]:
	"""Parse a time value into (hour, minute)."""
	if not time_val:
		return 2, 0
	if isinstance(time_val, timedelta):
		total = int(time_val.total_seconds())
		return total // 3600, (total % 3600) // 60
	parts = str(time_val).split(":")
	try:
		return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
	except (ValueError, IndexError):
		return 2, 0


def _next_weekday(now, day_name: str, hour: int, minute: int):
	"""Return the next occurrence of the given weekday at the given time."""
	target_dow = _DAY_NAMES.index(day_name) if day_name in _DAY_NAMES else 0
	days_ahead = target_dow - now.weekday()
	if days_ahead < 0:
		days_ahead += 7
	candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)
	if candidate <= now:
		candidate += timedelta(weeks=1)
	return candidate


# ---------------------------------------------------------------------------
# Whitelisted methods (called from JS controller)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_timezone_options() -> list[str]:
	"""Return a sorted list of all available IANA timezones."""
	from zoneinfo import available_timezones

	return sorted(available_timezones())


@frappe.whitelist()
def register_token(connection: str) -> None:
	"""Exchange the setup token for an access URL and store it encrypted."""
	from simplefin_sync.utils.simplefin_client import (
		SimpleFINAuthError,
		SimpleFINClient,
		SimpleFINError,
	)

	conn = frappe.get_doc("SimpleFIN Connection", connection)

	if conn.is_registered:
		frappe.throw(_("This connection is already registered."))

	setup_token = conn.get_password("setup_token")
	if not setup_token:
		frappe.throw(_("Please enter a Setup Token before registering."))

	try:
		access_url = SimpleFINClient.claim_access_url(setup_token)
	except SimpleFINAuthError:
		frappe.throw(
			_(
				"This setup token has already been used or is compromised. "
				"Please generate a new token from SimpleFIN Bridge."
			)
		)
	except SimpleFINError as e:
		frappe.throw(_("Registration failed: {0}").format(str(e)))

	conn.access_url = access_url
	conn.is_registered = 1
	conn.setup_token = ""
	conn.registration_date = now_datetime()
	conn.connection_status = "Active"
	conn.save(ignore_permissions=True)
	frappe.db.commit()

	# Auto-populate account mappings by fetching the account list
	_populate_account_mappings(conn, SimpleFINClient(access_url))


@frappe.whitelist()
def test_connection(connection: str) -> str:
	"""Test the connection by fetching balances only. Returns formatted HTML."""
	from simplefin_sync.utils.simplefin_client import SimpleFINClient, SimpleFINError

	conn = frappe.get_doc("SimpleFIN Connection", connection)

	if not conn.is_registered:
		frappe.throw(_("Connection is not registered."))

	access_url = conn.get_password("access_url")
	if not access_url:
		frappe.throw(_("No access URL stored. Please re-register."))

	try:
		client = SimpleFINClient(access_url)
		data = client.test_connection()
	except SimpleFINError as e:
		frappe.throw(_("Connection test failed: {0}").format(str(e)))

	# Format response as readable HTML
	accounts = data.get("accounts", [])
	if not accounts:
		return _("Connection successful but no accounts found.")

	lines = [_("<b>Connection successful!</b> Found {0} account(s):").format(len(accounts)), "<br><br>"]
	for acct in accounts:
		name = acct.get("name", _("Unknown"))
		currency = acct.get("currency", "")
		balance = acct.get("balance", "N/A")
		lines.append(f"<b>{name}</b> ({currency}): {balance}<br>")

	errors = data.get("errors", [])
	if errors:
		lines.append(f"<br><b>{_('Warnings')}:</b><br>")
		for err in errors:
			lines.append(f"- {frappe.utils.escape_html(err)}<br>")

	return "".join(lines)


@frappe.whitelist()
def sync_now(connection: str) -> None:
	"""Queue an immediate sync job for this connection."""
	conn = frappe.get_doc("SimpleFIN Connection", connection)

	if not conn.enabled:
		frappe.throw(_("Connection is not enabled."))

	if conn.rate_limit_paused_until and now_datetime() < conn.rate_limit_paused_until:
		frappe.throw(
			_("Connection is rate-limited until {0}. Sync blocked.").format(
				conn.rate_limit_paused_until
			)
		)

	conn.sync_state = "Queued"
	conn.save(ignore_permissions=True)

	frappe.enqueue(
		"simplefin_sync.utils.sync.run_sync",
		connection=conn.name,
		queue="long",
		deduplicate=True,
		job_id=f"simplefin_sync_{conn.name}",
		timeout=600,
	)
	frappe.db.commit()


@frappe.whitelist()
def sync_full(connection: str) -> None:
	"""Reset last_sync_end_date and queue a full history sync."""
	conn = frappe.get_doc("SimpleFIN Connection", connection)

	if not conn.enabled:
		frappe.throw(_("Connection is not enabled."))

	if conn.rate_limit_paused_until and now_datetime() < conn.rate_limit_paused_until:
		frappe.throw(
			_("Connection is rate-limited until {0}. Sync blocked.").format(
				conn.rate_limit_paused_until
			)
		)

	conn.last_sync_end_date = 0
	conn.sync_state = "Queued"
	conn.save(ignore_permissions=True)

	frappe.enqueue(
		"simplefin_sync.utils.sync.run_sync",
		connection=conn.name,
		queue="long",
		deduplicate=True,
		job_id=f"simplefin_sync_{conn.name}",
		timeout=600,
	)
	frappe.db.commit()


@frappe.whitelist()
def clear_rate_limit_pause(connection: str) -> None:
	"""Clear the rate limit pause (System Manager only)."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only System Managers can clear rate limit pauses."))

	frappe.db.set_value(
		"SimpleFIN Connection",
		connection,
		{
			"rate_limit_paused_until": None,
			"connection_status": "Active",
		},
	)
	frappe.db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _populate_account_mappings(conn, client) -> None:
	"""Fetch accounts from SimpleFIN and populate the mappings table.

	Called after registration to give the user something to map immediately.
	Uses balances-only mode to minimise API usage.
	"""
	try:
		data = client.test_connection()
	except Exception:
		# Non-fatal — user can still map manually or via Refresh Accounts later
		return

	accounts = data.get("accounts", [])
	if not accounts:
		return

	existing_ids = {m.simplefin_account_id for m in (conn.account_mappings or [])}
	now = now_datetime()
	default_tz = frappe.db.get_single_value("System Settings", "time_zone") or "UTC"
	changed = False

	# Update org info
	org = accounts[0].get("org", {})
	if org and not conn.org_name:
		conn.org_name = org.get("name", "")
		conn.org_domain = org.get("domain", "")
		conn.org_url = org.get("url", "")
		changed = True

	for acct in accounts:
		acct_id = acct.get("id")
		if not acct_id or acct_id in existing_ids:
			continue
		acct_org = acct.get("org", {})
		conn.append("account_mappings", {
			"simplefin_account_id": acct_id,
			"simplefin_account_name": acct.get("name", ""),
			"simplefin_org_domain": acct_org.get("domain", ""),
			"simplefin_org_name": acct_org.get("name", ""),
			"simplefin_currency": acct.get("currency", ""),
			"transaction_timezone": default_tz,
			"is_active": 0,
			"first_seen": now,
			"last_seen": now,
		})
		existing_ids.add(acct_id)
		changed = True

	if changed:
		conn.save(ignore_permissions=True)
		frappe.db.commit()
