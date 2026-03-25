# SimpleFIN Sync

Automatic bank transaction import via [SimpleFIN Bridge](https://beta-bridge.simplefin.org/info/developers) for ERPNext.

SimpleFIN Sync connects ERPNext to your bank accounts through the SimpleFIN Bridge aggregation service. It periodically retrieves posted bank transactions and imports them as Bank Transaction records, ready for reconciliation using ERPNext's standard Bank Reconciliation Tool.

## Features

- **Automatic sync** — Configurable schedules from every 2 hours to monthly
- **Deduplication** — Idempotent syncs with mismatch detection; no duplicates on re-run
- **Date range chunking** — Handles >90-day history pulls automatically (newest-first)
- **Party name extraction** — Parses merchant/party names from bank descriptions for ERPNext's automatic party matching
- **Reference number extraction** — Extracts check numbers and reference codes from descriptions and SimpleFIN's `extra` data
- **Rate limit protection** — Detects SimpleFIN rate limit warnings and auto-pauses to protect your access token
- **Encrypted credentials** — Access URLs stored using Frappe's encrypted Password fields
- **Retry logic** — Configurable retry count and interval with automatic state machine management
- **Balance snapshots** — Records account balances on each sync for auditing
- **Per-connection configuration** — Each connection has its own sync schedule, enrichment settings, and notification preferences

## Requirements

- ERPNext v16
- Frappe v16
- Python 3.14+
- A [SimpleFIN Bridge](https://beta-bridge.simplefin.org/) account

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/archadianai/SimpleFIN_Sync.git
bench --site your-site install-app simplefin_sync
```

The installer automatically creates custom fields on the Bank Transaction doctype and a database index for fast deduplication lookups.

## Setup

### 1. Get a SimpleFIN Setup Token

1. Go to [SimpleFIN Bridge](https://beta-bridge.simplefin.org/) and create an account (or log in)
2. Connect your bank through the Bridge interface
3. Generate a **Setup Token** — this is a one-time code that SimpleFIN Sync exchanges for a persistent access credential

### 2. Create a Connection

1. In ERPNext, go to **SimpleFIN Sync** in the sidebar (or search for "SimpleFIN Connection")
2. Click **New SimpleFIN Connection**
3. Enter a name (e.g., "BECU Business") and paste your Setup Token
4. Click **Save**

### 3. Register and Map Accounts

After saving, you'll be prompted to register with SimpleFIN Bridge. The registration:
- Exchanges your setup token for an encrypted access credential (one-time operation)
- Fetches your bank accounts and populates the Account Mappings table

Then:
1. For each account you want to sync, set the **ERPNext Bank Account** (Link field) and verify the **Transaction Timezone** matches the bank's local timezone
2. Check the **Enabled** checkbox at the top of the form
3. Click **Save**, then **Actions > Sync Now** (or **Actions > Enable & Sync Now**)

### 4. Verify in Bank Reconciliation Tool

1. Go to **Bank Reconciliation Tool**
2. Select your Bank Account
3. Set a date range covering your transactions
4. Click **Get Unreconciled Entries**

Your imported transactions should appear with status "Unreconciled", ready for matching.

### 5. Enable Automatic Party Matching (Optional)

ERPNext can automatically match imported transactions to existing Customers, Suppliers, and Employees:

1. Go to **Accounts Settings**
2. Click the **Others** tab
3. Check **Enable Automatic Party Matching**

This fuzzy-matches the `bank_party_name` field (extracted by SimpleFIN Sync) against your party records. It only works for newly synced transactions and requires the party to already exist in ERPNext.

## Configuration

### Sync Frequency

| Frequency | Behavior |
|-----------|----------|
| Every 2 Hours | Interval-based — fires when enough time has elapsed |
| 4x Daily | Every 6 hours |
| Twice Daily | Every 12 hours |
| Daily | At the configured Sync Time |
| Weekly | On the configured day at Sync Time |
| Bi-Weekly | Every 14 days on the configured day |
| Monthly | On the configured day of month (1-28) |

### Transaction Enrichment

Each connection has toggles for:
- **Extract Reference Number** — Pulls check numbers, reference codes, and trace numbers from descriptions
- **Extract Party Name** — Parses merchant/party names from bank descriptions

Both support **custom regex** with one capture group for bank-specific formats. Leave blank to use the built-in patterns.

### Notifications

Per-connection notification preferences for:
- **Sync Failure** — When a sync fails after all retries
- **Empty Account** — When a mapped account returns no transactions
- **Record Mismatch** — When SimpleFIN returns different data for an existing transaction

Each can be set to: Log Only, Email, or System Notification.

### Settings

Global settings at **SimpleFIN Sync Settings**:
- **Log Retention Days** — How long to keep sync logs (default: 90 days)
- **Default Sync Frequency** — Default for new connections
- **Enable Detailed Logging** — Log raw API responses for debugging

## Manual Recovery: Accidentally Cancelled Transactions

If a Bank Transaction is cancelled by mistake:

1. **Delete** the cancelled Bank Transaction document (requires Accounts Manager or System Manager permission)
2. On the **next scheduled sync**, if the transaction is still within the rolling window (default: 14 days), dedup will find no match and re-import it as a fresh record
3. If the transaction has **aged out** of the rolling window, open the SimpleFIN Connection and click **Actions > Sync Now** to trigger a manual sync that covers the full initial history range

Note: Cancelled transactions (docstatus=2) are intentionally retained in the dedup check to prevent unwanted re-imports. Deleting the record is what signals that a re-import is desired.

## Permissions

| Role | Connections | Sync Logs | Settings |
|------|-------------|-----------|----------|
| System Manager | Full CRUD | Read, Delete | Full CRUD |
| Accounts Manager | Full CRUD | Read | Read |
| Accounts User | Read | Read | — |

## Contributing

This app uses `pre-commit` for code formatting and linting:

```bash
cd apps/simplefin_sync
pre-commit install
```

Tools: ruff, eslint, prettier

### Running Tests

```bash
bench --site your-site run-tests --app simplefin_sync
```

All tests use mocked HTTP responses — no external API calls are made during testing.

## License

GPL-3.0

## Credits

- **Author:** Steve Bourg / [Archadian AI, LLC](https://github.com/archadianai)
- **Development assistance:** Claude Opus/Sonnet 4.6 (Anthropic)
- **SimpleFIN Bridge:** [simplefin.org](https://www.simplefin.org/)
