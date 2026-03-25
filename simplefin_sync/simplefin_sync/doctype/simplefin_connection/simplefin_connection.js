// Copyright (c) 2026, Steve Bourg and Contributors
// Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
// License: GPL-3.0

const CONN_METHOD =
	"simplefin_sync.simplefin_sync.doctype.simplefin_connection.simplefin_connection";

frappe.ui.form.on("SimpleFIN Connection", {
	refresh(frm) {
		// --- Setup wizard on new connection ---
		// Frappe reuses the form controller — track which doc we showed
		// the wizard for so it re-triggers on each new document.
		if (frm.is_new() && frm._wizard_shown_for !== frm.doc.name) {
			frm._wizard_shown_for = frm.doc.name;
			_show_setup_wizard(frm);
		}

		// --- Guide user on never-synced connections ---
		if (
			!frm.is_new() &&
			frm.doc.is_registered &&
			frm.doc.last_sync_status === "Never Synced" &&
			!frm._guide_shown
		) {
			frm._guide_shown = true;
			frm.dashboard.set_headline_alert(
				'<span class="indicator whitespace-nowrap blue">' +
				__("Review your settings below, then use Actions → Sync Full to import transactions.") +
				"</span>"
			);
		}

		// Disable duplicate row on Account Mappings grid + fix text wrapping
		if (frm.fields_dict.account_mappings) {
			frm.fields_dict.account_mappings.grid.cannot_add_rows = false;
			frm.fields_dict.account_mappings.grid.grid_buttons.find(".grid-duplicate-row").hide();
			_fix_grid_wrapping(frm);
		}

		// Show the system timezone on the Sync Time field description
		let sys_tz = frappe.boot.time_zone?.system || frappe.sys_defaults?.time_zone || "";
		if (sys_tz && frm.fields_dict.sync_time) {
			frm.fields_dict.sync_time.df.description = __("Time of day ({0})", [sys_tz]);
			frm.refresh_field("sync_time");
		}

		// --- Register / Re-register button ---
		if (!frm.is_new() && !frm.doc.is_registered && frm.doc.setup_token) {
			frm.add_custom_button(__("Register"), function () {
				_do_register(frm);
			}, __("Actions"));
		}
		if (!frm.is_new() && frm.doc.connection_status === "Revoked") {
			frm.add_custom_button(__("Re-register"), function () {
				_do_reregister(frm);
			}, __("Actions"));
		}

		// --- Test Connection button ---
		if (frm.doc.is_registered) {
			frm.add_custom_button(__("Test Connection"), function () {
				_do_test(frm);
			}, __("Actions"));
		}

		// --- Sync buttons ---
		if (frm.doc.enabled) {
			frm.add_custom_button(__("Sync Latest"), function () {
				_do_sync_now(frm);
			}, __("Actions"));

			frm.add_custom_button(__("Sync Full"), function () {
				frappe.confirm(
					__(
						"This will re-pull the full transaction history ({0} days). " +
						"Use this to recover deleted transactions or fill gaps. Continue?",
						[frm.doc.initial_history_days || 90]
					),
					function () {
						_do_sync_full(frm);
					}
				);
			}, __("Actions"));
		}

		// --- Enable & Sync for registered + mapped but not enabled ---
		if (!frm.is_new() && frm.doc.is_registered && !frm.doc.enabled && _has_mapped_accounts(frm)) {
			frm.add_custom_button(__("Enable & Sync"), function () {
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
		if (!frm.is_new()) {
			if (frm.doc.is_registered) {
				frm.dashboard.set_headline_alert(
					'<span class="indicator whitespace-nowrap green">' + __("Registered") + "</span>"
				);
			} else {
				frm.dashboard.set_headline_alert(
					'<span class="indicator whitespace-nowrap red">' + __("Unregistered") + "</span>"
				);
			}
		}
	},
});

// ---------------------------------------------------------------------------
// Setup Wizard Dialog
// ---------------------------------------------------------------------------

function _show_setup_wizard(frm) {
	let wizard_state = { step: 1, connection: null, accounts: [] };

	let d = new frappe.ui.Dialog({
		title: __("New SimpleFIN Connection"),
		size: "large",
		fields: [
			// Step indicator (updated per step)
			{
				fieldname: "step_html",
				fieldtype: "HTML",
				options: '<div class="text-muted small">' +
					__("Step 1 of 2: Connect to SimpleFIN Bridge") + "</div><hr>",
			},
			// Step 1 fields
			{
				fieldname: "connection_name",
				fieldtype: "Data",
				label: __("Connection Name"),
				reqd: 1,
				description: __("A friendly label (e.g., 'BECU Business', 'Chase Personal')"),
			},
			{
				fieldname: "setup_token",
				fieldtype: "Small Text",
				label: __("Setup Token"),
				reqd: 1,
				description: __(
					'Paste the token from <a href="https://beta-bridge.simplefin.org" target="_blank">SimpleFIN Bridge</a>. ' +
					"This is a one-time code that will be exchanged for a secure connection."
				),
			},
			// Step 2 field (hidden initially)
			{
				fieldname: "accounts_html",
				fieldtype: "HTML",
				hidden: 1,
			},
		],
		primary_action_label: __("Register"),
		primary_action(values) {
			if (wizard_state.step === 1) {
				_wizard_step1_register(d, wizard_state, values);
			} else {
				_wizard_step2_save(d, wizard_state);
			}
		},
		secondary_action_label: __("Cancel"),
		secondary_action() {
			d.hide();
		},
	});

	d.show();
}

function _wizard_step1_register(d, state, values) {
	frappe.call({
		method: CONN_METHOD + ".wizard_register",
		args: {
			connection_name: values.connection_name,
			setup_token: values.setup_token,
		},
		btn: d.get_primary_btn(),
		freeze: true,
		freeze_message: __("Exchanging token and fetching accounts…"),
		callback(r) {
			if (r.exc) return;

			let data = r.message;
			state.step = 2;
			state.connection = data.connection;
			state.accounts = data.accounts;

			// Update step indicator
			d.set_title(__("Map Your Accounts"));
			d.fields_dict.step_html.$wrapper.html(
				'<div class="text-muted small">' +
				__("Step 2 of 2: Map SimpleFIN accounts to ERPNext") +
				"</div><hr>"
			);

			// Hide step 1 fields
			d.set_df_property("connection_name", "hidden", 1);
			d.set_df_property("connection_name", "reqd", 0);
			d.set_df_property("setup_token", "hidden", 1);
			d.set_df_property("setup_token", "reqd", 0);

			// Show step 2 with account mapping table
			d.set_df_property("accounts_html", "hidden", 0);
			let html = _build_account_mapping_html(data);
			d.fields_dict.accounts_html.$wrapper.html(html);

			// Attach Link controls after DOM renders
			setTimeout(function () {
				_attach_bank_account_links(d, data.accounts);
			}, 200);

			d.set_primary_action(__("Create Connection"), function () {
				_wizard_step2_save(d, state);
			});
		},
	});
}

function _build_account_mapping_html(data) {
	let html = "";

	// Group accounts by org_name
	let groups = {};
	for (let acct of data.accounts) {
		let key = acct.org_name || "Other";
		if (!groups[key]) groups[key] = [];
		groups[key].push(acct);
	}

	for (let [org, accounts] of Object.entries(groups)) {
		if (Object.keys(groups).length > 1) {
			html += `<div class="mt-3 mb-2"><b>${frappe.utils.escape_html(org)}</b></div>`;
		}

		html += '<table class="table table-bordered table-sm"><thead><tr>' +
			`<th>${__("Account")}</th>` +
			`<th>${__("Currency")}</th>` +
			`<th>${__("Balance")}</th>` +
			`<th>${__("ERPNext Bank Account")}</th>` +
			"</tr></thead><tbody>";

		for (let acct of accounts) {
			let esc_id = frappe.utils.escape_html(acct.id);
			html += "<tr>" +
				`<td>${frappe.utils.escape_html(acct.name)}</td>` +
				`<td>${frappe.utils.escape_html(acct.currency)}</td>` +
				`<td class="text-right">${frappe.utils.escape_html(acct.balance)}</td>` +
				`<td><div class="wizard-bank-link" data-account-id="${esc_id}"></div></td>` +
				"</tr>";
		}

		html += "</tbody></table>";
	}

	html += '<p class="text-muted small">' +
		__("Select an ERPNext Bank Account for each account you want to sync. Leave blank to skip.") +
		"</p>";

	return html;
}

function _attach_bank_account_links(d, accounts) {
	d.$wrapper.find(".wizard-bank-link").each(function () {
		let $cell = $(this);
		let acct_id = $cell.data("account-id");

		// Use Frappe's ControlLink class directly
		let control = new frappe.ui.form.ControlLink({
			df: {
				fieldname: "bank_account_" + acct_id,
				fieldtype: "Link",
				options: "Bank Account",
				placeholder: __("Select Bank Account"),
			},
			parent: $cell,
			only_input: true,
		});
		control.make_input();
		control.$input.css("min-width", "200px");
		$cell.data("control", control);
	});
}

function _wizard_step2_save(d, state) {
	// Collect mappings from the Link controls
	let mappings = [];
	d.$wrapper.find(".wizard-bank-link").each(function () {
		let $cell = $(this);
		let acct_id = $cell.data("account-id");
		let control = $cell.data("control");
		let bank_acct = control ? control.get_value() : "";
		if (bank_acct) {
			mappings.push({
				simplefin_account_id: acct_id,
				erpnext_bank_account: bank_acct,
			});
		}
	});

	if (!mappings.length) {
		frappe.msgprint(__("Please map at least one account to an ERPNext Bank Account."));
		return;
	}

	frappe.call({
		method: CONN_METHOD + ".wizard_save_mappings",
		args: {
			connection: state.connection,
			mappings: JSON.stringify(mappings),
		},
		btn: d.get_primary_btn(),
		freeze: true,
		freeze_message: __("Saving connection…"),
		callback(r) {
			if (r.exc) return;
			d.hide();
			frappe.set_route("Form", "SimpleFIN Connection", state.connection);
			frappe.show_alert({
				message: __("Connection created and enabled! Use Actions → Sync Latest to pull transactions."),
				indicator: "green",
			});
		},
	});
}

// ---------------------------------------------------------------------------
// Re-register (for revoked connections)
// ---------------------------------------------------------------------------

function _do_reregister(frm) {
	frappe.prompt(
		[
			{
				fieldname: "setup_token",
				fieldtype: "Small Text",
				label: __("New Setup Token"),
				reqd: 1,
				description: __(
					'Paste a new token from <a href="https://beta-bridge.simplefin.org" target="_blank">SimpleFIN Bridge</a>.'
				),
			},
		],
		function (values) {
			frappe.call({
				method: CONN_METHOD + ".reregister",
				args: {
					connection: frm.doc.name,
					setup_token: values.setup_token,
				},
				freeze: true,
				freeze_message: __("Exchanging new token…"),
				callback(r) {
					if (!r.exc) {
						frappe.show_alert({
							message: __("Re-registration successful. Connection is active again."),
							indicator: "green",
						});
						frm.reload_doc();
					}
				},
			});
		},
		__("Re-register Connection"),
		__("Register")
	);
}

// ---------------------------------------------------------------------------
// Action button helpers (for existing connections)
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
		},
	});
}

function _do_test(frm) {
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

function _do_sync_full(frm) {
	if (frm.doc.rate_limit_paused_until) {
		frappe.msgprint(
			__("Connection is rate-limited until {0}. Sync blocked.", [
				frm.doc.rate_limit_paused_until,
			])
		);
		return;
	}
	frappe.call({
		method: CONN_METHOD + ".sync_full",
		args: { connection: frm.doc.name },
		freeze: true,
		freeze_message: __("Queuing full sync job…"),
		callback(r) {
			if (!r.exc) {
				frappe.show_alert({ message: __("Full sync job queued"), indicator: "blue" });
				frm.reload_doc();
			}
		},
	});
}

function _has_mapped_accounts(frm) {
	return (frm.doc.account_mappings || []).some(function (m) {
		return m.erpnext_bank_account;
	});
}

function _fix_grid_wrapping(frm) {
	let $grid = frm.fields_dict.account_mappings.$wrapper;

	// Force inline styles on every element in the constraint chain
	$grid.find(".form-grid").css({
		"overflow": "visible",
	});
	$grid.find(".data-row").css({
		"height": "auto",
	});
	$grid.find(".grid-static-col").css({
		"height": "auto",
		"max-height": "none",
		"overflow": "visible",
		"padding-top": "8px",
		"padding-bottom": "8px",
	});
	$grid.find(".static-area").each(function () {
		$(this).removeClass("ellipsis").css({
			"white-space": "normal",
			"overflow": "visible",
			"text-overflow": "unset",
			"word-break": "break-word",
			"max-width": "none",
			"line-height": "1.4",
		});
	});

	// Also fix editable-row inputs so they don't collapse height
	$grid.find(".editable-row .grid-static-col").css({
		"height": "auto",
		"max-height": "none",
		"overflow": "visible",
	});
	$grid.find(".editable-row .field-area").css({
		"overflow": "visible",
	});
	$grid.find(".editable-row .form-group").css({
		"margin-bottom": "0",
	});

	// Re-apply after grid re-renders (click in/out of cells)
	if (!frm._grid_wrap_observer) {
		let observer = new MutationObserver(function () {
			_fix_grid_wrapping(frm);
		});
		let target = $grid.find(".grid-body")[0];
		if (target) {
			observer.observe(target, { childList: true, subtree: true });
			frm._grid_wrap_observer = observer;
		}
	}
}
