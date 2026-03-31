# CLAUDE.md — Sync via SimpleFIN for ERPNext

## Project Overview

You are building a Frappe/ERPNext application called **Sync via SimpleFIN** that imports bank transactions from the SimpleFIN Bridge into ERPNext's Bank Transaction doctype for reconciliation.

**Read `v1.0/simplefin_sync_spec.md` first.** It is the authoritative specification. This file tells you how to work; the spec tells you what to build.

## Environment

- **Framework:** Frappe Framework + ERPNext v16
- **Python:** 3.14+ (Frappe v16 requirement; bench image ships 3.14.2)
- **Dev environment:** Ubuntu 24.04, ERPNext in Docker containers
- **Production target:** Frappe Cloud (Hetzner)
- **License:** GPL-3.0
- **GitHub Repo:** SimpleFIN_Sync

## Project Structure

This is a standard Frappe app. Scaffold with:
```bash
cd ~/frappe-bench
bench new-app sync_simplefin
```

The app lives at `~/frappe-bench/apps/sync_simplefin/`. All paths below are relative to the app root.

## Critical Conventions

### File Headers
Every `.py` and `.js` file must start with:
```python
# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0
```

### Python Style
- Follow PEP 8. Use tabs for indentation in Frappe convention (Frappe uses tabs, not spaces).
  **IMPORTANT:** Verify this — recent Frappe versions may have switched. Check `.editorconfig` in the frappe repo. If frappe uses tabs, we use tabs. If spaces, we use spaces. Match exactly.
- Type hints on all public method signatures.
- Docstrings on all public methods and classes (Google style).
- Use `frappe._()` for ALL user-facing strings (translation support).
- Use `frappe.logger(__name__)` for logging, never `print()`. (Note: `get_logger` was removed in Frappe v16.)
- Use `frappe.db` ORM methods, never raw SQL except for performance-critical dedup queries (as specified in Section 5.3 of the spec).
- Use `Decimal` (from Python `decimal` module) for parsing SimpleFIN amounts. Never `float`.

### JavaScript Style
- Follow Frappe client-side conventions: `frappe.ui.form.on()`, `frm` object patterns.
- Use `frappe.call()` for server communication, never raw `fetch()` or `XMLHttpRequest`.
- Use `__()` for translatable strings in JS.

### DocType JSON Files
- Generate via Frappe's DocType creation process or write by hand following the exact JSON schema that Frappe expects.
- `"module": "SimpleFIN Sync"` for all doctypes.
- `"engine": "InnoDB"` for all doctypes.
- Child table doctypes: `"istable": 1`, `"editable_grid": 1`.
- Single doctypes (Settings): `"issingle": 1`.

### Custom Fields on Existing DocTypes
- Use `create_custom_fields()` from `frappe.custom.doctype.custom_field.custom_field`.
- Create in `install.py:after_install()`.
- Remove in `install.py:after_uninstall()` using `frappe.db.delete("Custom Field", ...)`.
- Never modify core ERPNext DocType JSON files directly.

### Password Fields (Security Critical)
- `access_url` and `setup_token` use fieldtype `"Password"`.
- Frappe encrypts Password fields at rest using the site's `encryption_key`.
- To read: `doc.get_password("access_url")` — never access the field directly.
- To write: `doc.access_url = value` then `doc.save()` — Frappe handles encryption.
- Never log, print, or include Password field values in error messages or sync logs.

### Scheduled Tasks
- Register in `hooks.py` under `scheduler_events`.
- The `"all"` event runs every ~60 seconds on Frappe Cloud.
- Long-running work must be enqueued: `frappe.enqueue(..., queue="long")`.
- Always use `deduplicate=True` on enqueue to prevent duplicate jobs. Frappe v16 also requires `job_id` when deduplicating.

### Testing
- Test files go in each doctype directory: `test_simplefin_connection.py`, etc.
- Use `frappe.tests.utils.FrappeTestCase` as the base class.
- Run tests with: `bench run-tests --app sync_simplefin`
- Mock external HTTP calls — never hit the real SimpleFIN API in tests.
- Use `unittest.mock.patch` for mocking `requests.get` and `requests.post`.

## Build Sequence

Follow this order strictly. Complete and verify each phase before starting the next.

### Phase 1: Scaffold and DocTypes

1. **Scaffold the app** (if not already done):
   ```bash
   bench new-app sync_simplefin
   bench --site your-site.local install-app sync_simplefin
   ```

2. **Create the module:** Ensure `sync_simplefin/sync_simplefin/` directory exists with `__init__.py`.

3. **Create DocTypes in this order** (dependencies flow downward):
   - `SimpleFIN Sync Settings` (Single DocType — no dependencies)
   - `SimpleFIN Balance Snapshot` (Child Table — no dependencies)
   - `SimpleFIN Account Mapping` (Child Table — no dependencies)
   - `SimpleFIN Sync Log` (Normal DocType — references SimpleFIN Connection, has Balance Snapshot child)
   - `SimpleFIN Connection` (Normal DocType — has Account Mapping child, references Sync Log)

   For each DocType:
   - Create the JSON definition with all fields from the spec (Section 3).
   - Create the Python controller (`.py` file) with validation logic.
   - Create the JS controller (`.js` file) with form behavior (buttons, depends_on, indicators).

4. **Verify Phase 1:**
   - `bench migrate` succeeds without errors.
   - All DocTypes appear in the Frappe UI.
   - You can create, save, and read a SimpleFIN Sync Settings document.
   - You can create a SimpleFIN Connection with all fields visible and validates correctly.
   - `sync_time`, `sync_day_of_week`, `sync_day_of_month` show/hide based on `sync_frequency` selection.
   - `enabled` cannot be checked when `is_registered` is 0.

### Phase 2: Custom Fields and Install Script

1. **Create `install.py`** with `after_install()` and `after_uninstall()`.
2. **Define all custom fields** on Bank Transaction per spec Section 4.1.
3. **Create the database index** on `(simplefin_account_id, simplefin_transaction_id)`.

4. **Verify Phase 2:**
   - `bench --site your-site.local reinstall-app sync_simplefin` (or uninstall/install cycle).
   - Custom fields appear on the Bank Transaction form.
   - `simplefin_last_seen`, `simplefin_transaction_id`, etc. are all present.
   - After uninstall, custom fields are removed cleanly.

### Phase 3: SimpleFIN Client

1. **Create `utils/simplefin_client.py`** — the HTTP client class per spec Section 11.
   - `claim_access_url(setup_token)`: base64 decode → POST → return Access URL.
   - `__init__(access_url)`: parse URL into base_url + Basic Auth credentials.
   - `get_accounts(...)`: build request, make HTTPS GET, parse response.
   - `test_connection()`: call `get_accounts(balances_only=True)`.
   - All methods enforce HTTPS, verify SSL, set 30-second timeout.
   - Never log credentials.

2. **Create unit tests** for the client with mocked HTTP responses:
   - Successful token exchange (200 + Access URL).
   - Failed token exchange (403).
   - Successful accounts fetch (200 + JSON).
   - Auth failure on accounts (403).
   - Payment required (402).
   - Network timeout.
   - Non-HTTPS URL rejection.

3. **Verify Phase 3:**
   - All unit tests pass: `bench run-tests --app sync_simplefin --module sync_simplefin.utils.test_simplefin_client`

### Phase 4: Token Exchange Flow

1. **Implement token exchange** in `SimpleFIN Connection` controller:
   - On save, if `setup_token` has a value and `is_registered == 0`, trigger exchange.
   - On success: store `access_url` (encrypted), set `is_registered = 1`, clear `setup_token`, set `registration_date`.
   - On 403: show error via `frappe.throw()`.
   - Add the "Register" button to the JS controller.

2. **Implement "Test Connection" button:**
   - Calls `test_connection()` on the client.
   - Displays account names and balances in a dialog, or error message.

3. **Verify Phase 4:**
   - Using the SimpleFIN demo token (from the developer page), complete a full exchange.
   - `access_url` is stored (verify it's encrypted in the DB — the raw column should be gibberish).
   - `is_registered` flips to 1.
   - Test Connection button shows demo account data.
   - Using an already-claimed token shows the 403 error message.

### Phase 5: Core Sync Logic

1. **Create `utils/sync.py`** — the main sync function per spec Section 5.1.
   - State machine transitions per Section 6.4.
   - Date range calculation and chunk building (newest-first) per Section 5.4.
   - Per-chunk fetch and import loop.
   - Dedup with all-docstatus check per Section 5.3.
   - `simplefin_last_seen` updates on every dedup hit.
   - Bank Transaction creation with correct field mapping per Section 5.2.
   - **CRITICAL:** Set `unallocated_amount` and `allocated_amount` per the spec note.
   - **CRITICAL:** Set `status = "Unreconciled"` not "Pending".
   - Rate limit warning detection in errors array per Section 5.5.
   - Rate limit pause activation (`rate_limit_paused_until`) when warning detected.
   - Balance snapshot storage.
   - Sync log creation and finalization.

2. **Create `utils/enrichment.py`** — transaction data enrichment per spec Section 5.6.
   - `enrich_transaction(description, extra, connection)`: Main dispatch function. Checks connection-level toggle (`extract_reference_number`, `extract_party_name`) and routes to custom regex or built-in patterns accordingly.
   - `apply_custom_regex(pattern, description)`: Applies a user-provided regex with one capture group. Returns captured value or None. Handles invalid patterns gracefully.
   - `extract_reference_number(description, extra)`: Built-in extraction. Checks SimpleFIN `extra` object keys, then check number patterns, then reference/confirmation patterns.
   - `extract_party_name(description)`: Built-in extraction. Strips common prefixes (ACH, POS, Wire, etc.), suffixes (store numbers, ZIP codes), and title-cases all-caps results.
   - `validate_custom_regex(pattern, field_label)`: Called on SimpleFIN Connection save. Validates regex compiles and has exactly one capture group.
   - All functions return `None` when extraction is disabled, no match found, or regex is invalid — never force a bad value.

3. **Create `utils/notifications.py`** — notification helpers per spec Section 9.

4. **Add "Sync Now" button** to SimpleFIN Connection JS controller.
   - Must check `rate_limit_paused_until` and block with message if active.

5. **Create unit tests:**
   - Sync with mocked client — verify Bank Transactions are created correctly.
   - Dedup — run sync twice, verify no duplicates on second run.
   - Mismatch detection — change a mocked transaction's amount, verify alert.
   - Cancelled transaction — cancel a Bank Transaction, run sync, verify dedup catches it and `last_seen` is updated.
   - Date range splitting — verify chunk ordering is newest-first.
   - Stop conditions — verify sync stops on empty chunk and all-duplicate chunk.
   - Rate limit warning — mock a response with rate limit error string, verify pause is activated and remaining chunks are aborted.
   - Reference number extraction — test against `extra` object keys, check patterns, reference patterns, and descriptions with no reference.
   - Party name extraction — test ACH, POS, wire, direct deposit descriptions; test all-caps normalization; test no-party descriptions (interest, fees, check-only).
   - Custom regex — test valid custom regex overrides built-in patterns; test invalid regex logs warning and returns None; test regex with wrong number of capture groups is rejected on save.
   - Enrichment toggles — test that disabling `extract_reference_number` or `extract_party_name` on a connection skips extraction entirely.

6. **Verify Phase 5:**
   - Manual sync with SimpleFIN demo token creates Bank Transactions.
   - Transactions appear in Bank Reconciliation Tool (status = "Unreconciled").
   - `reference_number` and `bank_party_name` are populated where data is available.
   - Running sync again creates no duplicates.
   - Sync log is created with accurate counts.

### Phase 6: Scheduler

1. **Create `tasks.py`** with all scheduled task functions per spec Section 6.
   - `check_due_syncs()`: state machine evaluation per Section 6.3.
   - `cleanup_old_sync_logs()`: per Section 6.7.

2. **Register in `hooks.py`** under `scheduler_events`.

3. **Implement retry logic** — state transitions per Section 6.4.

4. **Implement stale state recovery** — 30-minute timeout per Section 6.3.

5. **Implement save-time validation** — retry window < sync interval per Section 6.5.

6. **Verify Phase 6:**
   - Unit tests for `is_regular_interval_due()` with all frequency types.
   - Unit tests for retry state transitions.
   - Unit tests for stale state recovery.
   - Unit test: connection with active `rate_limit_paused_until` is skipped by scheduler.
   - Unit test: expired `rate_limit_paused_until` is auto-cleared by scheduler.
   - Unit test: connection isolation — one connection with corrupt data doesn't prevent other connections from being scheduled.
   - Validation error when retry_count × retry_interval ≥ sync interval.

### Phase 7: Workspace and Polish

1. **Create the SimpleFIN Sync workspace** with shortcuts to:
   - SimpleFIN Connection (list)
   - SimpleFIN Sync Log (list)
   - SimpleFIN Sync Settings
   - Bank Reconciliation Tool (link)

2. **Add dashboard indicators** to SimpleFIN Connection list view:
   - Registration status (green/red).
   - Enabled/Disabled (green/grey).
   - Last sync health (green/red/grey per spec).

3. **Add permission rules** per spec Section 7.4.

4. **Verify Phase 7:**
   - Workspace appears in sidebar.
   - All shortcuts work.
   - List view indicators render correctly.
   - Permissions are enforced (test with Accounts User role).

### Phase 8: Testing and Documentation

1. **Run full test suite:** `bench run-tests --app sync_simplefin`
2. **Write README.md** with installation instructions, configuration guide, screenshots placeholder.
   - Include the manual recovery procedure for accidentally cancelled transactions (delete + re-sync).
3. **Verify all file headers** have the copyright/attribution notice.
4. **Verify `hooks.py`** has all required entries (after_install, scheduler_events, doc_events).
5. **Verify `pyproject.toml`** is correctly configured for Frappe Marketplace. (Frappe v16 uses `pyproject.toml` with `flit_core`, not `setup.py`/`setup.cfg`.)

## Common Pitfalls to Avoid

1. **DO NOT** use `frappe.get_doc(...).save()` inside a loop without `frappe.db.commit()` batching. For bulk Bank Transaction creation, use `frappe.get_doc({...}).insert(ignore_permissions=True)` and commit in batches of 100.

2. **DO NOT** forget `ignore_permissions=True` on system-created documents (Bank Transactions created by the sync job run as the scheduler user, not as an Accounts Manager).

3. **DO NOT** store the Access URL in any log, error message, or sync log field. The `simplefin_connection` Link field on Bank Transaction is sufficient for traceability.

4. **DO NOT** use `frappe.db.sql` with string formatting for user-provided values. Always use parameterized queries: `frappe.db.sql("... WHERE x = %s", (value,))`.

5. **DO NOT** create Bank Transactions with `status = "Pending"`. It must be `"Unreconciled"`. See spec Section 5.2.

6. **DO NOT** forget to set `unallocated_amount = abs(amount)` and `allocated_amount = 0` on new Bank Transactions. Without this, they are invisible in the Bank Reconciliation Tool.

7. **DO NOT** use `float()` to parse SimpleFIN amounts. Use `decimal.Decimal(amount_string)` then convert to float only at the point of setting the Frappe Currency field.

8. **DO NOT** modify any core ERPNext DocType JSON or Python file. All extensions use custom fields, hooks, and client scripts.

9. **DO NOT** call the SimpleFIN API from the scheduler's `all` event handler directly. Always enqueue to `queue="long"`.

10. **DO NOT** skip the `simplefin_last_seen` update on dedup hits. This field tracks whether SimpleFIN is still returning a transaction, which is useful for debugging and auditing sync coverage.

11. **DO NOT** let one connection's error block other connections. The `check_due_syncs` scheduler loop must wrap each connection's evaluation in a try/except. An exception evaluating Connection A (e.g., corrupt field, DB error) must be logged and skipped — not allowed to abort the loop and prevent Connection B from being scheduled. See spec Section 6.3.

12. **DO NOT** worry about `bank_party_name` being read-only after submission. It is read-only by design in stock ERPNext (`allow_on_submit = 0`). This is fine — `bank_party_name` is an input to ERPNext's Automatic Party Matching, not the field users edit. If matching picks the wrong party, users correct `party_type` and `party` directly, which are editable on submitted transactions. We populate `bank_party_name` at creation time and never need to update it.

## Reference: Key Frappe APIs

```python
# Creating custom fields programmatically
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
create_custom_fields({
    "Bank Transaction": [
        {
            "fieldname": "simplefin_transaction_id",
            "fieldtype": "Data",
            "label": "SimpleFIN Transaction ID",
            "insert_after": "description",
            "read_only": 1,
        },
        # ... more fields
    ]
})

# Reading a Password field
access_url = doc.get_password("access_url")

# Enqueuing a background job
frappe.enqueue(
    "sync_simplefin.utils.sync.run_sync",
    connection=connection_name,
    queue="long",
    deduplicate=True,
    job_id=f"sync_simplefin_{connection_name}",
    timeout=600,  # 10 minutes max
)

# Creating a Bank Transaction (must be submitted — docstatus=1 — for BRT visibility and Cancel support)
bt = frappe.get_doc({
    "doctype": "Bank Transaction",
    "date": transaction_date,
    "bank_account": erpnext_bank_account,
    "deposit": deposit_amount,
    "withdrawal": withdrawal_amount,
    "currency": currency,
    "description": sanitized_description,
    "reference_number": extract_reference_number(description, extra),  # Section 5.6
    "bank_party_name": extract_party_name(description),               # Section 5.6
    "status": "Unreconciled",
    "unallocated_amount": abs_amount,
    "allocated_amount": 0,
    "simplefin_transaction_id": txn_id,
    "simplefin_connection": connection_name,
    "simplefin_account_id": account_id,
    "simplefin_posted_at": posted_datetime,
    "simplefin_transacted_at": transacted_datetime,
    "simplefin_raw_amount": raw_amount_string,
    "simplefin_last_seen": frappe.utils.now_datetime(),
})
bt.insert(ignore_permissions=True)
bt.submit()  # CRITICAL: Bank Transaction is submittable — must submit for BRT and Cancel

# Sending notifications
frappe.sendmail(
    recipients=recipient_list,
    subject=frappe._("SimpleFIN Sync Failed: {0}").format(connection_name),
    message=message_body,
)

# System notification
frappe.publish_realtime(
    "msgprint",
    {"message": message, "alert": True},
    user=frappe.session.user,
)
```

## Reference: hooks.py Complete Template

```python
app_name = "sync_simplefin"
app_title = "Sync via SimpleFIN"
app_publisher = "Steve Bourg"
app_description = "Bank transaction sync via SimpleFIN Bridge for ERPNext"
app_version = "1.0.0"
app_license = "GPL-3.0"
required_apps = ["frappe", "erpnext"]

after_install = "sync_simplefin.install.after_install"
after_uninstall = "sync_simplefin.install.after_uninstall"

scheduler_events = {
    "all": [
        "sync_simplefin.tasks.check_due_syncs"
    ],
    "daily": [
        "sync_simplefin.tasks.cleanup_old_sync_logs"
    ]
}

# Allow deleting connections while retaining historical sync logs and bank transactions.
# Value is the linking doctype (the one with the Link field), not the doctype being deleted.
ignore_links_on_delete = ["SimpleFIN Sync Log", "Bank Transaction"]

doc_events = {
    "Bank Transaction": {
        "on_trash": "sync_simplefin.utils.sync.on_bank_transaction_trash"
    }
}
```

## When You're Stuck

- If a Frappe API behaves unexpectedly, check the Frappe source code directly — it's the ground truth.
- If a DocType field doesn't appear after creation, run `bench migrate` and `bench clear-cache`.
- If scheduled tasks don't fire, check `bench doctor` and ensure the scheduler is enabled: `bench enable-scheduler`.
- If custom fields don't appear on Bank Transaction, verify the `after_install` hook ran: check `Custom Field` doctype for entries with `dt = "Bank Transaction"`.
