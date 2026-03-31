# Sync via SimpleFIN

Automatic bank transaction import via [SimpleFIN Bridge](https://beta-bridge.simplefin.org/info/developers) for ERPNext.

Sync via SimpleFIN connects ERPNext to your bank accounts through the SimpleFIN Bridge aggregation service. It periodically retrieves posted bank transactions and imports them as Bank Transaction records, ready for reconciliation using ERPNext's standard Bank Reconciliation Tool.

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
bench get-app https://github.com/archadianai/Sync_SimpleFIN.git
bench --site your-site install-app sync_simplefin
```

The installer automatically creates custom fields on the Bank Transaction doctype and a database index for fast deduplication lookups.

## Setup

### 1. Get a SimpleFIN Setup Token

1. Go to [SimpleFIN Bridge](https://beta-bridge.simplefin.org/) and create an account (or log in)
2. Connect your bank through the Bridge interface
3. Generate a **Setup Token** — this is a one-time code that Sync via SimpleFIN exchanges for a persistent access credential

### 2. Create a Connection

1. In ERPNext, go to **Sync via SimpleFIN** in the sidebar (or search for "SimpleFIN Connection")
2. Click **New SimpleFIN Connection** — a setup wizard dialog appears
3. **Step 1:** Enter a connection name (e.g., "BECU Business") and paste your Setup Token, then click **Register**
4. **Step 2:** Your bank accounts appear grouped by institution. Set the **ERPNext Bank Account** for each account you want to sync, then click **Create Connection**

The wizard exchanges your token, discovers your accounts, maps them, and enables the connection — all in one flow.

### 3. Review Settings and Sync

After the wizard completes, you'll land on the Connection form with a blue banner: *"Review your settings below, then use Actions → Sync Full to import transactions."*

1. Review **Sync Schedule**, **Transaction Enrichment** (per account, in Account Mappings), and **Notification Settings**
2. Click **Actions → Sync Full** to import your transaction history

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

This fuzzy-matches the `bank_party_name` field (extracted by Sync via SimpleFIN) against your party records. It only works for newly synced transactions and requires the party to already exist in ERPNext.

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

Enrichment settings are **per account** (in the Account Mappings table, click a row to edit). Each account has:
- **Extract Reference Number** — Pulls check numbers, reference codes, and trace numbers from descriptions
- **Extract Party Name** — Parses merchant/party names from bank descriptions

Both support **custom regex** with one capture group for bank-specific formats. Leave blank to use the built-in patterns. Per-account settings allow different extraction patterns for different institutions on the same connection.

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
3. If the transaction has **aged out** of the rolling window, open the SimpleFIN Connection and click **Actions > Sync Full** to re-pull the full history range

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
cd apps/sync_simplefin
pre-commit install
```

Tools: ruff, eslint, prettier

### Running Tests

```bash
bench --site your-site run-tests --app sync_simplefin
```

All tests use mocked HTTP responses — no external API calls are made during testing.

## License

GPL-3.0

## Credits

- **Author:** Steve Bourg / [Archadian AI, LLC](https://github.com/archadianai)
- **Development assistance:** Claude Opus/Sonnet 4.6 (Anthropic)
- **SimpleFIN Bridge:** [simplefin.org](https://www.simplefin.org/)

## Trademarks

ERPNext is a registered trademark of Frappe Technologies Pvt Ltd. SimpleFIN is a trademark of SF Sync, LLC. This tool is not affiliated with or endorsed by either party.
