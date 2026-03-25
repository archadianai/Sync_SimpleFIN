# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Core sync logic — fetch transactions from SimpleFIN and create Bank Transactions.

This module implements the sync flow described in spec Section 5.1, including
date-range chunking (newest-first), deduplication, mismatch detection, rate-limit
detection, and balance snapshot storage.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import now_datetime

from simplefin_sync.utils.enrichment import enrich_transaction
from simplefin_sync.utils.notifications import (
	notify_connection_revoked,
	notify_empty_account,
	notify_record_mismatch,
	notify_sync_failure,
)
from simplefin_sync.utils.simplefin_client import (
	SimpleFINAuthError,
	SimpleFINClient,
	SimpleFINError,
	SimpleFINHTTPError,
	SimpleFINPaymentRequired,
)

logger = frappe.logger(__name__)

# Rate-limit keywords (case-insensitive) — spec Section 5.5.
RATE_LIMIT_PATTERNS = [
	"rate", "limit", "quota", "throttle", "too many requests",
	"slow down", "exceeded",
]

# Batch size for committing Bank Transactions.
COMMIT_BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_sync(connection: str) -> None:
	"""Run a full sync for the given SimpleFIN Connection.

	Called from ``frappe.enqueue`` via the scheduler or the *Sync Now* button.
	Manages state-machine transitions per spec Section 6.4.
	"""
	conn = frappe.get_doc("SimpleFIN Connection", connection)

	# Transition: Queued → Syncing
	conn.sync_state = "Syncing"
	conn.last_sync_attempt = now_datetime()
	conn.save(ignore_permissions=True)
	frappe.db.commit()

	sync_log = _create_sync_log(conn, "Scheduled" if frappe.flags.in_scheduler else "Manual")

	try:
		_do_sync(conn, sync_log)

		# Success
		conn.reload()
		conn.sync_state = "Idle"
		conn.last_sync_status = "Success"
		conn.last_successful_sync = now_datetime()
		conn.retry_attempts_used = 0
		conn.next_retry_at = None
		if conn.connection_status in ("Unknown", "Rate Limited"):
			conn.connection_status = "Active"
		conn.rate_limit_paused_until = None
		conn.save(ignore_permissions=True)

		sync_log.reload()
		sync_log.status = "Success"
		sync_log.completed_at = now_datetime()
		sync_log.save(ignore_permissions=True)

	except Exception as e:
		frappe.db.rollback()
		conn.reload()
		conn.last_sync_status = "Failed"
		conn.last_sync_error = str(e)[:1000]
		conn.retry_attempts_used = (conn.retry_attempts_used or 0) + 1

		if conn.retry_attempts_used < (conn.retry_count or 0):
			conn.sync_state = "Retry Pending"
			conn.next_retry_at = now_datetime() + timedelta(
				minutes=conn.retry_interval_minutes or 30
			)
		else:
			conn.sync_state = "Failed"
			conn.next_retry_at = None
			notify_sync_failure(conn, str(e), sync_log.name)

		conn.save(ignore_permissions=True)

		sync_log.reload()
		sync_log.status = "Failed"
		sync_log.error_message = frappe.get_traceback(with_context=False)[:5000]
		sync_log.completed_at = now_datetime()
		sync_log.save(ignore_permissions=True)

	frappe.db.commit()


def on_bank_transaction_trash(doc, method) -> None:
	"""Hook called when a Bank Transaction is deleted.

	Currently a no-op placeholder for future cleanup logic.
	"""
	pass


# ---------------------------------------------------------------------------
# Internal sync implementation
# ---------------------------------------------------------------------------

def _do_sync(conn, sync_log) -> None:
	"""Execute the actual sync logic (Section 5.1 steps 3-7)."""
	access_url = conn.get_password("access_url")
	if not access_url:
		raise SimpleFINError(_("No access URL stored. Please re-register."))

	client = SimpleFINClient(access_url)

	# Build active account mapping lookup
	mapped = _get_active_mappings(conn)
	mapped_ids = [m.simplefin_account_id for m in mapped] if mapped else None

	# Calculate date range
	start_ts, end_ts = _calculate_date_range(conn)
	chunks = build_chunks(start_ts, end_ts)

	sync_log.request_start_date = datetime.utcfromtimestamp(start_ts)
	sync_log.request_end_date = datetime.utcfromtimestamp(end_ts)
	sync_log.chunks_requested = len(chunks)
	sync_log.save(ignore_permissions=True)
	frappe.db.commit()

	total_created = 0
	total_retrieved = 0
	total_dup = 0
	total_pending = 0
	total_cancelled = 0
	total_mismatch = 0
	chunks_completed = 0
	chunks_empty = 0
	all_simplefin_errors: list[str] = []
	rate_limit_hit = False

	for i, (chunk_start, chunk_end) in enumerate(chunks):
		try:
			data = client.get_accounts(
				start_date=chunk_start,
				end_date=chunk_end,
				account_ids=mapped_ids,
				include_pending=bool(conn.include_pending),
			)
		except SimpleFINAuthError:
			conn.reload()
			conn.connection_status = "Revoked"
			conn.enabled = 0
			conn.save(ignore_permissions=True)
			notify_connection_revoked(conn)
			raise
		except SimpleFINPaymentRequired:
			conn.reload()
			conn.connection_status = "Payment Required"
			conn.save(ignore_permissions=True)
			raise
		except SimpleFINHTTPError:
			raise
		except SimpleFINError:
			raise

		chunks_completed += 1

		# Process errors array
		errors = data.get("errors", [])
		if errors:
			all_simplefin_errors.extend(errors)

		# Rate limit detection (spec Section 5.5)
		if _contains_rate_limit_warning(errors):
			rate_limit_hit = True
			_activate_rate_limit_pause(conn)

		# Process accounts
		accounts = data.get("accounts", [])
		sync_log.accounts_retrieved = (sync_log.accounts_retrieved or 0) + len(accounts)

		# Update org info on first sync
		if accounts and not conn.org_name:
			_update_org_info(conn, accounts[0])

		# Auto-discover and update account mappings
		_update_account_mappings(conn, accounts)

		# Refresh mapped lookup after potential new mappings
		mapped = _get_active_mappings(conn)
		mapping_lookup = {m.simplefin_account_id: m for m in mapped}

		chunk_txn_count = 0
		chunk_created = 0

		for acct in accounts:
			acct_id = acct.get("id")

			# Store balance snapshot
			_store_balance_snapshot(sync_log, acct)

			# Skip unmapped or inactive accounts
			mapping = mapping_lookup.get(acct_id)
			if not mapping or not mapping.erpnext_bank_account:
				continue

			transactions = acct.get("transactions", [])
			chunk_txn_count += len(transactions)

			for txn in transactions:
				# Skip pending unless enabled
				if txn.get("pending") and not conn.include_pending:
					total_pending += 1
					continue

				result = _process_transaction(
					txn, acct_id, mapping.erpnext_bank_account, conn
				)
				if result == "created":
					chunk_created += 1
				elif result == "duplicate":
					total_dup += 1
				elif result == "cancelled":
					total_cancelled += 1
				elif result == "mismatch":
					total_mismatch += 1

			# Commit in batches
			if chunk_created > 0 and chunk_created % COMMIT_BATCH_SIZE == 0:
				frappe.db.commit()

		total_retrieved += chunk_txn_count
		total_created += chunk_created
		frappe.db.commit()

		# Stop conditions (spec Section 5.4)
		if chunk_txn_count == 0:
			chunks_empty += 1
			logger.info(
				f"Chunk {i + 1}: no data returned. Stopping backfill."
			)
			break

		if chunk_created == 0 and total_dup > 0:
			logger.info(
				f"Chunk {i + 1}: all transactions already imported. "
				f"Backfill complete."
			)
			break

		if rate_limit_hit:
			logger.warning(
				f"Rate limit warning detected. Processed {i + 1}/{len(chunks)} chunks. "
				f"Aborting remaining."
			)
			break

	# Update last_sync_end_date to the end of the FIRST (newest) chunk
	conn.reload()
	conn.last_sync_end_date = end_ts
	conn.save(ignore_permissions=True)

	# Finalize sync log
	sync_log.reload()
	sync_log.transactions_retrieved = total_retrieved
	sync_log.transactions_created = total_created
	sync_log.transactions_skipped_duplicate = total_dup
	sync_log.transactions_skipped_pending = total_pending
	sync_log.transactions_skipped_cancelled = total_cancelled
	sync_log.transactions_mismatched = total_mismatch
	sync_log.chunks_completed = chunks_completed
	sync_log.chunks_empty = chunks_empty
	sync_log.rate_limit_warning_received = 1 if rate_limit_hit else 0
	if all_simplefin_errors:
		sync_log.simplefin_errors = "\n".join(
			frappe.utils.escape_html(e) for e in all_simplefin_errors
		)
	sync_log.save(ignore_permissions=True)
	frappe.db.commit()


# ---------------------------------------------------------------------------
# Date range and chunking
# ---------------------------------------------------------------------------

def _calculate_date_range(conn) -> tuple[int, int]:
	"""Return (start_ts, end_ts) as UNIX timestamps."""
	import time

	end_ts = int(time.time())

	if conn.last_sync_end_date:
		# Ongoing sync: overlap by rolling_window_days
		rolling = conn.rolling_window_days or 14
		start_ts = conn.last_sync_end_date - (rolling * 86400)
	else:
		# First sync: pull initial_history_days
		initial = conn.initial_history_days or 90
		start_ts = end_ts - (initial * 86400)

	return start_ts, end_ts


def build_chunks(
	start_ts: int,
	end_ts: int,
	max_days: int = 45,
) -> list[tuple[int, int]]:
	"""Split a date range into ≤90-day chunks, newest first.

	Returns a list of ``(chunk_start_ts, chunk_end_ts)`` tuples ordered
	from most recent to oldest.
	"""
	max_seconds = max_days * 86400
	chunks: list[tuple[int, int]] = []
	current_end = end_ts

	while current_end > start_ts:
		chunk_start = max(current_end - max_seconds, start_ts)
		chunks.append((chunk_start, current_end))
		current_end = chunk_start

	return chunks


# ---------------------------------------------------------------------------
# Transaction processing
# ---------------------------------------------------------------------------

def _process_transaction(
	txn: dict,
	account_id: str,
	bank_account: str,
	conn,
) -> str:
	"""Process a single transaction. Returns 'created', 'duplicate', 'cancelled', or 'mismatch'."""
	txn_id = txn.get("id")
	if not txn_id:
		logger.warning("Transaction missing 'id' field, skipping.")
		return "duplicate"

	amount_str = txn.get("amount")
	if not amount_str:
		logger.warning(f"Transaction {txn_id} missing 'amount', skipping.")
		return "duplicate"

	description = txn.get("description", "")
	posted = txn.get("posted")
	if not posted:
		logger.warning(f"Transaction {txn_id} missing 'posted', skipping.")
		return "duplicate"

	# Dedup check — ALL docstatuses (spec Section 5.3)
	existing = frappe.db.sql(
		"""
		SELECT name, date, description, deposit, withdrawal, docstatus
		FROM `tabBank Transaction`
		WHERE simplefin_account_id = %s
		  AND simplefin_transaction_id = %s
		""",
		(account_id, txn_id),
		as_dict=True,
	)

	now = now_datetime()

	if existing:
		record = existing[0]

		# Always update simplefin_last_seen
		frappe.db.set_value(
			"Bank Transaction", record.name,
			"simplefin_last_seen", now,
			update_modified=False,
		)

		if record.docstatus == 2:
			# Cancelled — skip, still tracking
			return "cancelled"

		# Mismatch detection on non-cancelled records
		try:
			amount = Decimal(amount_str)
		except InvalidOperation:
			amount = Decimal(0)

		new_deposit = float(abs(amount)) if amount > 0 else 0
		new_withdrawal = float(abs(amount)) if amount < 0 else 0
		posted_date = _unix_to_date(posted, conn.transaction_timezone)

		differences = []
		if str(record.date) != str(posted_date):
			differences.append(f"date: {record.date} → {posted_date}")
		if abs(float(record.deposit or 0) - new_deposit) > 0.01:
			differences.append(f"deposit: {record.deposit} → {new_deposit}")
		if abs(float(record.withdrawal or 0) - new_withdrawal) > 0.01:
			differences.append(f"withdrawal: {record.withdrawal} → {new_withdrawal}")

		if differences:
			notify_record_mismatch(
				conn, txn_id, account_id, "<br>".join(differences)
			)
			return "mismatch"

		return "duplicate"

	# --- Create new Bank Transaction ---
	try:
		amount = Decimal(amount_str)
	except InvalidOperation:
		logger.error(f"Transaction {txn_id}: invalid amount '{amount_str}', skipping.")
		return "duplicate"

	deposit = float(abs(amount)) if amount > 0 else 0.0
	withdrawal = float(abs(amount)) if amount < 0 else 0.0
	abs_amount = float(abs(amount))

	posted_date = _unix_to_date(posted, conn.transaction_timezone)
	sanitized_desc = frappe.utils.escape_html(description)[:500] if description else ""

	# Enrichment
	enriched = enrich_transaction(description, txn.get("extra"), conn)

	bt = frappe.get_doc({
		"doctype": "Bank Transaction",
		"date": posted_date,
		"bank_account": bank_account,
		"deposit": deposit,
		"withdrawal": withdrawal,
		"currency": conn.account_mappings[0].simplefin_currency if conn.account_mappings else "USD",
		"description": sanitized_desc,
		"reference_number": enriched.get("reference_number") or "",
		"bank_party_name": enriched.get("bank_party_name") or "",
		"status": "Unreconciled",
		"unallocated_amount": abs_amount,
		"allocated_amount": 0,
		"simplefin_transaction_id": txn_id,
		"simplefin_connection": conn.name,
		"simplefin_account_id": account_id,
		"simplefin_posted_at": datetime.utcfromtimestamp(posted),
		"simplefin_raw_amount": amount_str,
		"simplefin_last_seen": now,
	})

	transacted_at = txn.get("transacted_at")
	if transacted_at:
		bt.simplefin_transacted_at = datetime.utcfromtimestamp(transacted_at)

	if txn.get("pending"):
		bt.simplefin_pending = 1

	bt.insert(ignore_permissions=True)
	bt.submit()

	return "created"


# ---------------------------------------------------------------------------
# Account mapping management
# ---------------------------------------------------------------------------

def _get_active_mappings(conn) -> list:
	"""Return active account mapping rows with an ERPNext Bank Account set."""
	return [
		m for m in (conn.account_mappings or [])
		if m.is_active and m.erpnext_bank_account
	]


def _update_account_mappings(conn, accounts: list[dict]) -> None:
	"""Auto-discover new SimpleFIN accounts and update existing mappings."""
	existing_ids = {m.simplefin_account_id for m in (conn.account_mappings or [])}
	now = now_datetime()
	changed = False

	for acct in accounts:
		acct_id = acct.get("id")
		if not acct_id:
			continue

		if acct_id in existing_ids:
			# Update last_seen
			for m in conn.account_mappings:
				if m.simplefin_account_id == acct_id:
					m.last_seen = now
					if m.missing_from_simplefin:
						m.missing_from_simplefin = 0
					changed = True
					break
		else:
			# Auto-add new account (inactive, no ERPNext mapping)
			org = acct.get("org", {})
			conn.append("account_mappings", {
				"simplefin_account_id": acct_id,
				"simplefin_account_name": acct.get("name", ""),
				"simplefin_org_domain": org.get("domain", ""),
				"simplefin_org_name": org.get("name", ""),
				"simplefin_currency": acct.get("currency", ""),
				"is_active": 0,
				"first_seen": now,
				"last_seen": now,
			})
			existing_ids.add(acct_id)
			changed = True

	# Flag accounts that disappeared
	response_ids = {acct.get("id") for acct in accounts if acct.get("id")}
	for m in (conn.account_mappings or []):
		if m.simplefin_account_id not in response_ids and not m.missing_from_simplefin:
			m.missing_from_simplefin = 1
			changed = True

	if changed:
		conn.save(ignore_permissions=True)
		frappe.db.commit()


def _update_org_info(conn, account: dict) -> None:
	"""Populate org fields on the connection from the first account response."""
	org = account.get("org", {})
	if not org:
		return

	conn.reload()
	conn.org_domain = org.get("domain", "")
	conn.org_name = org.get("name", "")
	conn.org_url = org.get("url", "")
	conn.save(ignore_permissions=True)
	frappe.db.commit()


# ---------------------------------------------------------------------------
# Balance snapshots
# ---------------------------------------------------------------------------

def _store_balance_snapshot(sync_log, account: dict) -> None:
	"""Add a balance snapshot row to the sync log."""
	balance_date = account.get("balance-date")
	sync_log.append("balance_snapshot", {
		"simplefin_account_id": account.get("id", ""),
		"simplefin_account_name": account.get("name", ""),
		"currency": account.get("currency", ""),
		"balance": float(Decimal(account.get("balance", "0"))),
		"available_balance": float(Decimal(account.get("available-balance", "0")))
		if account.get("available-balance")
		else 0,
		"balance_date": datetime.utcfromtimestamp(balance_date)
		if balance_date
		else None,
	})


# ---------------------------------------------------------------------------
# Rate limit detection
# ---------------------------------------------------------------------------

def _contains_rate_limit_warning(errors: list[str]) -> bool:
	"""Check if any error message suggests rate limiting."""
	for error in errors:
		error_lower = error.lower()
		if any(pattern in error_lower for pattern in RATE_LIMIT_PATTERNS):
			return True
	return False


def _activate_rate_limit_pause(conn) -> None:
	"""Set rate_limit_paused_until to 01:00 UTC next day."""
	tomorrow_start = (datetime.utcnow() + timedelta(days=1)).replace(
		hour=0, minute=0, second=0, microsecond=0
	)
	pause_until = tomorrow_start + timedelta(hours=1)

	conn.reload()
	conn.rate_limit_paused_until = pause_until
	conn.connection_status = "Rate Limited"
	conn.save(ignore_permissions=True)
	frappe.db.commit()

	notify_sync_failure(
		conn,
		_(
			"SimpleFIN rate limit warning detected. Syncs paused until {0}. "
			"The access token is safe — pausing prevents escalation to token disabling."
		).format(str(pause_until)),
	)


# ---------------------------------------------------------------------------
# Sync log creation
# ---------------------------------------------------------------------------

def _create_sync_log(conn, sync_type: str):
	"""Create an In Progress sync log."""
	log = frappe.get_doc({
		"doctype": "SimpleFIN Sync Log",
		"connection": conn.name,
		"sync_type": sync_type,
		"status": "In Progress",
		"started_at": now_datetime(),
	})
	log.insert(ignore_permissions=True)
	frappe.db.commit()
	return log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unix_to_date(timestamp: int, tz_name: str | None = None):
	"""Convert a UNIX timestamp to a Python date using the given timezone."""
	if tz_name:
		from zoneinfo import ZoneInfo

		try:
			tz = ZoneInfo(tz_name)
			return datetime.fromtimestamp(timestamp, tz=tz).date()
		except (KeyError, Exception):
			pass

	# Default: UTC (naive)
	return datetime.utcfromtimestamp(timestamp).date()
