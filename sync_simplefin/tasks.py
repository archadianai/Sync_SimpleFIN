# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Scheduled task handlers for SimpleFIN Sync.

``check_due_syncs`` runs every ~60 seconds via the Frappe ``all`` scheduler
event.  ``cleanup_old_sync_logs`` runs daily.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import now_datetime, get_datetime

# Map sync_frequency → minutes for interval-based frequencies.
FREQUENCY_MINUTES = {
	"Every 2 Hours": 120,
	"4x Daily": 360,
	"Twice Daily": 720,
}

# Stale state threshold — if a connection has been Syncing/Queued for longer
# than this, assume the worker crashed and reset to Failed.
STALE_THRESHOLD_SECONDS = 1800  # 30 minutes


# ---------------------------------------------------------------------------
# Scheduler entry points
# ---------------------------------------------------------------------------

def check_due_syncs() -> None:
	"""Evaluate all enabled, registered connections and enqueue syncs that are due.

	Runs every ~60 s via Frappe's ``all`` scheduler event.  Each connection is
	evaluated in isolation — an exception on one connection never blocks others.
	"""
	connections = frappe.get_all(
		"SimpleFIN Connection",
		filters={"enabled": 1, "is_registered": 1},
		fields=[
			"name", "sync_state", "sync_frequency", "sync_time",
			"sync_day_of_week", "sync_day_of_month",
			"last_sync_attempt", "retry_count", "retry_attempts_used",
			"retry_interval_minutes", "next_retry_at",
			"rate_limit_paused_until",
		],
	)

	now = now_datetime()

	for conn in connections:
		try:
			_evaluate_connection(conn, now)
		except Exception:
			frappe.logger(__name__).error(
				f"SimpleFIN Connection {conn.name}: scheduler evaluation failed",
				exc_info=True,
			)


def cleanup_old_sync_logs() -> None:
	"""Delete SimpleFIN Sync Log records older than the configured retention period."""
	retention_days = (
		frappe.db.get_single_value("SimpleFIN Sync Settings", "log_retention_days")
		or 90
	)

	cutoff = now_datetime() - timedelta(days=int(retention_days))

	old_logs = frappe.get_all(
		"SimpleFIN Sync Log",
		filters={"started_at": ["<", cutoff]},
		pluck="name",
	)

	for log_name in old_logs:
		frappe.delete_doc("SimpleFIN Sync Log", log_name, force=True)

	if old_logs:
		frappe.db.commit()  # nosemgrep: frappe-manual-commit -- persist sync-log deletions in scheduler context


# ---------------------------------------------------------------------------
# Connection evaluation
# ---------------------------------------------------------------------------

def _evaluate_connection(conn, now) -> None:
	"""Evaluate a single connection and decide whether to enqueue a sync."""
	# RATE LIMIT GUARD
	if conn.rate_limit_paused_until:
		paused_until = get_datetime(conn.rate_limit_paused_until)
		if now < paused_until:
			return  # Still paused — skip entirely
		# Pause expired — clear it (auto-resume)
		frappe.db.set_value(
			"SimpleFIN Connection", conn.name,
			{"rate_limit_paused_until": None},
		)

	# CONCURRENCY / STALE STATE GUARD
	if conn.sync_state in ("Syncing", "Queued"):
		if conn.last_sync_attempt:
			elapsed = (now - get_datetime(conn.last_sync_attempt)).total_seconds()
			if elapsed > STALE_THRESHOLD_SECONDS:
				frappe.db.set_value(
					"SimpleFIN Connection", conn.name,
					{
						"sync_state": "Failed",
						"last_sync_status": "Failed",
						"last_sync_error": "Sync job appears to have crashed (stale state recovery)",
					},
				)
				frappe.logger(__name__).warning(
					f"SimpleFIN Connection {conn.name}: stale sync state recovered"
				)
		return  # Job in flight (or just recovered) — skip this tick

	# RETRY PENDING
	if conn.sync_state == "Retry Pending":
		if is_regular_interval_due(conn, now):
			# Regular interval takes priority — reset retry cycle
			_enqueue_sync(conn, reset_retries=True)
		elif conn.next_retry_at and now >= get_datetime(conn.next_retry_at):
			# Retry interval elapsed
			_enqueue_sync(conn, reset_retries=False)
		return

	# IDLE or FAILED
	if conn.sync_state in ("Idle", "Failed"):
		if is_regular_interval_due(conn, now):
			_enqueue_sync(conn, reset_retries=True)


# ---------------------------------------------------------------------------
# Frequency / interval logic
# ---------------------------------------------------------------------------

def is_regular_interval_due(conn, now=None) -> bool:
	"""Determine whether the regular sync interval has arrived.

	Sub-daily frequencies (Every 2 Hours, 4x Daily, Twice Daily) are purely
	interval-based — they fire when enough time has elapsed since the last
	attempt, regardless of clock time.

	Daily and longer frequencies are clock-based — they fire at a specific
	time on a specific day.
	"""
	if now is None:
		now = now_datetime()

	freq = conn.get("sync_frequency") if hasattr(conn, "get") else conn.sync_frequency
	last = conn.get("last_sync_attempt") if hasattr(conn, "get") else conn.last_sync_attempt

	# --- Sub-daily (interval-based) ---
	if freq in FREQUENCY_MINUTES:
		if not last:
			return True  # Never synced — due immediately
		interval = timedelta(minutes=FREQUENCY_MINUTES[freq])
		return now >= get_datetime(last) + interval

	# --- Daily and longer (clock-based) ---
	sync_time = conn.get("sync_time") if hasattr(conn, "get") else conn.sync_time
	sync_day_of_week = conn.get("sync_day_of_week") if hasattr(conn, "get") else conn.sync_day_of_week
	sync_day_of_month = conn.get("sync_day_of_month") if hasattr(conn, "get") else conn.sync_day_of_month

	# Parse sync_time (HH:MM or HH:MM:SS string) into hour/minute
	target_hour, target_minute = _parse_time(sync_time)

	if freq == "Daily":
		return _is_daily_due(now, last, target_hour, target_minute)

	if freq == "Weekly":
		return _is_weekly_due(now, last, target_hour, target_minute, sync_day_of_week)

	if freq == "Bi-Weekly":
		return _is_biweekly_due(now, last, target_hour, target_minute, sync_day_of_week)

	if freq == "Monthly":
		return _is_monthly_due(now, last, target_hour, target_minute, sync_day_of_month)

	return False


def _is_daily_due(now, last, hour: int, minute: int) -> bool:
	"""Current time >= sync_time AND no sync today."""
	if now.hour < hour or (now.hour == hour and now.minute < minute):
		return False
	if last:
		last_dt = get_datetime(last)
		if last_dt.date() == now.date():
			return False
	return True


def _is_weekly_due(now, last, hour: int, minute: int, day_of_week: str) -> bool:
	"""Current day = sync_day_of_week AND time >= sync_time AND no sync this week."""
	if _day_name(now) != day_of_week:
		return False
	if now.hour < hour or (now.hour == hour and now.minute < minute):
		return False
	if last:
		last_dt = get_datetime(last)
		# Same ISO week?
		if last_dt.isocalendar()[1] == now.isocalendar()[1] and last_dt.year == now.year:
			return False
	return True


def _is_biweekly_due(now, last, hour: int, minute: int, day_of_week: str) -> bool:
	"""Current day = sync_day_of_week AND time >= sync_time AND >= 14 days since last."""
	if _day_name(now) != day_of_week:
		return False
	if now.hour < hour or (now.hour == hour and now.minute < minute):
		return False
	if last:
		last_dt = get_datetime(last)
		if (now - last_dt).days < 14:
			return False
	return True


def _is_monthly_due(now, last, hour: int, minute: int, day_of_month: int) -> bool:
	"""Current day = sync_day_of_month AND time >= sync_time AND no sync this month."""
	if now.day != (day_of_month or 1):
		return False
	if now.hour < hour or (now.hour == hour and now.minute < minute):
		return False
	if last:
		last_dt = get_datetime(last)
		if last_dt.month == now.month and last_dt.year == now.year:
			return False
	return True


# ---------------------------------------------------------------------------
# Enqueue helper
# ---------------------------------------------------------------------------

def _enqueue_sync(conn, reset_retries: bool) -> None:
	"""Transition to Queued and enqueue the background sync job."""
	update_fields = {"sync_state": "Queued"}
	if reset_retries:
		update_fields["retry_attempts_used"] = 0
		update_fields["next_retry_at"] = None

	frappe.db.set_value("SimpleFIN Connection", conn.name, update_fields)

	frappe.enqueue(
		"sync_simplefin.utils.sync.run_sync",
		connection=conn.name,
		sync_type="Scheduled",
		queue="long",
		deduplicate=True,
		job_id=f"sync_simplefin_{conn.name}",
		timeout=600,
	)
	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- background sync worker must observe Queued state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _day_name(dt) -> str:
	"""Return the English day name for a datetime."""
	return _DAY_NAMES[dt.weekday()]


def _parse_time(time_val) -> tuple[int, int]:
	"""Parse a time value (string, timedelta, or None) into (hour, minute)."""
	if not time_val:
		return 2, 0  # Default: 02:00

	if isinstance(time_val, timedelta):
		total_seconds = int(time_val.total_seconds())
		return total_seconds // 3600, (total_seconds % 3600) // 60

	time_str = str(time_val)
	parts = time_str.split(":")
	try:
		return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
	except (ValueError, IndexError):
		return 2, 0
