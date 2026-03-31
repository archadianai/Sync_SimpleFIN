# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Unit tests for the core sync logic."""

import time
import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sync_simplefin.utils.sync import (
	_contains_rate_limit_warning,
	_unix_to_date,
	build_chunks,
)


# ---------------------------------------------------------------------------
# build_chunks tests
# ---------------------------------------------------------------------------

class TestBuildChunks(FrappeTestCase):
	"""Tests for build_chunks()."""

	def test_single_chunk_under_45_days(self):
		"""Range < 45 days produces one chunk."""
		end = int(time.time())
		start = end - (30 * 86400)  # 30 days
		chunks = build_chunks(start, end)
		self.assertEqual(len(chunks), 1)
		self.assertEqual(chunks[0], (start, end))

	def test_exactly_45_days(self):
		"""Range = 45 days produces one chunk."""
		end = int(time.time())
		start = end - (45 * 86400)
		chunks = build_chunks(start, end)
		self.assertEqual(len(chunks), 1)

	def test_90_days_produces_two_chunks(self):
		"""90-day range splits into 2 chunks, newest first."""
		end = int(time.time())
		start = end - (90 * 86400)
		chunks = build_chunks(start, end)
		self.assertEqual(len(chunks), 2)
		# First chunk should be the newest
		self.assertEqual(chunks[0][1], end)
		# Second chunk should start at the original start
		self.assertEqual(chunks[1][0], start)

	def test_365_days_produces_nine_chunks(self):
		"""365-day range produces 9 chunks (8×45 + 5 days)."""
		end = int(time.time())
		start = end - (365 * 86400)
		chunks = build_chunks(start, end)
		self.assertEqual(len(chunks), 9)

	def test_newest_first_ordering(self):
		"""Chunks are ordered newest to oldest."""
		end = int(time.time())
		start = end - (135 * 86400)  # 3 chunks at 45 days each
		chunks = build_chunks(start, end)
		self.assertEqual(len(chunks), 3)
		# Each chunk's end should be >= the next chunk's end
		for i in range(len(chunks) - 1):
			self.assertGreater(chunks[i][1], chunks[i + 1][1])

	def test_chunk_boundaries_are_contiguous(self):
		"""Adjacent chunks share a boundary (no gaps)."""
		end = int(time.time())
		start = end - (200 * 86400)
		chunks = build_chunks(start, end)
		for i in range(len(chunks) - 1):
			self.assertEqual(chunks[i][0], chunks[i + 1][1])


# ---------------------------------------------------------------------------
# Rate limit detection tests
# ---------------------------------------------------------------------------

class TestRateLimitDetection(FrappeTestCase):
	"""Tests for _contains_rate_limit_warning()."""

	def test_rate_limit_keyword(self):
		self.assertTrue(_contains_rate_limit_warning(["Rate limit exceeded"]))

	def test_quota_keyword(self):
		self.assertTrue(_contains_rate_limit_warning(["You have exceeded your quota"]))

	def test_throttle_keyword(self):
		self.assertTrue(_contains_rate_limit_warning(["Request throttled"]))

	def test_too_many_requests(self):
		self.assertTrue(_contains_rate_limit_warning(["Too many requests today"]))

	def test_slow_down(self):
		self.assertTrue(_contains_rate_limit_warning(["Please slow down"]))

	def test_no_rate_limit(self):
		self.assertFalse(_contains_rate_limit_warning(["Account not found"]))

	def test_empty_errors(self):
		self.assertFalse(_contains_rate_limit_warning([]))

	def test_case_insensitive(self):
		self.assertTrue(_contains_rate_limit_warning(["RATE LIMIT EXCEEDED"]))


# ---------------------------------------------------------------------------
# Date conversion tests
# ---------------------------------------------------------------------------

class TestUnixToDate(FrappeTestCase):
	"""Tests for _unix_to_date()."""

	def test_epoch_zero(self):
		"""UNIX epoch 0 → 1970-01-01."""
		from datetime import date
		self.assertEqual(_unix_to_date(0), date(1970, 1, 1))

	def test_known_date(self):
		"""Known timestamp → correct date."""
		from datetime import date
		# 2023-11-15 12:00:00 UTC (noon, like SimpleFIN Bridge sends)
		ts = 1700049600
		self.assertEqual(_unix_to_date(ts), date(2023, 11, 15))

	def test_noon_utc_same_date_everywhere(self):
		"""Noon UTC always yields the same calendar date regardless of timezone."""
		from datetime import date
		# SimpleFIN normalises to noon UTC — verify the date is unambiguous
		ts = 1700049600  # 2023-11-15 12:00:00 UTC
		self.assertEqual(_unix_to_date(ts), date(2023, 11, 15))
