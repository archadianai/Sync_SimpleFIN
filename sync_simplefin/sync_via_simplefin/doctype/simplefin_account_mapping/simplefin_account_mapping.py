# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

from frappe.model.document import Document


class SimpleFINAccountMapping(Document):
	"""Maps a SimpleFIN account to an ERPNext Bank Account. Child of SimpleFIN Connection.

	Custom-regex validation lives in the parent controller
	(``SimpleFINConnection._validate_account_mapping_regexes``) — Frappe does
	not invoke a child doctype's ``validate()`` on parent save.
	"""
