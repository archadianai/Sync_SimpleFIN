# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

from frappe.model.document import Document


class SimpleFINBalanceSnapshot(Document):
	"""Balance data captured during a sync, stored as child of SimpleFIN Sync Log."""

	pass
