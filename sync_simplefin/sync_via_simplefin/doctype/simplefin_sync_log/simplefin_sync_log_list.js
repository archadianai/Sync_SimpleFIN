// Copyright (c) 2026, Steve Bourg and Contributors
// Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
// License: GPL-3.0

frappe.listview_settings["SimpleFIN Sync Log"] = {
	get_indicator(doc) {
		const status_map = {
			"Success": [__("Success"), "green", "status,=,Success"],
			"Partial Success": [__("Partial Success"), "orange", "status,=,Partial Success"],
			"Failed": [__("Failed"), "red", "status,=,Failed"],
			"In Progress": [__("In Progress"), "blue", "status,=,In Progress"],
		};
		return status_map[doc.status] || [doc.status, "grey"];
	},
};
