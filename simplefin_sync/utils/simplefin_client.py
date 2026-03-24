# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

"""HTTP client for the SimpleFIN Bridge API.

All methods:
- Enforce HTTPS
- Verify SSL certificates
- Set 30-second timeouts
- Return parsed JSON or raise typed exceptions
- Never log credentials
"""

from __future__ import annotations

import base64
from urllib.parse import urlparse

import requests

import frappe
from frappe import _


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SimpleFINError(Exception):
	"""Base exception for SimpleFIN client errors."""


class SimpleFINAuthError(SimpleFINError):
	"""HTTP 403 — token already claimed, revoked, or compromised."""


class SimpleFINPaymentRequired(SimpleFINError):
	"""HTTP 402 — SimpleFIN account requires payment."""


class SimpleFINNetworkError(SimpleFINError):
	"""Network-level failure (timeout, DNS, connection refused, etc.)."""


class SimpleFINHTTPError(SimpleFINError):
	"""Unexpected HTTP status code."""

	def __init__(self, status_code: int, body: str = "") -> None:
		self.status_code = status_code
		self.body = body
		super().__init__(f"HTTP {status_code}")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT = 30  # seconds


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SimpleFINClient:
	"""HTTP client for SimpleFIN Bridge API."""

	def __init__(self, access_url: str) -> None:
		"""Parse *access_url* into base_url + Basic Auth credentials.

		Args:
			access_url: Full URL in the form ``https://user:pass@host/path``.

		Raises:
			SimpleFINError: If the URL is not HTTPS or cannot be parsed.
		"""
		_enforce_https(access_url)
		parsed = urlparse(access_url)

		if not parsed.username or not parsed.password:
			raise SimpleFINError(_("Access URL does not contain credentials."))

		# Rebuild base URL without embedded credentials.
		self._base_url = f"{parsed.scheme}://{parsed.hostname}"
		if parsed.port:
			self._base_url += f":{parsed.port}"
		self._base_url += parsed.path.rstrip("/")

		self._auth = (parsed.username, parsed.password)

	# -- public API ---------------------------------------------------------

	@staticmethod
	def claim_access_url(setup_token: str) -> str:
		"""Exchange a one-time setup token for a persistent access URL.

		Args:
			setup_token: Base64-encoded claim URL from SimpleFIN Bridge.

		Returns:
			The access URL string (contains embedded Basic Auth credentials).

		Raises:
			SimpleFINAuthError: 403 — token already claimed or compromised.
			SimpleFINNetworkError: Connection / timeout failure.
			SimpleFINError: Any other unexpected failure.
		"""
		try:
			claim_url = base64.b64decode(setup_token.strip()).decode("utf-8")
		except Exception as exc:
			raise SimpleFINError(
				_("Failed to decode setup token: {0}").format(str(exc))
			)

		_enforce_https(claim_url)

		try:
			resp = requests.post(claim_url, timeout=REQUEST_TIMEOUT, verify=True)
		except requests.exceptions.Timeout:
			raise SimpleFINNetworkError(_("Connection timed out while claiming token."))
		except requests.exceptions.ConnectionError as exc:
			raise SimpleFINNetworkError(
				_("Network error while claiming token: {0}").format(str(exc))
			)

		if resp.status_code == 403:
			raise SimpleFINAuthError(
				_(
					"This setup token has already been used or is compromised. "
					"Please generate a new token from SimpleFIN Bridge."
				)
			)

		if resp.status_code != 200:
			raise SimpleFINHTTPError(resp.status_code, resp.text)

		access_url = resp.text.strip()
		if not access_url:
			raise SimpleFINError(_("SimpleFIN returned an empty access URL."))

		_enforce_https(access_url)
		return access_url

	def get_accounts(
		self,
		start_date: int | None = None,
		end_date: int | None = None,
		account_ids: list[str] | None = None,
		include_pending: bool = False,
		balances_only: bool = False,
	) -> dict:
		"""Fetch account data from SimpleFIN Bridge.

		Args:
			start_date: UNIX timestamp — transactions on or after this time.
			end_date: UNIX timestamp — transactions before this time.
			account_ids: Limit to these SimpleFIN account IDs.
			include_pending: Include pending transactions.
			balances_only: Return only balances, no transactions.

		Returns:
			Parsed JSON response dict with ``errors`` and ``accounts`` keys.

		Raises:
			SimpleFINAuthError: 403 — access revoked.
			SimpleFINPaymentRequired: 402 — payment issue.
			SimpleFINNetworkError: Timeout / connection failure.
			SimpleFINHTTPError: Other HTTP errors.
		"""
		url = f"{self._base_url}/accounts"
		params: dict[str, str] = {}

		if start_date is not None:
			params["start-date"] = str(int(start_date))
		if end_date is not None:
			params["end-date"] = str(int(end_date))
		if include_pending:
			params["pending"] = "1"
		if balances_only:
			params["balances-only"] = "1"

		# account IDs are repeatable query params
		if account_ids:
			# requests handles list values as repeated params with the same key
			# but SimpleFIN expects ``&account=id1&account=id2``
			params_list: list[tuple[str, str]] = list(params.items())
			for aid in account_ids:
				params_list.append(("account", aid))
		else:
			params_list = list(params.items())

		try:
			resp = requests.get(
				url,
				params=params_list,
				auth=self._auth,
				timeout=REQUEST_TIMEOUT,
				verify=True,
			)
		except requests.exceptions.Timeout:
			raise SimpleFINNetworkError(
				_("Connection timed out while fetching accounts.")
			)
		except requests.exceptions.ConnectionError as exc:
			raise SimpleFINNetworkError(
				_("Network error while fetching accounts: {0}").format(str(exc))
			)

		if resp.status_code == 403:
			raise SimpleFINAuthError(
				_("Access has been revoked. Please re-register with a new token.")
			)

		if resp.status_code == 402:
			raise SimpleFINPaymentRequired(
				_("SimpleFIN account requires payment. Please check your SimpleFIN subscription.")
			)

		if resp.status_code != 200:
			raise SimpleFINHTTPError(resp.status_code, resp.text)

		try:
			data = resp.json()
		except ValueError:
			raise SimpleFINError(
				_("Failed to parse SimpleFIN response as JSON.")
			)

		return data

	def test_connection(self) -> dict:
		"""Lightweight connection test using balances-only mode.

		Returns:
			Parsed JSON response dict.
		"""
		return self.get_accounts(balances_only=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enforce_https(url: str) -> None:
	"""Raise if the URL scheme is not HTTPS."""
	parsed = urlparse(url)
	if parsed.scheme.lower() != "https":
		raise SimpleFINError(
			_("SimpleFIN requires HTTPS. Received URL with scheme: {0}").format(
				parsed.scheme or "(empty)"
			)
		)
