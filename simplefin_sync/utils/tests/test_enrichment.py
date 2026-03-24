# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Unit tests for transaction data enrichment."""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from simplefin_sync.utils.enrichment import (
	apply_custom_regex,
	enrich_transaction,
	extract_party_name,
	extract_reference_number,
	validate_custom_regex,
)


# ---------------------------------------------------------------------------
# Reference number extraction
# ---------------------------------------------------------------------------

class TestExtractReferenceNumber(FrappeTestCase):
	"""Tests for extract_reference_number()."""

	def test_extra_check_number(self):
		self.assertEqual(
			extract_reference_number("Some description", {"check_number": "1042"}),
			"1042",
		)

	def test_extra_reference(self):
		self.assertEqual(
			extract_reference_number("Desc", {"reference": "REF123"}),
			"REF123",
		)

	def test_extra_trace_number(self):
		self.assertEqual(
			extract_reference_number("Desc", {"trace_number": "987654"}),
			"987654",
		)

	def test_extra_empty_value_skipped(self):
		"""Empty extra values should be skipped."""
		self.assertIsNone(
			extract_reference_number("Payment", {"check_number": "", "reference": ""})
		)

	def test_check_pattern_in_description(self):
		self.assertEqual(
			extract_reference_number("Check #1042", None),
			"1042",
		)

	def test_chk_pattern(self):
		self.assertEqual(
			extract_reference_number("CHK 5678", None),
			"5678",
		)

	def test_ck_pattern(self):
		self.assertEqual(
			extract_reference_number("CK#12345", None),
			"12345",
		)

	def test_ref_pattern(self):
		self.assertEqual(
			extract_reference_number("Payment Ref: ABC1234", None),
			"ABC1234",
		)

	def test_conf_pattern(self):
		self.assertEqual(
			extract_reference_number("Transfer Conf#99887766", None),
			"99887766",
		)

	def test_trace_pattern(self):
		self.assertEqual(
			extract_reference_number("Wire Trace: TR12345", None),
			"TR12345",
		)

	def test_no_match(self):
		"""No reference found returns None."""
		self.assertIsNone(
			extract_reference_number("ACH Payment - Vendor", None)
		)

	def test_none_description(self):
		self.assertIsNone(extract_reference_number(None, None))

	def test_empty_description(self):
		self.assertIsNone(extract_reference_number("", None))

	def test_extra_takes_priority_over_description(self):
		"""Extra data is checked before description patterns."""
		result = extract_reference_number(
			"Check #9999", {"check_number": "5555"}
		)
		self.assertEqual(result, "5555")


# ---------------------------------------------------------------------------
# Party name extraction
# ---------------------------------------------------------------------------

class TestExtractPartyName(FrappeTestCase):
	"""Tests for extract_party_name()."""

	def test_ach_payment(self):
		self.assertEqual(
			extract_party_name("ACH Payment - Acme Corporation"),
			"Acme Corporation",
		)

	def test_pos_purchase(self):
		result = extract_party_name("POS Purchase COSTCO #1234 SEATTLE WA")
		self.assertEqual(result, "Costco")

	def test_wire_transfer(self):
		self.assertEqual(
			extract_party_name("Wire Transfer from Jane Smith"),
			"Jane Smith",
		)

	def test_direct_deposit(self):
		result = extract_party_name("Direct Deposit - EMPLOYER INC")
		self.assertEqual(result, "Employer Inc")

	def test_all_caps_normalization(self):
		result = extract_party_name("ACH Payment - AMAZON.COM")
		self.assertEqual(result, "Amazon.Com")

	def test_no_party_check(self):
		"""Check-only descriptions return None."""
		self.assertIsNone(extract_party_name("Check #1042"))

	def test_no_party_interest(self):
		self.assertIsNone(extract_party_name("Interest Payment"))

	def test_no_party_fee(self):
		self.assertIsNone(extract_party_name("Fee Charge"))

	def test_no_party_service_charge(self):
		self.assertIsNone(extract_party_name("Service Charge"))

	def test_none_description(self):
		self.assertIsNone(extract_party_name(None))

	def test_empty_description(self):
		self.assertIsNone(extract_party_name(""))

	def test_short_result_returns_none(self):
		"""Results shorter than 3 chars return None."""
		self.assertIsNone(extract_party_name("ACH Payment - AB"))

	def test_mixed_case_preserved(self):
		"""Mixed-case results are NOT title-cased."""
		result = extract_party_name("ACH Payment - McDonald's")
		self.assertEqual(result, "McDonald's")


# ---------------------------------------------------------------------------
# Custom regex
# ---------------------------------------------------------------------------

class TestApplyCustomRegex(FrappeTestCase):
	"""Tests for apply_custom_regex()."""

	def test_valid_regex_matches(self):
		result = apply_custom_regex(r"REF:\s*(\w+)", "Payment REF: ABC123")
		self.assertEqual(result, "ABC123")

	def test_no_match_returns_none(self):
		self.assertIsNone(
			apply_custom_regex(r"REF:\s*(\w+)", "No reference here")
		)

	def test_invalid_regex_returns_none(self):
		"""Invalid regex pattern returns None (logged as warning)."""
		self.assertIsNone(
			apply_custom_regex(r"[invalid(", "Some text")
		)

	def test_empty_description(self):
		self.assertIsNone(apply_custom_regex(r"(\w+)", ""))

	def test_empty_pattern(self):
		self.assertIsNone(apply_custom_regex("", "Some text"))


class TestValidateCustomRegex(FrappeTestCase):
	"""Tests for validate_custom_regex()."""

	def test_valid_pattern(self):
		"""Valid pattern with one group passes silently."""
		validate_custom_regex(r"REF:\s*(\w+)", "Test Field")

	def test_empty_pattern_passes(self):
		"""Empty string is allowed (means 'use built-in')."""
		validate_custom_regex("", "Test Field")

	def test_invalid_pattern_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			validate_custom_regex(r"[invalid(", "Test Field")

	def test_no_capture_group_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			validate_custom_regex(r"\w+", "Test Field")

	def test_two_capture_groups_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			validate_custom_regex(r"(\w+)\s+(\w+)", "Test Field")


# ---------------------------------------------------------------------------
# Enrichment dispatch
# ---------------------------------------------------------------------------

class TestEnrichTransaction(FrappeTestCase):
	"""Tests for enrich_transaction() dispatch logic."""

	def test_both_enabled(self):
		conn = frappe._dict({
			"extract_reference_number": 1,
			"extract_party_name": 1,
			"custom_reference_regex": "",
			"custom_party_regex": "",
		})
		result = enrich_transaction(
			"ACH Payment - Vendor Check #1042", None, conn
		)
		self.assertEqual(result["reference_number"], "1042")
		self.assertEqual(result["bank_party_name"], "Vendor Check")

	def test_reference_disabled(self):
		conn = frappe._dict({
			"extract_reference_number": 0,
			"extract_party_name": 1,
			"custom_reference_regex": "",
			"custom_party_regex": "",
		})
		result = enrich_transaction(
			"ACH Payment - Vendor Check #1042", None, conn
		)
		self.assertIsNone(result["reference_number"])
		self.assertIsNotNone(result["bank_party_name"])

	def test_party_disabled(self):
		conn = frappe._dict({
			"extract_reference_number": 1,
			"extract_party_name": 0,
			"custom_reference_regex": "",
			"custom_party_regex": "",
		})
		result = enrich_transaction(
			"ACH Payment - Vendor Check #1042", None, conn
		)
		self.assertIsNotNone(result["reference_number"])
		self.assertIsNone(result["bank_party_name"])

	def test_both_disabled(self):
		conn = frappe._dict({
			"extract_reference_number": 0,
			"extract_party_name": 0,
			"custom_reference_regex": "",
			"custom_party_regex": "",
		})
		result = enrich_transaction("Check #1042", None, conn)
		self.assertIsNone(result["reference_number"])
		self.assertIsNone(result["bank_party_name"])

	def test_custom_regex_overrides_builtin(self):
		conn = frappe._dict({
			"extract_reference_number": 1,
			"extract_party_name": 0,
			"custom_reference_regex": r"CUSTOM-(\d+)",
			"custom_party_regex": "",
		})
		result = enrich_transaction("Payment CUSTOM-9999", None, conn)
		self.assertEqual(result["reference_number"], "9999")
