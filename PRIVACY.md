# Privacy Policy — Sync via SimpleFIN

**Last updated:** April 1, 2026

## Overview

Sync via SimpleFIN is an open-source Frappe/ERPNext application that imports bank transactions from the SimpleFIN Bridge service into your ERPNext instance. This policy describes how data is handled by the application.

## Data Collection

Sync via SimpleFIN **does not collect, transmit, or store any data on servers operated by the app developer** (Archadian AI, LLC). All data remains within your ERPNext instance and the SimpleFIN Bridge service.

## Data Flow

The application facilitates a direct connection between your ERPNext instance and the SimpleFIN Bridge API:

1. **Your ERPNext instance → SimpleFIN Bridge:** The app sends API requests to SimpleFIN Bridge to retrieve bank account data and transactions. These requests include your encrypted access credentials (stored in your ERPNext database) and date range parameters.

2. **SimpleFIN Bridge → Your ERPNext instance:** The app receives bank account balances and transaction records, which are stored as Bank Transaction documents in your ERPNext database.

No data passes through any intermediate server operated by the app developer.

## Data Storage

All data is stored within your ERPNext instance:

- **Access credentials** (setup tokens and access URLs) are encrypted at rest using Frappe's built-in encryption with your site's encryption key.
- **Bank transactions** are stored as standard ERPNext Bank Transaction documents.
- **Sync logs** record sync activity (timestamps, transaction counts, error messages) and are automatically cleaned up based on your configured retention period.
- **Account mappings** store SimpleFIN account identifiers and their association with ERPNext Bank Accounts.

Credentials are never logged, included in error messages, or exposed through the application's API.

## Third-Party Services

This application connects to **SimpleFIN Bridge** (operated by SF Sync, LLC). Your use of SimpleFIN Bridge is governed by their own terms of service and privacy policy, available at [simplefin.org](https://www.simplefin.org/). The app developer has no relationship with or access to your SimpleFIN Bridge account.

## Open Source

This application is open source under the GPL-3.0 license. The complete source code is available at [github.com/archadianai/Sync_SimpleFIN](https://github.com/archadianai/Sync_SimpleFIN) for review.

## Contact

For questions about this privacy policy or the application:

- **GitHub Issues:** [github.com/archadianai/Sync_SimpleFIN/issues](https://github.com/archadianai/Sync_SimpleFIN/issues)
- **Publisher:** Archadian AI, LLC
