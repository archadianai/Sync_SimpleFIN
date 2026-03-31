import frappe

def run():
	cf_count = len(frappe.get_all("Custom Field",
		filters={"dt": "Bank Transaction", "fieldname": ["like", "simplefin%"]}))
	if cf_count < 8:
		from sync_simplefin.install import after_install
		after_install()
		frappe.log(f"Custom fields created ({cf_count} -> 8)")
	else:
		frappe.log(f"Custom fields already present ({cf_count})")
