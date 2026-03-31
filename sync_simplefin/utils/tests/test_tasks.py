# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Unit tests for the scheduler (tasks.py)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sync_simplefin.tasks import (
	_evaluate_connection,
	_parse_time,
	is_regular_interval_due,
)


def _make_conn(**kwargs) -> frappe._dict:
	"""Build a minimal mock connection dict."""
	defaults = {
		"name": "SFIN-TEST",
		"sync_state": "Idle",
		"sync_frequency": "Daily",
		"sync_time": "02:00",
		"sync_day_of_week": "Monday",
		"sync_day_of_month": 1,
		"last_sync_attempt": None,
		"retry_count": 3,
		"retry_attempts_used": 0,
		"retry_interval_minutes": 30,
		"next_retry_at": None,
		"rate_limit_paused_until": None,
	}
	defaults.update(kwargs)
	return frappe._dict(defaults)


# ---------------------------------------------------------------------------
# is_regular_interval_due tests
# ---------------------------------------------------------------------------

class TestIsRegularIntervalDue(FrappeTestCase):
	"""Tests for is_regular_interval_due() with all frequency types."""

	# --- Sub-daily (interval-based) ---

	def test_every_2_hours_never_synced(self):
		"""Never synced → due immediately."""
		conn = _make_conn(sync_frequency="Every 2 Hours", last_sync_attempt=None)
		self.assertTrue(is_regular_interval_due(conn))

	def test_every_2_hours_elapsed(self):
		"""121 minutes since last sync → due."""
		now = datetime(2026, 3, 24, 14, 0)
		last = now - timedelta(minutes=121)
		conn = _make_conn(sync_frequency="Every 2 Hours", last_sync_attempt=last)
		self.assertTrue(is_regular_interval_due(conn, now))

	def test_every_2_hours_not_elapsed(self):
		"""60 minutes since last sync → not due."""
		now = datetime(2026, 3, 24, 14, 0)
		last = now - timedelta(minutes=60)
		conn = _make_conn(sync_frequency="Every 2 Hours", last_sync_attempt=last)
		self.assertFalse(is_regular_interval_due(conn, now))

	def test_4x_daily_elapsed(self):
		now = datetime(2026, 3, 24, 14, 0)
		last = now - timedelta(minutes=361)
		conn = _make_conn(sync_frequency="4x Daily", last_sync_attempt=last)
		self.assertTrue(is_regular_interval_due(conn, now))

	def test_twice_daily_not_elapsed(self):
		now = datetime(2026, 3, 24, 14, 0)
		last = now - timedelta(minutes=600)
		conn = _make_conn(sync_frequency="Twice Daily", last_sync_attempt=last)
		self.assertFalse(is_regular_interval_due(conn, now))

	# --- Daily ---

	def test_daily_due_at_sync_time(self):
		"""After sync_time with no sync today → due."""
		now = datetime(2026, 3, 24, 3, 0)  # 03:00
		conn = _make_conn(
			sync_frequency="Daily", sync_time="02:00",
			last_sync_attempt=datetime(2026, 3, 23, 2, 5),  # yesterday
		)
		self.assertTrue(is_regular_interval_due(conn, now))

	def test_daily_before_sync_time(self):
		"""Before sync_time → not due."""
		now = datetime(2026, 3, 24, 1, 30)  # 01:30
		conn = _make_conn(sync_frequency="Daily", sync_time="02:00")
		self.assertFalse(is_regular_interval_due(conn, now))

	def test_daily_already_synced_today(self):
		"""Already synced today → not due."""
		now = datetime(2026, 3, 24, 14, 0)
		conn = _make_conn(
			sync_frequency="Daily", sync_time="02:00",
			last_sync_attempt=datetime(2026, 3, 24, 2, 5),  # today
		)
		self.assertFalse(is_regular_interval_due(conn, now))

	def test_daily_never_synced(self):
		"""Never synced + past sync_time → due."""
		now = datetime(2026, 3, 24, 14, 0)
		conn = _make_conn(
			sync_frequency="Daily", sync_time="02:00",
			last_sync_attempt=None,
		)
		self.assertTrue(is_regular_interval_due(conn, now))

	# --- Weekly ---

	def test_weekly_due_on_correct_day(self):
		"""Correct day + past sync_time + no sync this week → due."""
		# 2026-03-23 is a Monday
		now = datetime(2026, 3, 23, 3, 0)
		conn = _make_conn(
			sync_frequency="Weekly", sync_time="02:00",
			sync_day_of_week="Monday",
			last_sync_attempt=datetime(2026, 3, 16, 2, 5),  # last week
		)
		self.assertTrue(is_regular_interval_due(conn, now))

	def test_weekly_wrong_day(self):
		"""Wrong day of week → not due."""
		# 2026-03-24 is a Tuesday
		now = datetime(2026, 3, 24, 3, 0)
		conn = _make_conn(
			sync_frequency="Weekly", sync_time="02:00",
			sync_day_of_week="Monday",
		)
		self.assertFalse(is_regular_interval_due(conn, now))

	def test_weekly_already_synced_this_week(self):
		"""Already synced this week → not due."""
		now = datetime(2026, 3, 23, 14, 0)  # Monday
		conn = _make_conn(
			sync_frequency="Weekly", sync_time="02:00",
			sync_day_of_week="Monday",
			last_sync_attempt=datetime(2026, 3, 23, 2, 5),  # earlier today
		)
		self.assertFalse(is_regular_interval_due(conn, now))

	# --- Bi-Weekly ---

	def test_biweekly_due_after_14_days(self):
		now = datetime(2026, 3, 23, 3, 0)  # Monday
		conn = _make_conn(
			sync_frequency="Bi-Weekly", sync_time="02:00",
			sync_day_of_week="Monday",
			last_sync_attempt=datetime(2026, 3, 9, 2, 5),  # 14 days ago
		)
		self.assertTrue(is_regular_interval_due(conn, now))

	def test_biweekly_not_due_under_14_days(self):
		now = datetime(2026, 3, 23, 3, 0)  # Monday
		conn = _make_conn(
			sync_frequency="Bi-Weekly", sync_time="02:00",
			sync_day_of_week="Monday",
			last_sync_attempt=datetime(2026, 3, 16, 2, 5),  # 7 days ago
		)
		self.assertFalse(is_regular_interval_due(conn, now))

	# --- Monthly ---

	def test_monthly_due_on_correct_day(self):
		now = datetime(2026, 3, 15, 3, 0)
		conn = _make_conn(
			sync_frequency="Monthly", sync_time="02:00",
			sync_day_of_month=15,
			last_sync_attempt=datetime(2026, 2, 15, 2, 5),  # last month
		)
		self.assertTrue(is_regular_interval_due(conn, now))

	def test_monthly_wrong_day(self):
		now = datetime(2026, 3, 14, 3, 0)
		conn = _make_conn(
			sync_frequency="Monthly", sync_time="02:00",
			sync_day_of_month=15,
		)
		self.assertFalse(is_regular_interval_due(conn, now))

	def test_monthly_already_synced_this_month(self):
		now = datetime(2026, 3, 15, 14, 0)
		conn = _make_conn(
			sync_frequency="Monthly", sync_time="02:00",
			sync_day_of_month=15,
			last_sync_attempt=datetime(2026, 3, 15, 2, 5),
		)
		self.assertFalse(is_regular_interval_due(conn, now))


# ---------------------------------------------------------------------------
# _evaluate_connection tests
# ---------------------------------------------------------------------------

class TestEvaluateConnection(FrappeTestCase):
	"""Tests for _evaluate_connection() state transitions."""

	@patch("sync_simplefin.tasks._enqueue_sync")
	def test_rate_limited_connection_skipped(self, mock_enqueue):
		"""Active rate limit pause → skip entirely."""
		now = datetime(2026, 3, 24, 12, 0)
		conn = _make_conn(
			sync_state="Idle",
			rate_limit_paused_until=datetime(2026, 3, 25, 1, 0),  # tomorrow
			last_sync_attempt=datetime(2026, 3, 20, 2, 0),
		)
		_evaluate_connection(conn, now)
		mock_enqueue.assert_not_called()

	@patch("sync_simplefin.tasks.frappe")
	@patch("sync_simplefin.tasks._enqueue_sync")
	def test_expired_rate_limit_cleared(self, mock_enqueue, mock_frappe):
		"""Expired rate limit pause → clear and proceed."""
		now = datetime(2026, 3, 25, 2, 0)
		conn = _make_conn(
			sync_state="Idle",
			sync_frequency="Daily",
			sync_time="02:00",
			rate_limit_paused_until=datetime(2026, 3, 25, 1, 0),  # expired
			last_sync_attempt=datetime(2026, 3, 23, 2, 0),
		)
		_evaluate_connection(conn, now)
		# Should have cleared the pause
		mock_frappe.db.set_value.assert_any_call(
			"SimpleFIN Connection", "SFIN-TEST",
			{"rate_limit_paused_until": None},
		)

	@patch("sync_simplefin.tasks.frappe")
	@patch("sync_simplefin.tasks._enqueue_sync")
	def test_stale_syncing_state_recovered(self, mock_enqueue, mock_frappe):
		"""Syncing for >30 min → recovered to Failed."""
		now = datetime(2026, 3, 24, 14, 0)
		conn = _make_conn(
			sync_state="Syncing",
			last_sync_attempt=datetime(2026, 3, 24, 13, 0),  # 60 min ago
			rate_limit_paused_until=None,
		)
		_evaluate_connection(conn, now)
		mock_frappe.db.set_value.assert_called_once()
		call_args = mock_frappe.db.set_value.call_args
		self.assertEqual(call_args[0][2]["sync_state"], "Failed")

	@patch("sync_simplefin.tasks._enqueue_sync")
	def test_syncing_state_not_stale_skipped(self, mock_enqueue):
		"""Syncing for <30 min → skip (job still running)."""
		now = datetime(2026, 3, 24, 14, 0)
		conn = _make_conn(
			sync_state="Syncing",
			last_sync_attempt=datetime(2026, 3, 24, 13, 45),  # 15 min ago
			rate_limit_paused_until=None,
		)
		_evaluate_connection(conn, now)
		mock_enqueue.assert_not_called()

	@patch("sync_simplefin.tasks._enqueue_sync")
	def test_retry_pending_fires_on_interval(self, mock_enqueue):
		"""Retry Pending + retry interval elapsed → enqueue without reset."""
		now = datetime(2026, 3, 24, 14, 0)
		conn = _make_conn(
			sync_state="Retry Pending",
			sync_frequency="Daily",
			sync_time="02:00",
			last_sync_attempt=datetime(2026, 3, 24, 13, 0),
			next_retry_at=datetime(2026, 3, 24, 13, 30),  # 30 min ago
			rate_limit_paused_until=None,
		)
		_evaluate_connection(conn, now)
		mock_enqueue.assert_called_once_with(conn, reset_retries=False)

	@patch("sync_simplefin.tasks._enqueue_sync")
	def test_retry_pending_regular_interval_takes_priority(self, mock_enqueue):
		"""Retry Pending + regular interval due → enqueue WITH reset."""
		# Daily sync at 02:00, last sync was yesterday, now it's 03:00
		now = datetime(2026, 3, 24, 3, 0)
		conn = _make_conn(
			sync_state="Retry Pending",
			sync_frequency="Daily",
			sync_time="02:00",
			last_sync_attempt=datetime(2026, 3, 23, 14, 0),  # yesterday
			next_retry_at=datetime(2026, 3, 24, 14, 30),  # future
			rate_limit_paused_until=None,
		)
		_evaluate_connection(conn, now)
		mock_enqueue.assert_called_once_with(conn, reset_retries=True)

	@patch("sync_simplefin.tasks._enqueue_sync")
	def test_idle_due_enqueues(self, mock_enqueue):
		"""Idle + interval due → enqueue with reset."""
		now = datetime(2026, 3, 24, 3, 0)
		conn = _make_conn(
			sync_state="Idle",
			sync_frequency="Daily",
			sync_time="02:00",
			last_sync_attempt=datetime(2026, 3, 23, 2, 5),
			rate_limit_paused_until=None,
		)
		_evaluate_connection(conn, now)
		mock_enqueue.assert_called_once_with(conn, reset_retries=True)

	@patch("sync_simplefin.tasks._enqueue_sync")
	def test_idle_not_due_skipped(self, mock_enqueue):
		"""Idle + not due → no action."""
		now = datetime(2026, 3, 24, 1, 0)  # before sync_time
		conn = _make_conn(
			sync_state="Idle",
			sync_frequency="Daily",
			sync_time="02:00",
			rate_limit_paused_until=None,
		)
		_evaluate_connection(conn, now)
		mock_enqueue.assert_not_called()

	@patch("sync_simplefin.tasks._enqueue_sync")
	def test_failed_state_waits_for_interval(self, mock_enqueue):
		"""Failed + interval not due → no action."""
		now = datetime(2026, 3, 24, 1, 0)
		conn = _make_conn(
			sync_state="Failed",
			sync_frequency="Daily",
			sync_time="02:00",
			last_sync_attempt=datetime(2026, 3, 23, 2, 5),
			rate_limit_paused_until=None,
		)
		_evaluate_connection(conn, now)
		mock_enqueue.assert_not_called()

	@patch("sync_simplefin.tasks._enqueue_sync")
	def test_failed_state_resets_on_interval(self, mock_enqueue):
		"""Failed + interval due → enqueue with reset."""
		now = datetime(2026, 3, 24, 3, 0)
		conn = _make_conn(
			sync_state="Failed",
			sync_frequency="Daily",
			sync_time="02:00",
			last_sync_attempt=datetime(2026, 3, 23, 2, 5),
			rate_limit_paused_until=None,
		)
		_evaluate_connection(conn, now)
		mock_enqueue.assert_called_once_with(conn, reset_retries=True)


# ---------------------------------------------------------------------------
# _parse_time tests
# ---------------------------------------------------------------------------

class TestParseTime(FrappeTestCase):
	"""Tests for _parse_time()."""

	def test_hh_mm_string(self):
		self.assertEqual(_parse_time("14:30"), (14, 30))

	def test_hh_mm_ss_string(self):
		self.assertEqual(_parse_time("02:00:00"), (2, 0))

	def test_none_defaults_to_0200(self):
		self.assertEqual(_parse_time(None), (2, 0))

	def test_timedelta(self):
		"""Frappe sometimes stores Time fields as timedelta."""
		from datetime import timedelta as td
		self.assertEqual(_parse_time(td(hours=3, minutes=15)), (3, 15))

	def test_empty_string(self):
		self.assertEqual(_parse_time(""), (2, 0))
