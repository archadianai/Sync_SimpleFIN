// Copyright (c) 2026, Steve Bourg and Contributors
// Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
// License: GPL-3.0

frappe.listview_settings["SimpleFIN Connection"] = {
	// Show all connections by default (Frappe auto-hides disabled records)
	filters: [["enabled", "like", "%"]],

	// Hide SFIN-#### ID column — connection name is the meaningful identifier
	hide_name_column: true,

	get_indicator(doc) {
		// Rate Limited (takes priority)
		if (doc.rate_limit_paused_until) {
			const pause = new Date(doc.rate_limit_paused_until);
			if (pause > new Date()) {
				return [__("Rate Limited"), "orange", "rate_limit_paused_until,is,set"];
			}
		}

		// Not registered
		if (!doc.is_registered) {
			return [__("Unregistered"), "red", "is_registered,=,0"];
		}

		// Disabled
		if (!doc.enabled) {
			return [__("Disabled"), "grey", "enabled,=,0"];
		}

		// Sync health based on last_sync_status
		if (doc.last_sync_status === "Never Synced") {
			return [__("Never Synced"), "grey", "last_sync_status,=,Never Synced"];
		}

		if (doc.last_sync_status === "Failed") {
			return [__("Failed"), "red", "last_sync_status,=,Failed"];
		}

		if (doc.last_sync_status === "Success") {
			return [__("Active"), "green", "last_sync_status,=,Success"];
		}

		return [__("Active"), "green", "enabled,=,1"];
	},
};
