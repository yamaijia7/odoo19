# hkssl_oca_admin_accountant

## Purpose

When `account_usability` (OCA `account-financial-tools`) is installed, it restructures Odoo's accounting group hierarchy and renames `group_account_manager` to **Accountant**. A side-effect is that the base Odoo Administrator users (`base.user_admin`, `base.user_root`) are left without any accounting group, so they cannot see accounting menus, journal entries, or analytic accounts.

This module fixes that by explicitly assigning both admin users to `account.group_account_manager` (Accountant).

## Access Matrix

| User | Settings | Accounting (full) | Notes |
|---|---|---|---|
| Administrator | ✅ | ✅ | Via this module |
| Accountant (non-admin) | ❌ | ✅ | Via `group_account_manager` |
| Bookkeeper | ❌ | Partial | Via `group_account_user` |
| Billing | ❌ | Invoicing only | Via `group_account_invoice` |
| Read-only | ❌ | Read only | Via `group_account_readonly` |

## Dependencies

- `account` (Odoo core)
- `account_usability` (OCA `account-financial-tools` 19.0)

## Installation

Install after `account_usability`. Run with:

```bash
odoo -u hkssl_oca_admin_accountant
```

## Author

H.K. Scafframe Systems Limited
