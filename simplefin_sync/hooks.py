# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

app_name = "simplefin_sync"
app_title = "SimpleFIN Sync"
app_publisher = "Steve Bourg"
app_description = "Bank transaction sync via SimpleFIN Bridge for ERPNext"
app_email = "steve@bourg.com"
app_version = "1.0.0"
app_license = "GPL-3.0"
required_apps = ["frappe", "erpnext"]

# Custom fields injected into Bank Transaction
after_install = "simplefin_sync.install.after_install"
after_uninstall = "simplefin_sync.install.after_uninstall"

# Scheduled tasks
scheduler_events = {
	"all": [
		"simplefin_sync.tasks.check_due_syncs"
	],
	"daily": [
		"simplefin_sync.tasks.cleanup_old_sync_logs"
	],
}

# Allow deleting a SimpleFIN Connection even when Sync Logs and
# Bank Transactions still reference it — those records are retained
# as historical data.  The list names the doctypes whose Link fields
# should be ignored (not the doctype being deleted).
ignore_links_on_delete = ["SimpleFIN Sync Log", "Bank Transaction"]

# Document events
doc_events = {
	"Bank Transaction": {
		"on_trash": "simplefin_sync.utils.sync.on_bank_transaction_trash"
	}
}
