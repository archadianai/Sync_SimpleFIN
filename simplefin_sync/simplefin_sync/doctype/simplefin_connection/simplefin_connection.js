// Copyright (c) 2026, Steve Bourg and Contributors
// Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
// License: GPL-3.0

const CONN_METHOD =
	"simplefin_sync.simplefin_sync.doctype.simplefin_connection.simplefin_connection";

frappe.ui.form.on("SimpleFIN Connection", {
	setup(frm) {
		// Fetch timezone list once and cache on the form object
		if (!frm._tz_options) {
			frappe.call({
				method: CONN_METHOD + ".get_timezone_options",
				async: false,
				callback(r) {
					if (r.message) {
						frm._tz_options = r.message.join("\n");
					}
				},
			});
		}
	},

	after_save(frm) {
		// Only prompt on first save (creation) of an unregistered connection
		// that has a setup token.
		if (frm.is_new_doc_state && !frm.doc.is_registered && frm.doc.setup_token) {
			frappe.confirm(
				__("Setup token detected. Register with SimpleFIN Bridge now?"),
				function () {
					_do_register(frm);
				}
			);
		}
		frm.is_new_doc_state = false;
	},

	before_save(frm) {
		// Track whether this is the initial creation save
		if (frm.is_new()) {
			frm.is_new_doc_state = true;
		}
	},

	refresh(frm) {
		// Apply timezone options on every refresh (including after reload_doc)
		if (frm._tz_options && frm.fields_dict.transaction_timezone) {
			frm.fields_dict.transaction_timezone.df.options = frm._tz_options;
			frm.refresh_field("transaction_timezone");
		}

		// Show the system timezone on the Sync Time field description
		let sys_tz = frappe.boot.time_zone?.system || frappe.sys_defaults?.time_zone || "";
		if (sys_tz && frm.fields_dict.sync_time) {
			frm.fields_dict.sync_time.df.description = __("Time of day ({0})", [sys_tz]);
			frm.refresh_field("sync_time");
		}

		// --- Register button ---
		if (!frm.doc.is_registered && frm.doc.setup_token) {
			frm.add_custom_button(__("Register"), function () {
				_do_register(frm);
			}, __("Actions"));
		}

		// --- Test Connection button ---
		if (frm.doc.is_registered) {
			frm.add_custom_button(__("Test Connection"), function () {
				_do_test(frm);
			}, __("Actions"));
		}

		// --- Sync Now button ---
		if (frm.doc.enabled) {
			frm.add_custom_button(__("Sync Now"), function () {
				_do_sync_now(frm);
			}, __("Actions"));
		}

		// --- Enable & Sync prompt for registered but not-yet-enabled connections
		//     that have at least one mapped account ---
		if (frm.doc.is_registered && !frm.doc.enabled && _has_mapped_accounts(frm)) {
			frm.add_custom_button(__("Enable & Sync Now"), function () {
				frm.set_value("enabled", 1);
				frm.save().then(function () {
					_do_sync_now(frm);
				});
			}, __("Actions"));
		}

		// --- Clear Rate Limit Pause button ---
		if (frm.doc.rate_limit_paused_until && frappe.user_roles.includes("System Manager")) {
			frm.add_custom_button(__("Clear Rate Limit Pause"), function () {
				frappe.confirm(
					__(
						"This will allow syncs to resume immediately. " +
						"Use with caution — forcing syncs against a rate-limited API risks token disabling."
					),
					function () {
						frappe.call({
							method: CONN_METHOD + ".clear_rate_limit_pause",
							args: { connection: frm.doc.name },
							callback(r) {
								if (!r.exc) {
									frappe.show_alert({
										message: __("Rate limit pause cleared"),
										indicator: "green",
									});
									frm.reload_doc();
								}
							},
						});
					}
				);
			}, __("Actions"));
		}

		// --- Dashboard indicators ---
		if (frm.doc.is_registered) {
			frm.dashboard.set_headline_alert(
				'<span class="indicator whitespace-nowrap green">' + __("Registered") + "</span>"
			);
		} else {
			frm.dashboard.set_headline_alert(
				'<span class="indicator whitespace-nowrap red">' + __("Unregistered") + "</span>"
			);
		}
	},
});

// ---------------------------------------------------------------------------
// Chained workflow helpers
// ---------------------------------------------------------------------------

function _do_register(frm) {
	if (frm.doc.rate_limit_paused_until) {
		frappe.msgprint(
			__("Connection is rate-limited until {0}.", [frm.doc.rate_limit_paused_until])
		);
		return;
	}
	frappe.call({
		method: CONN_METHOD + ".register_token",
		args: { connection: frm.doc.name },
		freeze: true,
		freeze_message: __("Registering and fetching accounts…"),
		callback(r) {
			if (r.exc) return;
			frappe.show_alert({ message: __("Registration successful"), indicator: "green" });
			frm.reload_doc();
			// Accounts are already populated — guide user to mapping
			frappe.msgprint({
				title: __("Registration Complete"),
				message: __(
					"Your SimpleFIN accounts have been discovered and added to the " +
					"Account Mappings table below.<br><br>" +
					"<b>Next steps:</b><br>" +
					"1. Set the <b>ERPNext Bank Account</b> for each account you want to sync<br>" +
					"2. Check the <b>Active</b> checkbox for those accounts<br>" +
					"3. Check the <b>Enabled</b> checkbox at the top of this form<br>" +
					"4. Save, then click <b>Actions → Sync Now</b>"
				),
				indicator: "green",
				primary_action: {
					label: __("Go to Account Mappings"),
					action() {
						frappe.msg_dialog.hide();
						frm.scroll_to_field("account_mappings");
					},
				},
			});
		},
	});
}

function _do_test(frm, offer_sync) {
	if (frm.doc.rate_limit_paused_until) {
		frappe.msgprint(
			__("Connection is rate-limited until {0}. Test blocked.", [
				frm.doc.rate_limit_paused_until,
			])
		);
		return;
	}
	frappe.call({
		method: CONN_METHOD + ".test_connection",
		args: { connection: frm.doc.name },
		freeze: true,
		freeze_message: __("Testing connection…"),
		callback(r) {
			if (r.exc) return;
			if (r.message) {
				frappe.msgprint({
					title: __("Connection Test"),
					message: r.message,
					indicator: "green",
				});
			}
			if (offer_sync) {
				// Wait a moment for the msgprint to render, then offer sync
				setTimeout(function () {
					frappe.confirm(
						__(
							"Connection test successful! " +
							"To sync transactions, map your accounts in the Account Mappings " +
							"table, enable the connection, and click Sync Now."
						),
						function () {
							frm.reload_doc();
							frm.scroll_to_field("account_mappings");
						}
					);
				}, 500);
			}
		},
	});
}

function _do_sync_now(frm) {
	if (frm.doc.rate_limit_paused_until) {
		frappe.msgprint(
			__("Connection is rate-limited until {0}. Sync blocked.", [
				frm.doc.rate_limit_paused_until,
			])
		);
		return;
	}
	frappe.call({
		method: CONN_METHOD + ".sync_now",
		args: { connection: frm.doc.name },
		freeze: true,
		freeze_message: __("Queuing sync job…"),
		callback(r) {
			if (!r.exc) {
				frappe.show_alert({ message: __("Sync job queued"), indicator: "blue" });
				frm.reload_doc();
			}
		},
	});
}

function _has_mapped_accounts(frm) {
	// Returns true if at least one account mapping has an ERPNext Bank Account set
	return (frm.doc.account_mappings || []).some(function (m) {
		return m.erpnext_bank_account;
	});
}
