# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
	"Bank Transaction": [
		{
			"fieldname": "simplefin_transaction_id",
			"fieldtype": "Data",
			"label": "SimpleFIN Transaction ID",
			"insert_after": "description",
			"read_only": 1,
		},
		{
			"fieldname": "simplefin_connection",
			"fieldtype": "Link",
			"label": "SimpleFIN Connection",
			"options": "SimpleFIN Connection",
			"insert_after": "simplefin_transaction_id",
			"read_only": 1,
		},
		{
			"fieldname": "simplefin_account_id",
			"fieldtype": "Data",
			"label": "SimpleFIN Account ID",
			"insert_after": "simplefin_connection",
			"read_only": 1,
		},
		{
			"fieldname": "simplefin_posted_at",
			"fieldtype": "Datetime",
			"label": "SimpleFIN Posted At",
			"insert_after": "simplefin_account_id",
			"read_only": 1,
		},
		{
			"fieldname": "simplefin_transacted_at",
			"fieldtype": "Datetime",
			"label": "SimpleFIN Transacted At",
			"insert_after": "simplefin_posted_at",
			"read_only": 1,
		},
		{
			"fieldname": "simplefin_raw_amount",
			"fieldtype": "Data",
			"label": "SimpleFIN Raw Amount",
			"insert_after": "simplefin_transacted_at",
			"read_only": 1,
		},
		{
			"fieldname": "simplefin_pending",
			"fieldtype": "Check",
			"label": "SimpleFIN Pending",
			"insert_after": "simplefin_raw_amount",
			"read_only": 1,
		},
		{
			"fieldname": "simplefin_last_seen",
			"fieldtype": "Datetime",
			"label": "SimpleFIN Last Seen",
			"insert_after": "simplefin_pending",
			"read_only": 1,
		},
	]
}

INDEX_FIELDS = [
	("Bank Transaction", "simplefin_account_id"),
	("Bank Transaction", "simplefin_transaction_id"),
]


def after_install() -> None:
	"""Create custom fields on Bank Transaction and add dedup index."""
	create_custom_fields(CUSTOM_FIELDS, update=True)

	# Create composite index for fast dedup lookups.
	_create_dedup_index()

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- persist custom-field DDL outside install transaction


def after_uninstall() -> None:
	"""Remove custom fields added by this app."""
	for dt, fields in CUSTOM_FIELDS.items():
		for field in fields:
			name = frappe.db.get_value(
				"Custom Field",
				{"dt": dt, "fieldname": field["fieldname"]},
			)
			if name:
				frappe.delete_doc("Custom Field", name, force=True)

	_drop_dedup_index()

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- persist custom-field DDL removal outside uninstall transaction


def _create_dedup_index() -> None:
	"""Create index on (simplefin_account_id, simplefin_transaction_id) for Bank Transaction."""
	# Check if index already exists.
	existing = frappe.db.sql(
		"SHOW INDEX FROM `tabBank Transaction` WHERE Key_name = %s",
		("idx_simplefin_dedup",),
	)
	if not existing:
		frappe.db.sql(
			"CREATE INDEX `idx_simplefin_dedup` ON `tabBank Transaction` "
			"(`simplefin_account_id`, `simplefin_transaction_id`)"
		)


def _drop_dedup_index() -> None:
	"""Drop the dedup index if it exists."""
	try:
		frappe.db.sql(
			"DROP INDEX `idx_simplefin_dedup` ON `tabBank Transaction`"
		)
	except Exception:
		pass
