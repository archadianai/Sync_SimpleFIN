# SimpleFIN Sync — Technical Specification

**App Name:** SimpleFIN Sync
**Version:** 1.0.0
**Authors:** Steve Bourg and Claude Opus/Sonnet 4.6
**License:** GPL-3.0 (compatible with ERPNext codebase for future absorption)
**Target:** ERPNext v16 on Frappe Cloud (Hetzner) · Frappe Marketplace publication
**Repository:** GitHub (public, required for Marketplace)

---

## 1. Overview

SimpleFIN Sync is a Frappe/ERPNext application that connects ERPNext to bank accounts via the [SimpleFIN Bridge](https://beta-bridge.simplefin.org/info/developers). Each configured connection periodically retrieves posted bank transactions and imports them as `Bank Transaction` records in ERPNext, ready for reconciliation using ERPNext's standard Bank Reconciliation Tool.

### 1.1 Design Principles

- **No harm to stock ERPNext.** The app extends ERPNext via custom fields, custom doctypes, and hooks — never patches or monkey-patches core doctypes.
- **Follow Frappe/ERPNext conventions.** DocTypes, naming, API patterns, scheduled tasks, permissions, and UI patterns match the existing codebase style.
- **Secure by default.** Access URLs (which contain embedded credentials) are stored encrypted. All SimpleFIN communication is HTTPS-only.
- **Respect SimpleFIN rate limits.** The bridge allows ≤24 requests/day per access token, with separate per-account quotas. The app enforces this.
- **Idempotent syncs.** Running a sync multiple times for the same period produces no duplicates and no data corruption.

---

## 2. SimpleFIN Protocol Summary

Reference: [SimpleFIN Protocol v1.0.7](https://www.simplefin.org/protocol.html) · [Bridge Developer Guide](https://beta-bridge.simplefin.org/info/developers)

### 2.1 Authentication Flow

```
User obtains Setup Token from SimpleFIN Bridge website
         │
         ▼
User pastes Setup Token into SimpleFIN Sync connection form
         │
         ▼
App base64-decodes Setup Token → Claim URL
         │
         ▼
App POSTs to Claim URL → receives Access URL (one-time exchange)
         │
         ▼
Access URL stored encrypted; used for all subsequent /accounts requests
```

**Critical constraints:**
- Setup Token → Access URL exchange is **one-time only**. A 403 on claim means the token was already used or is compromised.
- Access URL format: `https://{username}:{password}@{host}/{path}` — contains Basic Auth credentials inline.
- Access URLs can be revoked by the user at any time via the SimpleFIN Bridge website.

### 2.2 Data Retrieval

**Endpoint:** `GET {access_url_base}/accounts`

| Parameter | Type | Description |
|---|---|---|
| `start-date` | UNIX timestamp | Transactions on or after this time (optional) |
| `end-date` | UNIX timestamp | Transactions before (not on) this time (optional) |
| `pending` | `1` or absent | Include pending transactions (optional, default: excluded) |
| `account` | string | Filter to specific account ID (optional, repeatable) |
| `balances-only` | `1` or absent | Return only balances, no transactions (optional) |

**Constraints:**
- Date range (`end-date` minus `start-date`) must be ≤ 90 days per request.
- Rate limit: ≤ 24 requests/day per access token. Per-account requests have separate quotas.
- Exceeding limits triggers warnings in the `errors` array, then token disabling.

### 2.3 Response Structure

```json
{
  "errors": ["String messages to display to user (sanitize before display)"],
  "accounts": [
    {
      "org": {
        "domain": "mybank.com",
        "name": "My Bank",
        "sfin-url": "https://sfin.mybank.com",
        "url": "https://mybank.com",
        "id": "optional-institution-id"
      },
      "id": "account-unique-id",
      "name": "Business Checking",
      "currency": "USD",
      "balance": "12345.67",
      "available-balance": "12000.00",
      "balance-date": 1700000000,
      "transactions": [
        {
          "id": "txn-unique-within-account",
          "posted": 1699900000,
          "amount": "-150.00",
          "description": "ACH Payment - Vendor",
          "transacted_at": 1699890000,
          "pending": false,
          "extra": {}
        }
      ],
      "extra": {}
    }
  ]
}
```

### 2.4 Required Application Behaviors (SimpleFIN Checklist)

The app **must**:
1. Handle 403 on claim → notify user token may be compromised
2. Only use HTTPS URLs (never HTTP)
3. Store Access URLs at least as securely as financial data → Frappe `Password` field type
4. Handle 403 on `/accounts` → mark connection as revoked, notify user
5. Handle 402 on `/accounts` → mark connection, notify user of payment issue
6. Display `errors` array contents to user (sanitized)
7. Sanitize all error messages before display (XSS prevention)
8. Verify SSL/TLS certificates on all requests

---

## 3. DocType Definitions

### 3.1 SimpleFIN Connection (DocType)

**Type:** Normal DocType
**Naming:** `SFIN-{####}` (autoname)
**Module:** SimpleFIN Sync

| Field | Type | Options/Default | Required | Description |
|---|---|---|---|---|
| **Connection Settings** | Section Break | | | |
| `connection_name` | Data | | Yes | User-friendly label (e.g., "BECU Business") |
| `setup_token` | Password | | No | One-time input; cleared after successful exchange |
| `access_url` | Password | | No | Encrypted storage of Access URL (set by system) |
| `is_registered` | Check | 0 | No | Set to 1 after successful token exchange (read-only) |
| `enabled` | Check | 0 | No | Cannot be enabled until `is_registered = 1` |
| `registration_date` | Datetime | | No | When the token exchange succeeded (read-only) |
| **Organization Info** | Section Break | | | *(Auto-populated from SimpleFIN on first successful sync)* |
| `org_domain` | Data | | No | Institution domain from SimpleFIN `org.domain` |
| `org_name` | Data | | No | Institution name from SimpleFIN `org.name` |
| `org_url` | Data | | No | Institution URL from SimpleFIN `org.url` |
| **Sync Schedule** | Section Break | | | |
| `sync_frequency` | Select | Every 2 Hours/4x Daily/Twice Daily/Daily/Weekly/Bi-Weekly/Monthly | Yes | Default: Daily |
| `sync_time` | Time | | No | Preferred time of day. Applies to Daily, Weekly, Bi-Weekly, and Monthly. Ignored for sub-daily frequencies (Every 2 Hours, 4x Daily, Twice Daily) where syncs are interval-based. Default: 02:00 (2 AM site timezone) |
| `sync_day_of_week` | Select | Monday/Tuesday/Wednesday/Thursday/Friday/Saturday/Sunday | No | Which day of the week to sync. Applies to Weekly and Bi-Weekly only. Default: Monday |
| `sync_day_of_month` | Int | 1 | No | Which day of the month to sync (1–28). Applies to Monthly only. Capped at 28 to avoid end-of-month ambiguity. Default: 1 |
| `retry_count` | Int | 3 | No | Number of retry attempts on failure |
| `retry_interval_minutes` | Int | 30 | No | Minutes between retries |
| `initial_history_days` | Int | 90 | No | Days of history to pull on first sync or re-enable. No artificial cap — requests are automatically split into ≤90-day chunks per SimpleFIN API limits. Actual data availability depends on the institution. |
| `rolling_window_days` | Int | 14 | No | Days of overlap on subsequent syncs to catch backdated txns |
| **Pending Transactions** | Section Break | | | |
| `include_pending` | Check | 0 | No | Whether to request pending transactions from SimpleFIN |
| **Transaction Enrichment** | Section Break | | | |
| `extract_reference_number` | Check | 1 | No | Extract reference/check numbers from transaction descriptions. Default: enabled. When enabled, uses custom regex if configured, otherwise uses built-in patterns (see Section 5.6). |
| `custom_reference_regex` | Small Text | | No | Custom regex pattern for extracting reference numbers. Must contain one capture group. Example: `(?:REF|CONF)\s*#?\s*(\w+)`. Leave blank to use built-in patterns. |
| `extract_party_name` | Check | 1 | No | Extract party/merchant names from transaction descriptions. Default: enabled. When enabled, uses custom regex if configured, otherwise uses built-in patterns (see Section 5.6). |
| `custom_party_regex` | Small Text | | No | Custom regex pattern for extracting party names. Must contain one capture group that captures the party name portion. Example: `^ACH\s+\w+\s*[-:]\s*(.+)`. Leave blank to use built-in patterns. |
| **Notification Settings** | Section Break | | | |
| `on_sync_failure` | Select | Log Only/Email/System Notification | Yes | Default: System Notification |
| `on_empty_account` | Select | Log Only/Email/System Notification | Yes | Default: Log Only |
| `on_record_mismatch` | Select | Log Only/Email/System Notification | Yes | Default: System Notification |
| `notification_recipients` | Small Text | | No | Comma-separated email addresses (used when Email is selected) |
| **Timezone** | Section Break | | | |
| `transaction_timezone` | Select | (all pytz timezones) | Yes | Timezone for converting UNIX epoch → ERPNext Date. Default: site timezone |
| **Account Mappings** | Section Break | | | |
| `account_mappings` | Table | SimpleFIN Account Mapping | No | Child table mapping SimpleFIN accounts → ERPNext Bank Accounts |
| **Status (Read-Only)** | Section Break | | | |
| `sync_state` | Select | Idle/Queued/Syncing/Retry Pending/Failed | No | Current sync state machine state (read-only). Default: Idle |
| `last_sync_attempt` | Datetime | | No | Timestamp of last sync attempt |
| `last_sync_status` | Select | Success/Failed/Never Synced | No | |
| `last_successful_sync` | Datetime | | No | Timestamp of last successful sync |
| `last_sync_end_date` | Int | | No | UNIX timestamp: the `end-date` from the last successful data pull |
| `last_sync_error` | Small Text | | No | Last error message (if failed) |
| `retry_attempts_used` | Int | 0 | No | How many retries have been consumed in the current retry cycle (read-only). Reset to 0 on successful sync or when a new regular interval begins. |
| `next_retry_at` | Datetime | | No | When the next retry is scheduled (read-only). Null when not in retry cycle. |
| `rate_limit_paused_until` | Datetime | | No | If set, all syncs (scheduled, retry, and manual) are blocked until this datetime. Set when SimpleFIN returns a rate limit warning. Auto-clears when the pause expires. |
| `connection_status` | Select | Active/Revoked/Payment Required/Rate Limited/Unknown | No | Derived from last API response |

**Workflow / Validation Rules:**
- `enabled` cannot be set to `1` unless `is_registered == 1`.
- `setup_token` field triggers the exchange process on save (if `is_registered == 0` and `setup_token` is provided).
- After successful exchange, `setup_token` is cleared, `access_url` is stored, `is_registered` is set to 1.
- If exchange returns 403, show error: "This setup token has already been used or is compromised. Please generate a new token from SimpleFIN Bridge."

**Dashboard Indicators:**
- Registration: Green "Registered" / Red "Unregistered"
- Active: Green "Active" / Grey "Disabled"
- Last Sync: Green if within `sync_frequency + (retry_count × retry_interval)` window, Red if overdue, Grey if "Never Synced"
- Rate Limited: Orange "Rate Limited until {time}" when `rate_limit_paused_until` is set and in the future

**Buttons:**
- **Register** (visible when `is_registered == 0` and `setup_token` has a value): Triggers token exchange.
- **Test Connection** (visible when `is_registered == 1`): Calls `/accounts?balances-only=1`, displays account names/balances or error. Minimal rate-limit impact. **Blocked when rate-limit paused** — show message explaining why.
- **Sync Now** (visible when `enabled == 1`): Triggers immediate sync via background job. **Blocked when rate-limit paused** — show message with pause expiry time.
- **Clear Rate Limit Pause** (visible when `rate_limit_paused_until` is set, requires System Manager): Manual override to clear the pause early. Use with caution — forcing syncs against a rate-limited API risks token disabling.

### 3.2 SimpleFIN Account Mapping (Child DocType)

**Parent:** SimpleFIN Connection

| Field | Type | Options | Required | Description |
|---|---|---|---|---|
| `simplefin_account_id` | Data | | Yes | Opaque identifier assigned by SimpleFIN Bridge (not the bank account number) |
| `simplefin_account_name` | Data | | No | Human-readable name from SimpleFIN Bridge (e.g., "Business Checking"), derived from bank's account label |
| `simplefin_org_domain` | Data | | No | Institution domain from SimpleFIN `org.domain` (e.g., "becu.org") |
| `simplefin_org_name` | Data | | No | Institution name from SimpleFIN `org.name` (e.g., "BECU") |
| `simplefin_currency` | Data | | No | Account currency from SimpleFIN (ISO 4217 or custom currency URL) |
| `erpnext_bank_account` | Link | Bank Account | No | ERPNext Bank Account to import transactions into |
| `is_active` | Check | 1 | No | Whether to sync this account (default: active) |
| `missing_from_simplefin` | Check | 0 | No | Set when a previously seen account stops appearing in SimpleFIN responses |
| `first_seen` | Datetime | | No | When this account first appeared from SimpleFIN |
| `last_seen` | Datetime | | No | When this account last appeared in a SimpleFIN response |

**Data origin:** Both `simplefin_account_id` and `simplefin_account_name` originate from the **SimpleFIN Bridge**, not directly from the bank. The Bridge acts as an aggregation layer between the bank and our app. The `id` is an opaque, sanitized identifier generated by the Bridge — it is explicitly designed to not reveal sensitive bank credentials or account numbers. The `name` is the bank's account label (e.g., "Business Checking") as surfaced through the Bridge. The `org` fields identify the financial institution as the Bridge sees it.

**Mapping stability caveat:** Because the `id` is assigned by SimpleFIN Bridge (not the bank), it could theoretically change if:
- The user disconnects and reconnects their bank on the SimpleFIN Bridge website
- SimpleFIN changes their aggregation backend for an institution
- The bank restructures accounts

If a previously mapped `simplefin_account_id` disappears and a new unknown `id` appears with a similar `name` and `org`, the app should flag this prominently rather than silently creating a new unmapped row. The user likely needs to re-map rather than start fresh.

**Behavior:**
- When a sync discovers SimpleFIN accounts not yet in the mapping table, they are auto-added with `erpnext_bank_account` blank and `is_active = 0`. The `first_seen` and `last_seen` timestamps are set.
- On each sync, `last_seen` is updated for every account present in the response.
- Transactions are only imported for rows where `erpnext_bank_account` is set and `is_active = 1`.
- Previously mapped accounts that disappear from SimpleFIN: set `missing_from_simplefin = 1`, log a warning, trigger `on_sync_failure` notification with details. Do NOT delete the mapping row — the account may reappear.
- **Possible remapping detection:** When a new `simplefin_account_id` appears in the same sync response where an existing mapped account has gone missing, AND the new account's `name`, `org_domain`, and `currency` match the missing account, log a prominent warning: "Account '{name}' may have been reassigned a new ID by SimpleFIN. Please verify mappings." This helps users recover from ID changes without silently losing their mapping.
- A "Refresh Accounts" button fetches the current account list from SimpleFIN (using `balances-only=1` to minimize data transfer) and updates the table: refreshes names, adds new accounts, flags missing ones, detects possible remappings.

### 3.3 SimpleFIN Sync Log (DocType)

**Type:** Normal DocType
**Naming:** `SFIN-LOG-{connection_name}-{timestamp}` or autoname
**Module:** SimpleFIN Sync

| Field | Type | Options | Required | Description |
|---|---|---|---|---|
| `connection` | Link | SimpleFIN Connection | Yes | Which connection this log belongs to |
| `sync_type` | Select | Scheduled/Manual/Test | Yes | How the sync was triggered |
| `started_at` | Datetime | | Yes | When the sync started |
| `completed_at` | Datetime | | No | When the sync finished |
| `status` | Select | Success/Partial Success/Failed/In Progress | Yes | Outcome |
| `request_start_date` | Datetime | | No | Overall requested `start-date` (earliest chunk start) |
| `request_end_date` | Datetime | | No | Overall requested `end-date` (latest chunk end) |
| `actual_data_end_date` | Datetime | | No | End date of the last chunk that actually returned data |
| `chunks_requested` | Int | | No | Total ≤90-day chunks planned (1 if no splitting) |
| `chunks_completed` | Int | | No | Chunks actually executed (may be less if rate-limited) |
| `chunks_empty` | Int | | No | Chunks that returned zero transactions (institution history gap) |
| `accounts_retrieved` | Int | | No | Number of SimpleFIN accounts in response |
| `transactions_retrieved` | Int | | No | Total transactions returned by SimpleFIN (across all chunks) |
| `transactions_created` | Int | | No | New Bank Transactions created |
| `transactions_skipped_duplicate` | Int | | No | Skipped due to deduplication |
| `transactions_skipped_pending` | Int | | No | Skipped because pending |
| `transactions_skipped_cancelled` | Int | | No | Skipped because user cancelled them (docstatus=2) |
| `transactions_mismatched` | Int | | No | Same ID but different data (flagged) |
| `rate_limit_warning_received` | Check | | No | Whether SimpleFIN returned a rate limit warning during this sync |
| `simplefin_errors` | Small Text | | No | Contents of the `errors` array from SimpleFIN (across all chunks) |
| `error_message` | Long Text | | No | System error / traceback if sync failed |
| `balance_snapshot` | Table | SimpleFIN Balance Snapshot | No | Balance data captured during this sync |

**Auto-Cleanup:** Configurable retention period in SimpleFIN Sync Settings. Default: 90 days. A daily scheduled task prunes old logs.

### 3.4 SimpleFIN Balance Snapshot (Child DocType)

**Parent:** SimpleFIN Sync Log

| Field | Type | Options | Required | Description |
|---|---|---|---|---|
| `simplefin_account_id` | Data | | Yes | SimpleFIN account ID |
| `simplefin_account_name` | Data | | No | SimpleFIN account name |
| `currency` | Data | | No | ISO 4217 or custom currency URL |
| `balance` | Currency | | No | Account balance at `balance_date` |
| `available_balance` | Currency | | No | Available balance at `balance_date` |
| `balance_date` | Datetime | | No | When the balance was recorded (from UNIX `balance-date`) |

### 3.5 SimpleFIN Sync Settings (Single DocType)

**Type:** Single DocType
**Module:** SimpleFIN Sync

| Field | Type | Options/Default | Required | Description |
|---|---|---|---|---|
| `log_retention_days` | Int | 90 | No | Days to keep sync logs before auto-cleanup |
| `default_sync_frequency` | Select | Daily | No | Default for new connections |
| `enable_detailed_logging` | Check | 0 | No | Log raw API responses for debugging (disable in production) |

---

## 4. Custom Fields on Existing DocTypes

### 4.1 Bank Transaction (Custom Fields)

| Field | Type | Insert After | Description |
|---|---|---|---|
| `simplefin_transaction_id` | Data | `description` | SimpleFIN Bridge transaction `id` — opaque, unique within account; used as dedup key |
| `simplefin_connection` | Link (SimpleFIN Connection) | `simplefin_transaction_id` | Which connection imported this |
| `simplefin_account_id` | Data | `simplefin_connection` | SimpleFIN Bridge account `id` (opaque, not bank account number) |
| `simplefin_posted_at` | Datetime | `simplefin_account_id` | Original `posted` timestamp (full datetime) |
| `simplefin_transacted_at` | Datetime | `simplefin_posted_at` | Original `transacted_at` timestamp if present |
| `simplefin_raw_amount` | Data | `simplefin_transacted_at` | Original amount string from SimpleFIN |
| `simplefin_pending` | Check | `simplefin_raw_amount` | Whether the transaction was pending when imported |
| `simplefin_last_seen` | Datetime | `simplefin_pending` | Last time this transaction appeared in a SimpleFIN sync response |

**Index:** Create a database index on `(simplefin_account_id, simplefin_transaction_id)` for fast dedup lookups.

**Rejecting unwanted transactions:** Users reject unwanted SimpleFIN-imported transactions using ERPNext's native **Cancel** action on the Bank Transaction form. Cancelled records (docstatus=2) are automatically hidden from the Bank Reconciliation Tool by Frappe's framework. Our dedup logic checks all docstatuses including cancelled, so a cancelled transaction will not be re-imported by future syncs while it remains in the database.

**Recovery from accidental cancellation (manual procedure, documented in README):**
1. Delete the cancelled Bank Transaction document (requires Accounts Manager or System Manager permission).
2. On the next scheduled sync, if the transaction is still within the rolling window, dedup will find no match and re-import it as a fresh Bank Transaction.
3. If the transaction has aged out of the rolling window, run a manual "Sync Now" from the SimpleFIN Connection form.

---

## 5. Transaction Import Logic

### 5.1 Sync Flow (per Connection)

```
1. Validate connection: is_registered=1, enabled=1, access_url present
   Check rate_limit_paused_until — if set and in the future, abort immediately
   (log "Sync skipped: rate limit pause active until {time}")
   Transition sync_state: Queued → Syncing (see Section 6.4)
2. Create SimpleFIN Sync Log (status=In Progress)
3. Calculate date range:
   a. If last_sync_end_date exists:
      start_date = last_sync_end_date - rolling_window_days
   b. If no last_sync_end_date (first sync or re-enable):
      start_date = now - initial_history_days
   c. end_date = now
   d. If (end_date - start_date) > 90 days: split into ≤90-day chunks (see Section 5.4)
4. For each chunk (or single range if no splitting needed):
   a. Build request URL:
      {access_url_base}/accounts?start-date={chunk_start_ts}&end-date={chunk_end_ts}
      Add &account={id} for each active mapped account (optimizes rate limits)
      Do NOT add &pending=1 (unless include_pending is checked — v1 default: off)
   b. Check rate limit budget before request — abort remaining chunks if near limit (Section 5.4)
   c. Make HTTPS GET with Basic Auth (parsed from Access URL)
   d. Handle response:
      - HTTP 200 → parse JSON, process accounts (continue to step 5)
      - HTTP 403 → mark connection_status=Revoked, notify, log, abort all chunks
      - HTTP 402 → mark connection_status=Payment Required, notify, log, abort all chunks
      - Any other error → log, schedule retry if retries remain, abort remaining chunks
   e. Check errors array → log all, notify if non-empty
      **RATE LIMIT DETECTION:** Scan the errors array for rate limit warnings
      (see Section 5.5). If detected:
      - Set rate_limit_paused_until = end of current UTC day + buffer
      - Set connection_status = "Rate Limited"
      - Log prominently, trigger on_sync_failure notification
      - Process the data from THIS response (it's still valid), then abort remaining chunks
   f. Stop conditions (see Section 5.4):
      - Zero transactions returned → bank has no history this far back, stop walking back
      - All transactions are duplicates → reached previously imported territory, stop
      - Rate limit budget exhausted → stop, log, older history deferred
      - Rate limit warning detected in errors → stop after processing this chunk
5. For each account in response (per chunk):
   a. Store balance snapshot in sync log
   b. Update org info on connection (if first sync)
   c. Look up account mapping → skip if unmapped or inactive
   d. For each transaction:
      i.   Skip if pending=true (unless include_pending enabled)
      ii.  Dedup check: query Bank Transaction where
           simplefin_account_id={acct_id} AND simplefin_transaction_id={txn_id}
           across ALL docstatuses (0=Draft, 1=Submitted, 2=Cancelled)
      iii. If exists (any state):
           - Update simplefin_last_seen = now() on the existing record
           - If docstatus=2 (Cancelled) → skip (user cancelled it, still tracking)
           - Otherwise → compare date, description, amount:
             · If match → skip (already imported)
             · If mismatch → log as mismatch, trigger on_record_mismatch alert, do NOT overwrite
      iv.  If not exists → create Bank Transaction (with unallocated_amount set!)
6. Update connection: last_sync_attempt, last_sync_status, last_successful_sync
   Update last_sync_end_date to end_date of the FIRST (most recent) chunk — always correct
   with newest-first ordering since the forward edge is always captured.
   Transition sync_state per Section 6.4: Success → Idle · Failure → Retry Pending or Failed
7. Finalize sync log: status, counts, completed_at, chunks_requested/completed/empty
```

### 5.2 Bank Transaction Creation

Map SimpleFIN fields to ERPNext `Bank Transaction` fields:

| Bank Transaction Field | Source |
|---|---|
| `date` | Convert `posted` (UNIX epoch) → date using connection's `transaction_timezone` |
| `bank_account` | From account mapping (`erpnext_bank_account`) |
| `description` | SimpleFIN `description` (sanitized) |
| `deposit` | `amount` if positive (as absolute float) |
| `withdrawal` | `amount` if negative (as absolute float) |
| `currency` | SimpleFIN `currency` (if standard ISO 4217) |
| `simplefin_transaction_id` | SimpleFIN `id` |
| `simplefin_connection` | Connection docname |
| `simplefin_account_id` | SimpleFIN account `id` |
| `simplefin_posted_at` | `posted` as datetime (UTC) |
| `simplefin_transacted_at` | `transacted_at` as datetime (UTC) if present |
| `simplefin_raw_amount` | Original `amount` string |
| `reference_number` | Best-effort extraction from SimpleFIN `extra` or `description` (see Section 5.6) |
| `bank_party_name` | Best-effort extraction from SimpleFIN `description` (see Section 5.6) |
| `status` | `"Unreconciled"` — required for visibility in Bank Reconciliation Tool |
| `docstatus` | `1` (Submitted) — Bank Transaction is a submittable doctype in ERPNext v16. Records must be submitted to appear in the Bank Reconciliation Tool and to be cancellable. |
| `unallocated_amount` | Same as `deposit` or `withdrawal` (see critical note below) |
| `allocated_amount` | `0` (explicitly set — see critical note below) |

**Notes:**
- **Bank Transaction is submittable.** In ERPNext v16, Bank Transaction has `docstatus` support. New records must be created and submitted (`docstatus = 1`) to be visible in the Bank Reconciliation Tool and to allow the user to Cancel them later. Use `bt.insert(ignore_permissions=True)` followed by `bt.submit()`, or set `docstatus = 1` before insert.

- **Rejecting unwanted transactions:** Users Cancel unwanted transactions directly from the Bank Transaction list or form. This sets `docstatus = 2`, which hides the record from the Bank Reconciliation Tool and prevents re-import via dedup (Section 5.3). The full lifecycle is:
  - Created: `docstatus=1`, `status="Unreconciled"` → visible in BRT
  - Reconciled: `docstatus=1`, `status="Reconciled"` → matched to voucher
  - Cancelled: `docstatus=2`, `status="Cancelled"` → hidden from BRT, remains in DB for dedup

- `Bank Transaction.status` values in ERPNext v16 are: Pending, Settled, Unreconciled, Reconciled, Cancelled. New imports **must** be set to `"Unreconciled"` to appear in the Bank Reconciliation Tool. Do not use "Pending" — that has a different meaning in ERPNext (it means the system is waiting on something, not that it's ready for reconciliation). The reconciliation tool handles the transition to "Reconciled" automatically when a user links a voucher.

- **CRITICAL — The "Unallocated Amount" Quirk:** When creating a `Bank Transaction` programmatically, ERPNext does **not** automatically calculate `unallocated_amount`. If you only set `deposit` or `withdrawal`, the Bank Reconciliation Tool will silently hide the transaction because it assumes 100% of funds are already allocated to vouchers. You **must** explicitly set:
  ```python
  bt.unallocated_amount = abs(amount)  # Full transaction amount
  bt.allocated_amount = 0               # Nothing allocated yet
  ```
  Without this, imported transactions will exist in the database but be invisible in the reconciliation workflow.

- The `date` field is a `Date` (not Datetime). The UNIX epoch `posted` timestamp is converted: `datetime.fromtimestamp(posted, tz=connection_timezone).date()`.
- Amounts from SimpleFIN are strings (e.g., `"-150.00"`). Parse with `Decimal` for precision, not `float`.

### 5.3 Deduplication Strategy

**Primary dedup key:** `(simplefin_account_id, simplefin_transaction_id)`

This is checked before every insert. The SimpleFIN spec guarantees that transaction `id` is unique within an account but may be reused across accounts.

**CRITICAL — Check ALL docstatuses:** The dedup query must match against Bank Transactions in **every** docstatus state (0=Draft, 1=Submitted, 2=Cancelled). This prevents re-importing transactions that the user has cancelled via ERPNext's native Cancel action.

```python
existing = frappe.db.sql("""
    SELECT name, date, description, deposit, withdrawal, docstatus
    FROM `tabBank Transaction`
    WHERE simplefin_account_id = %s
      AND simplefin_transaction_id = %s
""", (account_id, transaction_id), as_dict=True)
```

If cancelled records were excluded from the dedup check, the rolling window would endlessly re-import transactions that the user intentionally cancelled.

**`simplefin_last_seen` tracking:** Every time a sync encounters a SimpleFIN transaction that already exists in ERPNext (regardless of its docstatus), the sync updates `simplefin_last_seen = now()` on that record. This tracks whether SimpleFIN is still returning this transaction. It costs one lightweight UPDATE per duplicate, which is negligible.

| Record State | Appears in Reconciliation Tool | Re-imported on Sync | `simplefin_last_seen` Updated |
|---|---|---|---|
| Unreconciled (normal) | Yes | No (dedup hit) | Yes |
| Reconciled | No (already matched to voucher) | No (dedup hit) | Yes |
| Cancelled (docstatus=2) | No — ERPNext hides cancelled docs | No (dedup hit) | Yes |

**Mismatch detection:** When a duplicate ID is found on a non-cancelled record but the `date`, `description`, or `amount` differ from the stored values, this is logged as a mismatch. The existing record is NOT modified. The mismatch is recorded in the sync log and triggers the `on_record_mismatch` notification. Mismatch detection is skipped for cancelled records since the user has already dealt with them.

**Rolling window rationale:** Banks occasionally backdate transactions (posting date adjusted after initial appearance). The 14-day default overlap ensures these are caught by dedup rather than missed entirely.

### 5.4 Date Range Splitting

**When this applies:**
- **Initial sync:** User sets `initial_history_days` to 180 → date range is 180 days → split into two 90-day chunks.
- **Re-enabled connection:** Connection was disabled for 5 months → gap since `last_sync_end_date` is ~150 days + rolling window → split into chunks.
- **Normal ongoing sync:** Range is typically 15–45 days → no splitting needed (single request).

**Chunk strategy — newest-first with smart stop:**

```python
def build_chunks(start_date, end_date, max_days=90):
    """Split a date range into ≤90-day chunks, newest first.

    Example: 180-day range (Jan 1 → Jun 30) produces:
      Chunk 1: Apr 1 → Jun 30  (most recent)
      Chunk 2: Jan 1 → Apr 1   (oldest)
    """
    chunks = []
    current_end = end_date
    while current_end > start_date:
        chunk_start = max(current_end - timedelta(days=max_days), start_date)
        chunks.append((chunk_start, current_end))
        current_end = chunk_start
    return chunks  # Ordered newest → oldest


def sync_with_chunks(connection, chunks, mapped_account_ids):
    """Process chunks newest-first. Stop walking back when no new data remains."""
    total_created = 0
    total_retrieved = 0

    for i, (chunk_start, chunk_end) in enumerate(chunks):
        # Rate limit check before each request
        if approaching_rate_limit(connection, chunks_remaining=len(chunks) - i):
            log.warning(f"Approaching SimpleFIN rate limit. "
                        f"Processed {i}/{len(chunks)} chunks. "
                        f"Older history deferred.")
            break

        result = fetch_and_import(connection, chunk_start, chunk_end, mapped_account_ids)
        total_created += result.transactions_created
        total_retrieved += result.transactions_retrieved

        # Stop conditions — no value in going further back
        if result.transactions_retrieved == 0:
            # Bank has no history this far back. Stop.
            log.info(f"Chunk {i+1}: no data returned. "
                     f"Institution has no history before {chunk_end}. "
                     f"Stopping backfill.")
            break

        if result.transactions_created == 0 and result.transactions_skipped_duplicate > 0:
            # Every transaction in this chunk was already imported.
            # We've reached previously synced territory. Stop.
            log.info(f"Chunk {i+1}: all {result.transactions_skipped_duplicate} "
                     f"transactions already imported. Backfill complete.")
            break

    return total_created, total_retrieved
```

**Why newest-first:**

1. **Most valuable data arrives first.** When re-enabling a connection after months, last week's transactions matter far more than those from 5 months ago. The user can begin reconciliation immediately, even if the full backfill hasn't finished.

2. **Rate limit interruption is graceful.** If the budget runs out after 2 of 4 chunks, newest-first means you have the most recent 180 days. Oldest-first would give you days 360–180 with the most recent data missing — useless for active reconciliation.

3. **Two clean stop conditions.** Walking backward, the app stops when:
   - A chunk returns **zero transactions** → the bank has no more history. No point requesting even older data.
   - A chunk returns **100% duplicates** → we've reached previously imported territory. Everything older is already in ERPNext.

   Either condition means there's no new data to be found by going further back. Oldest-first cannot detect the "already imported" boundary without completing all chunks through the overlap zone.

4. **The rolling window ensures continuity.** The next scheduled sync's rolling window reaches back from the most recent data. There's no forward edge to protect — only the backward edge matters, and that's where we stop naturally.

**Rate limit awareness:** SimpleFIN allows ≤24 requests/day per access token. Each chunk is one request (or one per mapped account if using per-account filtering). Before each chunk, calculate:

```
requests_used_this_sync = chunks_processed × num_mapped_accounts
estimated_total = total_chunks × num_mapped_accounts
```

If the estimate approaches 24 (accounting for other syncs that may run today), stop and log. The remaining older history is low-priority — if the user really needs it, they can run another manual sync tomorrow when quota resets.

| Scenario | Chunks | Accounts | Requests | Within limit? |
|---|---|---|---|---|
| Normal daily sync | 1 | 3 | 3 | Yes (3/24) |
| 180-day initial sync | 2 | 3 | 6 | Yes (6/24) |
| 365-day initial sync | 5 | 3 | 15 | Yes but tight (15/24) |
| 365-day initial sync | 5 | 6 | 30 | **Exceeds** — stops after most recent chunks |

**Sync log reporting for chunked syncs:** The sync log records `request_start_date` and `request_end_date` as the *overall* requested range (not per-chunk). Additional detail is captured:
- `chunks_requested`: total number of chunks planned
- `chunks_completed`: how many were actually executed
- `chunks_empty`: how many returned zero transactions (bank history boundary)
- If early termination occurred, `error_message` notes the reason (rate limit, all duplicates, or no data)

**`last_sync_end_date` behavior:** Always updated to `end_date` of the most recent chunk (which is always processed first in newest-first order). This is correct because the forward edge of the data is always captured. What varies is how far *back* we reached, which the sync log documents but doesn't affect next-sync scheduling.

### 5.5 Rate Limit Detection and Pause

SimpleFIN Bridge enforces daily request quotas per access token. The Bridge communicates rate limit status through the `errors` array in `/accounts` responses — warnings appear before enforcement. The three tiers (per SimpleFIN documentation):

| Tier | ~Requests/day | SimpleFIN Behavior | Our Response |
|---|---|---|---|
| Normal | ≤24 | No warnings | Continue normally |
| Warning | ~25–48 | Warning messages in `errors` array | **Pause connection until next day** |
| Abuse | ~49+ | Access token disabled | Connection becomes Revoked (403) |

**The goal is to never reach the abuse tier.** By pausing at the first warning, we protect the access token from being disabled — which would require the user to generate a new setup token and re-register.

**Detection:** After every `/accounts` response, scan the `errors` array for rate limit indicators. SimpleFIN doesn't document exact warning strings, so match conservatively:

```python
RATE_LIMIT_PATTERNS = [
    "rate", "limit", "quota", "throttle", "too many requests",
    "slow down", "exceeded"
]

def contains_rate_limit_warning(errors: list[str]) -> bool:
    """Check if any error message suggests rate limiting."""
    for error in errors:
        error_lower = error.lower()
        if any(pattern in error_lower for pattern in RATE_LIMIT_PATTERNS):
            return True
    return False
```

**Pause behavior:** When a rate limit warning is detected:

1. Calculate pause expiry: `end of current UTC day + 1 hour buffer` (SimpleFIN replenishes quotas throughout the day, so a conservative buffer avoids hitting the limit again immediately).
   ```python
   from datetime import datetime, timedelta
   tomorrow_start = (datetime.utcnow() + timedelta(days=1)).replace(
       hour=0, minute=0, second=0, microsecond=0
   )
   pause_until = tomorrow_start + timedelta(hours=1)  # 01:00 UTC next day
   ```

2. Set `rate_limit_paused_until = pause_until` on the connection.
3. Set `connection_status = "Rate Limited"`.
4. Log the warning in the sync log (`simplefin_errors` field).
5. Trigger `on_sync_failure` notification: "SimpleFIN rate limit warning detected. Syncs paused until {pause_until}. The access token is safe — pausing prevents escalation to token disabling."
6. **Process the current response data** — the response is still valid even though it included a warning. Import any transactions, store balance snapshots.
7. Abort any remaining chunks — do not make further requests today.

**Automatic resume:** The pause is time-based. Once `rate_limit_paused_until` is in the past:
- The scheduler treats the connection as normal again.
- `connection_status` is reset to "Active" on the next successful sync.
- `rate_limit_paused_until` is cleared.
- No manual intervention required.

**Manual override:** A "Clear Rate Limit Pause" button (System Manager only) allows early clearing. This exists for cases where the user knows the quota has reset (e.g., SimpleFIN documentation changes the replenishment schedule). Use with caution.

**Interaction with other sync states:** Rate limit pause takes priority over all sync scheduling:
- Scheduler: connections with active pause are skipped entirely (same as Queued/Syncing).
- Retries: paused, not lost — the retry cycle resumes after the pause expires.
- Manual "Sync Now": blocked with a message showing the pause expiry time.
- "Test Connection": blocked — even balances-only requests count against the quota.

### 5.6 Transaction Data Enrichment

SimpleFIN provides a `description` string and an optional `extra` object for each transaction. Unlike Plaid, SimpleFIN does not provide structured fields for merchant/party names or reference numbers. However, we can extract useful data from what's available to improve ERPNext's automatic party matching and reconciliation matching.

**Enrichment is configurable per connection.** Each SimpleFIN Connection has four fields that control extraction behavior:
- `extract_reference_number` (Check) — enables/disables reference number extraction. Default: on.
- `custom_reference_regex` (Small Text) — optional custom regex. If set, overrides built-in patterns.
- `extract_party_name` (Check) — enables/disables party name extraction. Default: on.
- `custom_party_regex` (Small Text) — optional custom regex. If set, overrides built-in patterns.

**Dispatch logic (called during sync for each transaction):**

```python
def enrich_transaction(description: str, extra: dict | None, connection) -> dict:
    """Extract reference_number and bank_party_name based on connection settings.

    Returns a dict with keys to set on the Bank Transaction.
    Values are None when extraction is disabled or yields no result.
    """
    result = {"reference_number": None, "bank_party_name": None}

    if connection.extract_reference_number:
        if connection.custom_reference_regex:
            result["reference_number"] = apply_custom_regex(
                connection.custom_reference_regex, description
            )
        else:
            result["reference_number"] = extract_reference_number(description, extra)

    if connection.extract_party_name:
        if connection.custom_party_regex:
            result["bank_party_name"] = apply_custom_regex(
                connection.custom_party_regex, description
            )
        else:
            result["bank_party_name"] = extract_party_name(description)

    return result


def apply_custom_regex(pattern: str, description: str) -> str | None:
    """Apply a user-provided regex pattern to extract a value.

    The pattern must contain exactly one capture group.
    Returns the captured group or None if no match.
    """
    if not description or not pattern:
        return None
    try:
        match = re.search(pattern, description, re.IGNORECASE)
        if match and match.group(1):
            return match.group(1).strip()
    except re.error:
        frappe.logger().warning(
            f"Invalid custom regex pattern: {pattern}"
        )
    return None
```

**Custom regex validation:** On save of a SimpleFIN Connection, if `custom_reference_regex` or `custom_party_regex` is set, validate that it compiles and contains exactly one capture group. Reject with a clear error if not.

```python
def validate_custom_regex(pattern: str, field_label: str):
    """Validate a custom regex pattern on SimpleFIN Connection save."""
    if not pattern:
        return
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        frappe.throw(_("Invalid regex in {0}: {1}").format(field_label, str(e)))
    if compiled.groups != 1:
        frappe.throw(_(
            "{0} must contain exactly one capture group. Found {1}."
        ).format(field_label, compiled.groups))
```

Below are the **built-in extraction patterns** used as defaults when no custom regex is configured.

**Reference Number Extraction (`reference_number`):**

The ERPNext Bank Reconciliation Tool uses `reference_number` to match transactions against Payment Entries, Journal Entries, and other vouchers. Populating this field significantly improves auto-matching accuracy.

Extraction priority (first match wins):

1. **SimpleFIN `extra` object:** Some banks include structured data in the `extra` field. Look for keys like `check_number`, `reference`, `ref`, `trace_number`, `confirmation`, or `transaction_number`. If found, use the value.
2. **Check number pattern in description:** Match patterns like `Check #1234`, `CHK 1234`, `Check Number: 1234`, `CK#1234`. Extract the numeric portion.
3. **Reference/confirmation pattern in description:** Match patterns like `Ref: ABC123`, `Conf#12345`, `Trace: 987654`. Extract the alphanumeric code.
4. **If no match:** Leave `reference_number` empty. Do not force a value — a bad reference number is worse than none.

```python
import re

def extract_reference_number(description: str, extra: dict | None) -> str | None:
    """Extract a reference/check number from SimpleFIN transaction data."""
    # 1. Check extra object for structured reference data
    if extra:
        for key in ("check_number", "reference", "ref", "trace_number",
                     "confirmation", "transaction_number", "reference_number"):
            if key in extra and extra[key]:
                return str(extra[key]).strip()

    if not description:
        return None

    # 2. Check number patterns
    check_match = re.search(
        r'(?:check|chk|ck)\s*#?\s*(\d{3,})',
        description, re.IGNORECASE
    )
    if check_match:
        return check_match.group(1)

    # 3. Reference/confirmation patterns
    ref_match = re.search(
        r'(?:ref|conf|confirmation|trace|trans)\s*[#:]\s*([A-Za-z0-9]{4,})',
        description, re.IGNORECASE
    )
    if ref_match:
        return ref_match.group(1)

    return None
```

**Party Name Extraction (`bank_party_name`):**

The ERPNext Bank Transaction doctype has a `bank_party_name` field (labeled "Party Name/Account Holder (Bank Statement)"). When populated, ERPNext's Automatic Party Matching feature (Accounts Settings > Banking > Enable Automatic Party Matching) uses this field to fuzzy-match against Customers, Suppliers, and Employees. This is the same field that Plaid populates with merchant data.

SimpleFIN only provides a raw `description` string, so extraction is best-effort. Common bank description formats:

```
"ACH Payment - Acme Corporation"       → "Acme Corporation"
"POS Purchase COSTCO #1234"            → "COSTCO"
"Wire Transfer from Jane Smith"        → "Jane Smith"
"Direct Deposit - EMPLOYER INC"        → "EMPLOYER INC"
"DEBIT CARD PURCHASE - AMAZON.COM"     → "AMAZON.COM"
"Check #1042"                          → None (no party info)
"Interest Payment"                     → None (no party info)
```

Extraction logic:

```python
import re

# Prefixes that precede the party name (stripped to get party)
DESCRIPTION_PREFIXES = [
    r"ACH (?:Payment|Deposit|Debit|Credit)\s*[-–—:]\s*",
    r"(?:POS|DEBIT CARD|CREDIT CARD)\s+(?:PURCHASE|PAYMENT|REFUND)\s*[-–—:]?\s*",
    r"Wire (?:Transfer|Payment)\s+(?:from|to)\s+",
    r"Direct (?:Deposit|Debit)\s*[-–—:]\s*",
    r"(?:ONLINE|MOBILE|BILL)\s+(?:PAYMENT|TRANSFER)\s*[-–—:]\s*",
    r"ZELLE\s+(?:TO|FROM)\s+",
    r"VENMO\s+(?:CASHOUT|PAYMENT)\s+",
]

# Suffixes to strip from the end (location codes, store numbers, dates)
DESCRIPTION_SUFFIXES = [
    r"\s+#\d+.*$",          # Store numbers: "COSTCO #1234 SEATTLE WA"
    r"\s+\d{2}/\d{2}$",    # Trailing dates: "MERCHANT 03/15"
    r"\s+[A-Z]{2}\s+\d{5}$",  # State + ZIP: "MERCHANT WA 98101"
    r"\s+\d{10,}$",        # Long trailing numbers (trace IDs)
]

# Descriptions that contain no party info
NO_PARTY_PATTERNS = [
    r"^(?:Interest|Dividend|Fee|Charge|Overdraft|ATM|Tax)",
    r"^Check\s*#?\d+$",
    r"^(?:Service Charge|Monthly Maintenance|Wire Fee)",
]

def extract_party_name(description: str) -> str | None:
    """Best-effort extraction of party name from bank description."""
    if not description:
        return None

    # Skip descriptions with no party information
    for pattern in NO_PARTY_PATTERNS:
        if re.match(pattern, description, re.IGNORECASE):
            return None

    result = description

    # Strip known prefixes
    for prefix in DESCRIPTION_PREFIXES:
        result = re.sub(f"^{prefix}", "", result, flags=re.IGNORECASE)

    # Strip known suffixes
    for suffix in DESCRIPTION_SUFFIXES:
        result = re.sub(suffix, "", result, flags=re.IGNORECASE)

    result = result.strip()

    # If nothing meaningful remains, return None
    if not result or len(result) < 3:
        return None

    # Title-case for readability unless already mixed case
    if result == result.upper() and len(result) > 3:
        result = result.title()

    return result
```

**Important caveats:**
- **Confirmed field names (verified on ERPNext v16, Frappe Cloud):**
  - `reference_number` — correct fieldname, label "Reference Number". Has `allow_on_submit = 1` in stock ERPNext, so users can edit it on submitted Bank Transactions.
  - `bank_party_name` — correct fieldname, label "Party Name/Account Holder (Bank Statement)". Has `allow_on_submit = 0` in stock ERPNext — users cannot edit this field after submission. This is acceptable because `bank_party_name` is an input to ERPNext's Automatic Party Matching, not the final party assignment. If the extracted name is wrong or matching picks the wrong party, users correct the result directly via the `party_type` and `party` fields, which are editable on submitted transactions.
- These are heuristic parsers. They will not be correct 100% of the time. A wrong `bank_party_name` is better than a wrong `reference_number` (party matching is fuzzy, reference matching is exact), which is why we're more conservative with reference extraction.
- Both extraction functions should be unit-tested against a corpus of real bank descriptions from the BECU Business account during development.
- The extraction patterns are US bank-centric. International bank description formats may differ significantly.
- If either function returns `None`, the corresponding Bank Transaction field is left empty — this is safe and allows manual entry by the user during reconciliation.

---

## 6. Scheduled Tasks

### 6.1 Sync Scheduler

**Hook:** `scheduler_events` in `hooks.py`

```python
scheduler_events = {
    "all": [
        "simplefin_sync.tasks.check_due_syncs"
    ],
    "daily": [
        "simplefin_sync.tasks.cleanup_old_sync_logs"
    ]
}
```

### 6.2 Sync State Machine

Each SimpleFIN Connection has a `sync_state` field that governs scheduling decisions. This prevents retries from bleeding into the next regular interval and prevents concurrent sync jobs.

```
                    ┌──────────────────────────────────────┐
                    │                                      │
                    ▼                                      │
               ┌────────┐   regular interval due      ┌───┴────────┐
               │  Idle   │ ──────────────────────────► │  Queued    │
               └────────┘                              └─────┬──────┘
                    ▲                                        │
                    │                                   job starts
                    │                                        │
                    │                                        ▼
                    │  success                          ┌─────────┐
                    ├──────────────────────────────────│ Syncing  │
                    │                                  └────┬─────┘
                    │                                       │
                    │                              failure  │
                    │                                       ▼
                    │                              ┌──────────────┐
                    │     retries exhausted         │Retry Pending │◄──┐
                    │◄─────────────────────────────│              │   │
                    │                              └──────┬───────┘   │
                    │                                     │           │
                    │                          retry fires│  failure  │
                    │                                     ▼    +      │
                    │  success                     ┌──────────┐retries│
                    ├──────────────────────────────│ Syncing  │remain │
                    │                              └──────────┘───────┘
                    │
                    │  retries exhausted
               ┌────┴───┐
               │ Failed  │ (waits for next regular interval)
               └────┬────┘
                    │     next regular interval due
                    └──────────────────► Queued (retry_attempts_used reset to 0)
```

**State definitions:**

| State | Meaning | Transitions |
|---|---|---|
| **Idle** | Waiting for next regular interval | → Queued (when interval is due) |
| **Queued** | Job enqueued, waiting for worker | → Syncing (when job starts) |
| **Syncing** | API call in progress | → Idle (on success) · → Retry Pending (on failure, retries remain) · → Failed (on failure, retries exhausted) · → Failed (stale state recovery, >30 min) |
| **Retry Pending** | Waiting for `retry_interval_minutes` to elapse | → Queued (when retry interval elapses) · → Queued (when regular interval arrives — resets retries) |
| **Failed** | All retries exhausted | → Queued (when next regular interval arrives, resets `retry_attempts_used` to 0) |
| **Queued** | Job enqueued, waiting for worker | → Syncing (when job starts) · → Failed (stale state recovery, >30 min) |

**Stale state recovery:** If a connection has been in "Syncing" or "Queued" for more than 30 minutes, the scheduler assumes the background worker crashed and resets the state to "Failed". This prevents a dead connection from being permanently stuck. The 30-minute threshold is generous — a normal sync completes in seconds to a few minutes.

### 6.3 Scheduler Logic (`check_due_syncs`)

Runs every minute via Frappe's `all` scheduler. For each enabled, registered connection:

```python
def check_due_syncs():
    connections = frappe.get_all("SimpleFIN Connection",
        filters={"enabled": 1, "is_registered": 1},
        fields=["name", "sync_state", "sync_frequency", "sync_time",
                "sync_day_of_week", "sync_day_of_month",
                "last_sync_attempt", "retry_count", "retry_attempts_used",
                "retry_interval_minutes", "next_retry_at",
                "rate_limit_paused_until"]
    )

    for conn in connections:
      try:
        # RATE LIMIT GUARD: if paused, skip entirely — no syncs, no retries
        if conn.rate_limit_paused_until and now() < conn.rate_limit_paused_until:
            continue
        # If pause has expired, clear it (auto-resume)
        if conn.rate_limit_paused_until and now() >= conn.rate_limit_paused_until:
            frappe.db.set_value("SimpleFIN Connection", conn.name,
                {"rate_limit_paused_until": None})

        if conn.sync_state in ("Syncing", "Queued"):
            # CONCURRENCY GUARD: job already in flight — but check for stale state.
            # If last_sync_attempt is >30 min ago and state is still Syncing/Queued,
            # the worker likely crashed. Reset to Failed so next interval picks it up.
            if conn.last_sync_attempt and (now() - conn.last_sync_attempt).total_seconds() > 1800:
                frappe.db.set_value("SimpleFIN Connection", conn.name,
                    {"sync_state": "Failed", "last_sync_status": "Failed",
                     "last_sync_error": "Sync job appears to have crashed (stale state recovery)"})
                frappe.logger().warning(f"SimpleFIN Connection {conn.name}: stale sync state recovered")
            continue

        if conn.sync_state == "Retry Pending":
            # Check if a regular interval has arrived FIRST
            if is_regular_interval_due(conn):
                # Regular interval takes priority — reset retry cycle
                enqueue_sync(conn, reset_retries=True)
            elif conn.next_retry_at and now() >= conn.next_retry_at:
                # Retry interval elapsed — fire retry
                enqueue_sync(conn, reset_retries=False)
            # else: still waiting, do nothing

        elif conn.sync_state in ("Idle", "Failed"):
            if is_regular_interval_due(conn):
                # Normal scheduled sync (Failed → resets retry counter)
                enqueue_sync(conn, reset_retries=True)
            # else: not yet due, do nothing

      except Exception:
        # CONNECTION ISOLATION: an error evaluating one connection must never
        # block scheduling of other connections. Log and continue.
        frappe.logger().error(
            f"SimpleFIN Connection {conn.name}: scheduler evaluation failed",
            exc_info=True
        )


def enqueue_sync(conn, reset_retries):
    if reset_retries:
        frappe.db.set_value("SimpleFIN Connection", conn.name,
            {"retry_attempts_used": 0, "next_retry_at": None})

    frappe.db.set_value("SimpleFIN Connection", conn.name,
        {"sync_state": "Queued"})

    frappe.enqueue(
        "simplefin_sync.utils.sync.run_sync",
        connection=conn.name,
        queue="long",
        deduplicate=True  # Frappe's built-in job dedup by method+args
    )
```

**Key rules enforced by this design:**

1. **No concurrent syncs.** If `sync_state` is "Queued" or "Syncing", the scheduler skips that connection entirely. The `deduplicate=True` flag on `frappe.enqueue` provides a second layer of protection.

2. **Regular interval always wins.** If a connection is in "Retry Pending" and a regular interval arrives, the regular interval takes priority. Retry counter resets to 0, and a fresh sync starts. Retries never bleed past the next regular interval.

3. **Retry window is bounded.** Maximum retry window = `retry_count × retry_interval_minutes`. With defaults (3 retries × 30 min = 90 min), this fits comfortably inside even the shortest regular interval (Every 2 Hours = 120 min), leaving a 30-minute buffer.

4. **Failed state is a dead end until next interval.** Once retries exhaust, the connection sits in "Failed" until the next regular interval — no infinite retry loops.

### 6.4 Sync Job State Transitions

The `run_sync` function (in `utils/sync.py`) manages state transitions:

```python
def run_sync(connection):
    conn = frappe.get_doc("SimpleFIN Connection", connection)
    conn.sync_state = "Syncing"
    conn.last_sync_attempt = now()
    conn.save(ignore_permissions=True)

    try:
        # ... perform sync (Section 5.1) ...
        conn.reload()
        conn.sync_state = "Idle"
        conn.last_sync_status = "Success"
        conn.last_successful_sync = now()
        conn.retry_attempts_used = 0
        conn.next_retry_at = None
        conn.save(ignore_permissions=True)

    except Exception as e:
        conn.reload()
        conn.last_sync_status = "Failed"
        conn.last_sync_error = str(e)
        conn.retry_attempts_used += 1

        if conn.retry_attempts_used < conn.retry_count:
            conn.sync_state = "Retry Pending"
            conn.next_retry_at = now() + timedelta(minutes=conn.retry_interval_minutes)
        else:
            conn.sync_state = "Failed"
            conn.next_retry_at = None
            # Trigger on_sync_failure notification — all retries exhausted

        conn.save(ignore_permissions=True)
```

### 6.5 Validation: Retry Window Must Fit Inside Sync Interval

On save of a SimpleFIN Connection, validate:

```python
max_retry_window = retry_count * retry_interval_minutes  # in minutes
sync_interval = frequency_to_minutes(sync_frequency)     # in minutes

if max_retry_window >= sync_interval:
    frappe.throw(_(
        "Retry window ({0} retries × {1} min = {2} min) must be shorter "
        "than sync interval ({3} min). Reduce retry count or interval."
    ).format(retry_count, retry_interval_minutes, max_retry_window, sync_interval))
```

This prevents misconfiguration at the source. A user cannot save a connection where retries would mathematically overlap with the next regular sync.

### 6.6 Frequency Calculations

**`is_regular_interval_due` logic** varies by frequency type:

| Frequency | Due when... | Uses `sync_time` | Uses `sync_day_of_week` | Uses `sync_day_of_month` |
|---|---|---|---|---|
| Every 2 Hours | ≥120 min since `last_sync_attempt` | — | — | — |
| 4x Daily | ≥360 min since `last_sync_attempt` | — | — | — |
| Twice Daily | ≥720 min since `last_sync_attempt` | — | — | — |
| Daily | Current time ≥ `sync_time` AND no sync today | Yes | — | — |
| Weekly | Current day = `sync_day_of_week` AND time ≥ `sync_time` AND no sync this week | Yes | Yes | — |
| Bi-Weekly | Current day = `sync_day_of_week` AND time ≥ `sync_time` AND ≥14 days since last sync | Yes | Yes | — |
| Monthly | Current day-of-month = `sync_day_of_month` AND time ≥ `sync_time` AND no sync this month | Yes | — | Yes |

**Sub-daily frequencies** (Every 2 Hours, 4x Daily, Twice Daily) are purely interval-based — they fire when enough time has elapsed since the last attempt, regardless of clock time. `sync_time`, `sync_day_of_week`, and `sync_day_of_month` are ignored.

**Daily and longer frequencies** are clock-based — they fire at a specific time on a specific day. This lets users schedule syncs during off-hours (e.g., 2 AM on Mondays) to avoid any latency during business use.

**Field visibility:** The Connection form should use Frappe's `depends_on` to show/hide scheduling fields contextually:
- `sync_time`: visible when `sync_frequency` is Daily, Weekly, Bi-Weekly, or Monthly
- `sync_day_of_week`: visible when `sync_frequency` is Weekly or Bi-Weekly
- `sync_day_of_month`: visible when `sync_frequency` is Monthly

And for enrichment fields:
- `custom_reference_regex`: visible when `extract_reference_number` is checked
- `custom_party_regex`: visible when `extract_party_name` is checked

**Interval equivalents** (for retry window validation per Section 6.5):

| Frequency | Interval (minutes) |
|---|---|
| Every 2 Hours | 120 |
| 4x Daily | 360 |
| Twice Daily | 720 |
| Daily | 1440 |
| Weekly | 10080 |
| Bi-Weekly | 20160 |
| Monthly | 40320 (28 days — conservative estimate for validation) |

### 6.7 Cleanup Tasks

**`cleanup_old_sync_logs`** (runs daily):
1. Read `log_retention_days` from SimpleFIN Sync Settings.
2. Delete SimpleFIN Sync Log records older than retention period.

---

## 7. Security

### 7.1 Credential Storage

- `access_url` field type is `Password`, which Frappe encrypts at rest using the site's `encryption_key`.
- `setup_token` field is also `Password` type; cleared immediately after exchange.
- Access URL is never logged in plain text. Sync logs store only connection reference, not credentials.
- If `enable_detailed_logging` is on, raw API responses are stored but Access URLs are redacted.

### 7.2 HTTPS Enforcement

- All SimpleFIN requests use `requests` library with `verify=True` (default SSL verification).
- If a decoded Setup Token URL is not HTTPS, reject it immediately.
- If an Access URL is not HTTPS, reject it and log an error.

### 7.3 Input Sanitization

- All strings from SimpleFIN `errors` array are HTML-escaped before display via `frappe.utils.html_utils.clean_html()` or `bleach`.
- Transaction descriptions are sanitized before storage.
- Organization names/domains are sanitized.

### 7.4 Permissions

| Role | SimpleFIN Connection | SimpleFIN Sync Log | SimpleFIN Sync Settings |
|---|---|---|---|
| System Manager | Full CRUD | Read, Delete | Full CRUD |
| Accounts Manager | Full CRUD | Read | Read |
| Accounts User | Read | Read | — |

---

## 8. Error Handling

### 8.1 Token Exchange Errors

| Scenario | Behavior |
|---|---|
| 200 + valid Access URL | Store encrypted, set `is_registered=1`, clear `setup_token` |
| 403 | Show error: token already claimed or compromised. Advise user to generate new token and consider revoking old one. |
| Network error | Show error with details. User can retry with same token. |
| Non-HTTPS claim URL | Reject immediately. Show security warning. |

### 8.2 Sync Errors

| Scenario | Behavior |
|---|---|
| HTTP 403 on `/accounts` | Set `connection_status=Revoked`. Disable connection. Notify per `on_sync_failure`. |
| HTTP 402 on `/accounts` | Set `connection_status=Payment Required`. Notify per `on_sync_failure`. |
| HTTP 5xx | Log error. Schedule retry. |
| Network timeout | Log error. Schedule retry. |
| JSON parse error | Log error with response body (redacted). Schedule retry. |
| SimpleFIN `errors` array non-empty | Log all messages. Display to user on next connection view. Continue processing if accounts data is present. |
| No mapped accounts in response | Trigger `on_empty_account` notification. |
| Rate limit warning in `errors` | **Trigger rate limit pause** per Section 5.5. Set `rate_limit_paused_until`, set `connection_status = "Rate Limited"`, notify, process current response data, abort remaining chunks. |

### 8.3 Data Errors

| Scenario | Behavior |
|---|---|
| Unknown currency (URL-based custom currency) | Log warning. Store the currency URL string. Transaction still created. |
| Amount parse failure | Log error. Skip transaction. Increment error count in sync log. |
| Missing required fields (`id`, `posted`, `amount`, `description`) | Log error. Skip transaction. |
| `posted = 0` (pending with no post date) | Skip (pending transactions excluded by default). |

---

## 9. Notifications

Use Frappe's built-in notification system (`frappe.sendmail`, `frappe.publish_realtime` for system notifications).

**Notification triggers:**
1. **Sync Failure** (`on_sync_failure`): Connection-level. Includes error details and link to sync log.
2. **Empty Account** (`on_empty_account`): No transactions returned for a mapped account that previously had transactions.
3. **Record Mismatch** (`on_record_mismatch`): Duplicate transaction ID with different data. Includes old vs. new values.
4. **Connection Revoked**: Always notifies (System Notification + Email to recipients) regardless of notification settings.
5. **Token Exchange Failure**: Always shown as a UI message (msgprint) since it's interactive.

---

## 10. App Structure

```
simplefin_sync/
├── simplefin_sync/
│   ├── __init__.py
│   ├── hooks.py
│   ├── patches/                          # Data migration patches
│   ├── simplefin_sync/
│   │   ├── doctype/
│   │   │   ├── simplefin_connection/
│   │   │   │   ├── simplefin_connection.py
│   │   │   │   ├── simplefin_connection.js
│   │   │   │   ├── simplefin_connection.json
│   │   │   │   └── test_simplefin_connection.py
│   │   │   ├── simplefin_account_mapping/
│   │   │   │   ├── simplefin_account_mapping.py
│   │   │   │   └── simplefin_account_mapping.json
│   │   │   ├── simplefin_sync_log/
│   │   │   │   ├── simplefin_sync_log.py
│   │   │   │   ├── simplefin_sync_log.js
│   │   │   │   └── simplefin_sync_log.json
│   │   │   ├── simplefin_balance_snapshot/
│   │   │   │   ├── simplefin_balance_snapshot.py
│   │   │   │   └── simplefin_balance_snapshot.json
│   │   │   └── simplefin_sync_settings/
│   │   │       ├── simplefin_sync_settings.py
│   │   │       └── simplefin_sync_settings.json
│   │   └── workspace/
│   │       └── simplefin_sync/
│   │           └── simplefin_sync.json     # Workspace with shortcuts
│   ├── api/
│   │   ├── __init__.py
│   │   └── simplefin.py                   # Whitelisted API methods
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── simplefin_client.py            # HTTP client for SimpleFIN API
│   │   ├── sync.py                        # Core sync logic
│   │   ├── enrichment.py                  # Transaction data enrichment (reference_number, party_name)
│   │   └── notifications.py               # Notification helpers
│   ├── tasks.py                           # Scheduled task handlers
│   └── install.py                         # Post-install setup (custom fields)
├── setup.py
├── setup.cfg
├── requirements.txt                       # Empty or minimal — `requests` is a Frappe core dep
├── license.txt                            # GPL-3.0
├── MANIFEST.in
└── README.md
```

### 10.1 hooks.py Key Entries

```python
app_name = "simplefin_sync"
app_title = "SimpleFIN Sync"
app_publisher = "Steve Bourg"
app_description = "Bank transaction sync via SimpleFIN Bridge for ERPNext"
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
    ]
}

# Document events (optional — for cleanup on Bank Transaction delete)
doc_events = {
    "Bank Transaction": {
        "on_trash": "simplefin_sync.utils.sync.on_bank_transaction_trash"
    }
}
```

### 10.2 install.py

Creates custom fields on `Bank Transaction` (see Section 4.1). Uses `frappe.custom.doctype.custom_field.custom_field.create_custom_fields()`.

On uninstall, removes custom fields cleanly.

---

## 11. SimpleFIN Client Module

`simplefin_sync/utils/simplefin_client.py`

```python
class SimpleFINClient:
    """HTTP client for SimpleFIN Bridge API."""

    def __init__(self, access_url: str):
        """Parse access_url into base_url + auth credentials."""

    @staticmethod
    def claim_access_url(setup_token: str) -> str:
        """Exchange a setup token for an access URL. One-time operation."""

    def get_accounts(
        self,
        start_date: int = None,
        end_date: int = None,
        account_ids: list[str] = None,
        include_pending: bool = False,
        balances_only: bool = False,
    ) -> dict:
        """Fetch account data from SimpleFIN Bridge."""

    def test_connection(self) -> dict:
        """Lightweight connection test using balances-only mode."""
```

All methods:
- Enforce HTTPS
- Verify SSL
- Set appropriate timeouts (30 seconds)
- Return parsed JSON or raise typed exceptions
- Never log credentials

---

## 12. Testing Strategy

### 12.1 Unit Tests

- **Token exchange:** Mock HTTP responses (200 with URL, 403, network error).
- **Sync logic:** Mock SimpleFIN client. Test dedup, mismatch detection, date conversion, amount parsing.
- **Date range splitting:** Edge cases for 90-day limit.
- **Scheduler:** Test frequency calculations, retry logic.

### 12.2 Integration Tests

- **End-to-end with demo token:** SimpleFIN provides reusable demo tokens for testing. Use these in CI.
- **Bank Transaction creation:** Verify correct field mapping, dedup on re-run.
- **Custom field installation:** Verify install/uninstall cycle.

### 12.3 Manual Testing (Development Environment)

- Use BECU Business account via SimpleFIN Bridge.
- Test full flow: create connection → exchange token → map accounts → run sync → verify in Bank Reconciliation Tool.

---

## 13. Frappe Marketplace Publication

### 13.1 Requirements

- App hosted on public GitHub repository
- GPL-3.0 license (compatible with Frappe Marketplace requirements)
- Works with ERPNext v16
- `setup.py` / `setup.cfg` properly configured
- README with installation instructions, screenshots, and usage guide

### 13.2 Metadata

```
App Title: SimpleFIN Sync
Tagline: Automatic bank transaction import via SimpleFIN Bridge
Category: Integrations
```

---

## 14. Implementation Phases

### Phase 1: Foundation
- [ ] Scaffold Frappe app (`bench new-app simplefin_sync`)
- [ ] Create all DocTypes (Connection, Account Mapping, Sync Log, Balance Snapshot, Settings)
- [ ] Create custom fields on Bank Transaction
- [ ] Implement SimpleFIN client module
- [ ] Implement token exchange flow (setup token → access URL)

### Phase 2: Core Sync
- [ ] Implement sync logic (Section 5)
- [ ] Implement dedup with mismatch detection
- [ ] Implement date range splitting for >90 day spans
- [ ] Bank Transaction creation with proper field mapping
- [ ] Test Connection button (balances-only)
- [ ] Manual Sync Now button

### Phase 3: Scheduling & Notifications
- [ ] Implement scheduler (check_due_syncs)
- [ ] Implement retry logic
- [ ] Implement notification system (email, system notification, log)
- [ ] Implement sync log cleanup task

### Phase 4: UI & Polish
- [ ] Connection list view with status indicators
- [ ] Connection form with dashboard indicators (registration, sync health)
- [ ] Workspace with shortcuts to Connections, Logs, Settings
- [ ] Account mapping refresh button

### Phase 5: Testing & Documentation
- [ ] Unit tests for all modules
- [ ] Integration tests with SimpleFIN demo tokens
- [ ] README and user documentation
- [ ] Code documentation (docstrings, inline comments)
- [ ] Manual testing with BECU Business account

### Phase 6: Marketplace Release
- [ ] Final review of license, attribution, metadata
- [ ] GitHub repository setup
- [ ] Marketplace submission

---

## 15. Open Questions / Future Scope

### Deferred to v2.0+
- Batch reject/restore operations (select multiple transactions, bulk cancel)
- Pre-built enrichment pattern sets for common bank formats (BECU, Chase, Bank of America, etc.) as selectable presets per connection
- Bank of America GCA individual cardholder support
- Connection Health scheduled job (separate from sync — daily auth check)
- Auto-reconciliation suggestions based on SimpleFIN data
- Support for SimpleFIN custom currencies (non-ISO 4217)
- Webhook support if SimpleFIN adds push notifications
- Multi-company support (one connection per company)

### Open Questions
None — all open questions from the initial spec have been resolved:
1. ✅ Bank Transaction `status` must be `"Unreconciled"` in v16 for Bank Reconciliation Tool visibility.
2. ✅ The `all` scheduler event (every 60 seconds) is fully supported on Frappe Cloud.
3. ✅ The `requests` library is a core Frappe dependency — do not add to `requirements.txt`.

---

## 16. Post-Implementation Notes

The following deviations and additions were discovered during v1.0 implementation (March 2026) against ERPNext v16.11.0 / Frappe v16.12.0.

### 16.1 Frappe v16 API Changes

| Spec Reference | Issue | Resolution |
|---|---|---|
| `frappe.get_logger(__name__)` | Removed in Frappe v16 | Use `frappe.logger(__name__)` |
| `frappe.enqueue(..., deduplicate=True)` | Requires `job_id` parameter in v16 | Added `job_id=f"simplefin_sync_{conn.name}"` |
| Datetime fields | MariaDB rejects timezone-aware strings (`+00:00`) | All Datetime values use naive UTC: `datetime.utcfromtimestamp()` |
| Python version | Spec said 3.11+ | Frappe v16 requires **Python 3.14+** (bench image ships 3.14.2) |
| Node version | Not specified | Frappe v16 requires **Node.js 24+** (bench image ships 24.13.0) |

### 16.2 Additional Fields (Not in Original Spec)

| DocType | Field | Purpose |
|---|---|---|
| SimpleFIN Connection | `next_scheduled_sync` (Datetime, read-only) | Computed on save — shows when the next sync will fire. Displayed in list view. |

### 16.3 DocType JSON Deviations

- **Password field length:** `setup_token` and `access_url` require `"length": 500` because SimpleFIN setup tokens are ~240 characters (Frappe Password default max is 140).
- **Transaction timezone:** The `transaction_timezone` Select field has `"options": ""` in the JSON; options are populated dynamically via a whitelisted method (`get_timezone_options()`) that returns `zoneinfo.available_timezones()`. Default is set in `before_insert` from `System Settings.time_zone`.

### 16.4 Connection Deletion

The spec did not address deleting a SimpleFIN Connection that has associated Sync Logs and Bank Transactions. Implementation adds `ignore_links_on_delete = ["SimpleFIN Sync Log", "Bank Transaction"]` to `hooks.py`, allowing connection deletion while retaining historical records. (Note: the hook value is the **linking** doctype, not the doctype being deleted.)

### 16.5 Guided Registration Workflow

The spec describes the Register button appearing after save. The implementation enhances this with a chained UX flow:

1. **First save** of a new connection with a setup token → prompt: "Register now?"
2. **Registration** auto-populates the Account Mappings table by fetching accounts via balances-only mode (one extra API call, minimal rate-limit impact)
3. **Post-registration message** guides the user through: map accounts → check Active → check Enabled → Sync Now
4. **"Enable & Sync Now" button** appears when the connection is registered and has mapped accounts but is not yet enabled — combines enable + sync in one click

### 16.6 Enrichment Pattern Expansion

The spec's enrichment patterns (Section 5.6) were designed for traditional bank formats (`ACH Payment - Vendor`). Real credit union data (BECU) uses a different format (`POS Withdrawal MERCHANT ADDRESS CITY STZIP`). The implementation expands patterns to handle both:

**Additional prefixes:**
- `POS/ATM/External/Internal/New Account` + `Withdrawal/Deposit/Transfer/Credit/Debit/Payment`
- These cover BECU, USAA, Navy Federal, STCU, and most US credit union descriptions

**Address tail removal:**
- Street addresses starting with numbers (`4141 Hacienda Drive PLEASANTON CAU`)
- PO Box addresses (`PO Box 7081 CHESTNUT MOUNGAUS`)
- City/state blocks with excess whitespace
- Embedded trace numbers (`ONLNE TRNSFR88871085`)

**Merchant cleanup:**
- `BRAND* BRAND-REAL NAME` → `REAL NAME` (e.g., `ZOHO* ZOHO-ZOHO CORP` → `Zoho Corp`)
- `ACCT VERIFY` suffix stripping

**Reference number extraction addition:**
- Embedded transfer numbers: `TRNSFR88871085` → `88871085`

**Whitespace normalization:**
- Excess spaces (common in credit union descriptions) collapsed before pattern matching

### 16.7 Workspace Format

Frappe v16 workspaces require a `content` field containing JSON-encoded block definitions (`header`, `spacer`, `card` types). The `links` array uses `"type": "Card Break"` entries as group headers with `"type": "Link"` entries beneath. A workspace with only top-level `links` and `shortcuts` (no content blocks) renders as blank gray boxes.

### 16.8 Timezone Configuration

Frappe stores timezone in three layered locations. The boot info sends both `system` and `user` timezone; the client uses the **user** value for display:

1. `System Settings.time_zone` — the DocType field
2. `tabDefaultValue` (`defkey='time_zone'`, `parent='__default'`) — system default
3. `User.time_zone` — per-user field on the User doctype (**takes priority**)

Frappe seeds Administrator and Guest with `Asia/Kolkata` by default. All three layers must be set during programmatic setup to avoid display inconsistencies.

### 16.9 Automatic Party Matching

ERPNext's `enable_party_matching` (Accounts Settings > Others tab) runs in `before_submit` on Bank Transaction. It fuzzy-matches `bank_party_name` against existing Customer/Supplier/Employee names. Key points:

- Only triggers on **newly submitted** transactions, not retroactively
- Requires the party to already exist in ERPNext
- The `bank_party_name` field has `allow_on_submit=0` (read-only after submission) — this is by design; users correct matching via `party_type` and `party` fields which are editable
- The Sync Time field description dynamically shows the system timezone from `frappe.boot.time_zone.system`

---

## 16. Code Standards & Attribution

### Coding Standards
- Follow existing ERPNext/Frappe codebase conventions
- Python: PEP 8, type hints where practical, docstrings on all public methods
- JavaScript: Frappe client-side patterns (frm.trigger, cur_frm, etc.)
- Use `frappe._()` for all user-facing strings (translation support)
- Use `frappe.get_logger()` for logging
- No raw SQL — use Frappe's ORM (`frappe.get_doc`, `frappe.get_all`, `frappe.db`)

### Attribution
```
# Copyright (c) 2026, Steve Bourg and Contributors
# Code developed with assistance from Claude Opus/Sonnet 4.6 (Anthropic)
# License: GPL-3.0
```

This header appears in every source file.
