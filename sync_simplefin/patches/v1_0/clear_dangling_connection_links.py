# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

import frappe


def execute() -> None:
	"""Clear connection links that point at deleted SimpleFIN Connections.

	Connections deleted before v1.0.4 left dangling links on retained
	Bank Transactions and Sync Logs. Frappe validates link targets on
	every document save, so the dangling reference blocked bank
	reconciliation (and any other post-submit edit) with
	"Could not find SimpleFIN Connection". v1.0.4+ clears the links at
	deletion time (SimpleFINConnection.on_trash); this patch repairs
	orphans left by earlier versions. Idempotent — only links whose
	target no longer exists are touched.
	"""
	valid = set(frappe.get_all("SimpleFIN Connection", pluck="name"))

	targets = [
		("Bank Transaction", "simplefin_connection"),
		("SimpleFIN Sync Log", "connection"),
	]
	for doctype, field in targets:
		if not frappe.db.has_column(doctype, field):
			continue
		linked = frappe.get_all(doctype, filters={field: ["is", "set"]}, fields=["name", field])
		stale = [row.name for row in linked if row.get(field) not in valid]
		if stale:
			frappe.db.set_value(doctype, {"name": ["in", stale]}, field, None)
