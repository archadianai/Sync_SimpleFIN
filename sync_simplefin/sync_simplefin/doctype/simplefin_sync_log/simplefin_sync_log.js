// Copyright (c) 2026, Steve Bourg and Contributors
// Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
// License: GPL-3.0

frappe.ui.form.on("SimpleFIN Sync Log", {
	refresh(frm) {
		// Sync logs are system-generated and should not be editable
		frm.disable_save();
	},
});
