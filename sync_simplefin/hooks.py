# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0

app_name = "sync_simplefin"
app_title = "Sync via SimpleFIN"
app_publisher = "Steve Bourg"
app_description = "Bank transaction sync via SimpleFIN Bridge for ERPNext"
app_email = "steve@bourg.com"
app_version = "1.0.1"
app_license = "GPL-3.0"
required_apps = ["erpnext"]

app_include_css = "/assets/sync_simplefin/css/sync_simplefin.css"

# Custom fields injected into Bank Transaction
after_install = "sync_simplefin.install.after_install"
after_uninstall = "sync_simplefin.install.after_uninstall"

# Scheduled tasks
scheduler_events = {
	"all": [
		"sync_simplefin.tasks.check_due_syncs"
	],
	"daily": [
		"sync_simplefin.tasks.cleanup_old_sync_logs"
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
		"on_trash": "sync_simplefin.utils.sync.on_bank_transaction_trash"
	}
}
