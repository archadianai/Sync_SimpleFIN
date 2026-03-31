# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

from sync_simplefin.utils.enrichment import validate_custom_regex

from frappe import _
from frappe.model.document import Document


class SimpleFINAccountMapping(Document):
	"""Maps a SimpleFIN account to an ERPNext Bank Account. Child of SimpleFIN Connection."""

	def validate(self) -> None:
		if self.custom_reference_regex:
			validate_custom_regex(self.custom_reference_regex, _("Custom Reference Regex"))
		if self.custom_party_regex:
			validate_custom_regex(self.custom_party_regex, _("Custom Party Regex"))
