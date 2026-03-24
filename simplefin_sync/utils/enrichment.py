# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""Transaction data enrichment — extract reference numbers and party names.

SimpleFIN provides a raw ``description`` string and an optional ``extra``
object.  This module extracts structured data to populate ERPNext's
``reference_number`` and ``bank_party_name`` fields, improving automatic
party matching and reconciliation accuracy.

All functions return ``None`` when extraction is disabled, no match is
found, or a regex is invalid — never force a bad value.
"""

from __future__ import annotations

import re

import frappe
from frappe import _


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def enrich_transaction(
	description: str,
	extra: dict | None,
	connection,
) -> dict:
	"""Extract reference_number and bank_party_name based on connection settings.

	Args:
		description: Raw transaction description from SimpleFIN.
		extra: Optional extra dict from SimpleFIN transaction.
		connection: The SimpleFIN Connection doc (or dict with the relevant fields).

	Returns:
		Dict with ``reference_number`` and ``bank_party_name`` keys.
		Values are ``None`` when extraction is disabled or yields no result.
	"""
	result: dict[str, str | None] = {"reference_number": None, "bank_party_name": None}

	if connection.get("extract_reference_number"):
		custom = connection.get("custom_reference_regex")
		if custom:
			result["reference_number"] = apply_custom_regex(custom, description)
		else:
			result["reference_number"] = extract_reference_number(description, extra)

	if connection.get("extract_party_name"):
		custom = connection.get("custom_party_regex")
		if custom:
			result["bank_party_name"] = apply_custom_regex(custom, description)
		else:
			result["bank_party_name"] = extract_party_name(description)

	return result


# ---------------------------------------------------------------------------
# Custom regex
# ---------------------------------------------------------------------------

def apply_custom_regex(pattern: str, description: str) -> str | None:
	"""Apply a user-provided regex pattern to extract a value.

	The pattern must contain exactly one capture group.
	Returns the captured group or ``None`` if no match.
	"""
	if not description or not pattern:
		return None
	try:
		match = re.search(pattern, description, re.IGNORECASE)
		if match and match.group(1):
			return match.group(1).strip()
	except re.error:
		frappe.logger(__name__).warning(
			f"Invalid custom regex pattern: {pattern}"
		)
	return None


def validate_custom_regex(pattern: str, field_label: str) -> None:
	"""Validate a custom regex pattern on SimpleFIN Connection save.

	Raises ``frappe.ValidationError`` if the pattern does not compile or
	does not have exactly one capture group.
	"""
	if not pattern:
		return
	try:
		compiled = re.compile(pattern)
	except re.error as e:
		frappe.throw(_("Invalid regex in {0}: {1}").format(field_label, str(e)))
	if compiled.groups != 1:
		frappe.throw(
			_("{0} must contain exactly one capture group. Found {1}.").format(
				field_label, compiled.groups
			)
		)


# ---------------------------------------------------------------------------
# Reference number extraction (built-in)
# ---------------------------------------------------------------------------

# Keys in the SimpleFIN ``extra`` object that may contain a reference number.
_EXTRA_REF_KEYS = (
	"check_number",
	"reference",
	"ref",
	"trace_number",
	"confirmation",
	"transaction_number",
	"reference_number",
)


def extract_reference_number(description: str, extra: dict | None) -> str | None:
	"""Extract a reference/check number from SimpleFIN transaction data.

	Priority:
	1. Structured data in the ``extra`` object.
	2. Check number pattern in description.
	3. Reference/confirmation pattern in description.
	4. ``None`` if no match.
	"""
	# 1. Check extra object for structured reference data
	if extra:
		for key in _EXTRA_REF_KEYS:
			if key in extra and extra[key]:
				return str(extra[key]).strip()

	if not description:
		return None

	# 2. Check number patterns
	check_match = re.search(
		r"(?:check|chk|ck)\s*#?\s*(\d{3,})",
		description,
		re.IGNORECASE,
	)
	if check_match:
		return check_match.group(1)

	# 3. Reference/confirmation patterns
	ref_match = re.search(
		r"(?:ref|conf|confirmation|trace|trans)\s*[#:]\s*([A-Za-z0-9]{4,})",
		description,
		re.IGNORECASE,
	)
	if ref_match:
		return ref_match.group(1)

	return None


# ---------------------------------------------------------------------------
# Party name extraction (built-in)
# ---------------------------------------------------------------------------

# Prefixes that precede the party name — stripped to isolate the party.
_DESCRIPTION_PREFIXES = [
	r"ACH (?:Payment|Deposit|Debit|Credit)\s*[-\u2013\u2014:]\s*",
	r"(?:POS|DEBIT CARD|CREDIT CARD)\s+(?:PURCHASE|PAYMENT|REFUND)\s*[-\u2013\u2014:]?\s*",
	r"Wire (?:Transfer|Payment)\s+(?:from|to)\s+",
	r"Direct (?:Deposit|Debit)\s*[-\u2013\u2014:]\s*",
	r"(?:ONLINE|MOBILE|BILL)\s+(?:PAYMENT|TRANSFER)\s*[-\u2013\u2014:]\s*",
	r"ZELLE\s+(?:TO|FROM)\s+",
	r"VENMO\s+(?:CASHOUT|PAYMENT)\s+",
]

# Suffixes to strip from the end (store numbers, ZIP codes, dates, trace IDs).
_DESCRIPTION_SUFFIXES = [
	r"\s+#\d+.*$",
	r"\s+\d{2}/\d{2}$",
	r"\s+[A-Z]{2}\s+\d{5}$",
	r"\s+\d{10,}$",
]

# Descriptions that contain no party information.
_NO_PARTY_PATTERNS = [
	r"^(?:Interest|Dividend|Fee|Charge|Overdraft|ATM|Tax)",
	r"^Check\s*#?\d+$",
	r"^(?:Service Charge|Monthly Maintenance|Wire Fee)",
]


def extract_party_name(description: str) -> str | None:
	"""Best-effort extraction of party name from a bank description.

	Returns ``None`` when no party information can be determined.
	"""
	if not description:
		return None

	# Skip descriptions with no party information
	for pattern in _NO_PARTY_PATTERNS:
		if re.match(pattern, description, re.IGNORECASE):
			return None

	result = description

	# Strip known prefixes
	for prefix in _DESCRIPTION_PREFIXES:
		result = re.sub(f"^{prefix}", "", result, flags=re.IGNORECASE)

	# Strip known suffixes
	for suffix in _DESCRIPTION_SUFFIXES:
		result = re.sub(suffix, "", result, flags=re.IGNORECASE)

	result = result.strip()

	# If nothing meaningful remains, return None
	if not result or len(result) < 3:
		return None

	# Title-case all-caps results for readability
	if result == result.upper() and len(result) > 3:
		result = result.title()

	return result
