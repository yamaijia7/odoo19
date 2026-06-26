# Odoo19 Custom Modules Summary

## Overview
This document provides a summary of the custom Odoo modules in the current project, their purposes, and development considerations.

## Custom Modules

### 1. HKSF Rental (`hksf_rental`)
**Category**: Sales/Rental Management
**Version**: 19.0.1.83.0
**Description**: Consolidated module for scaffolding rental management that merges three previous modules:
- hksf_rental (quotation/pricing base)
- hksf_rental_invoice (redundant wizard dropped; only account.move header fields and sale.order.minimum_charge_method kept)
- hksf_delivery_invoice (canonical billing chain)

**Key Features**:
- Sale order rental/sale toggle with per-line duration in months/weeks
- Weight/volume tracking
- Custom PDF quotation report
- Delivery & collection tracking on stock.picking / stock.move
- Pro-rata rental invoicing via hksf.delivery.invoice.wizard
- Return collection, damage/lost invoice, and sale-line qty update wizards
- Transport charges and lost/damaged material tracking
- Outstanding-product summary

**Dependencies**: 
- sale_management, sale_stock, stock, account, crm, hr, uom, analytic

### 2. HKSSL OCA Admin Accountant (`hkssl_oca_admin_accountant`)
**Category**: Accounting
**Version**: 19.0.1.0.0
**Description**: Grants Odoo Administrator users full Accountant accounting rights when account_usability (OCA) is installed.

**Key Features**:
- Ensures both admin users (base.user_admin / base.user_root) are always members of account.group_account_manager (Accountant)
- Maintains Settings access while providing full accounting visibility

**Dependencies**: 
- account, account_usability

### 3. Third-party Modules
Several OCA and community modules have been included:
- `base_account_budget`: Budget management functionality
- Various other modules from Odoo Community Association (OCA)

## Upgrade Considerations
The HKSF rental module has a detailed upgrade runbook that identifies potential compatibility issues for future major version jumps:

### Low Impact Risks:
- Tax computation changes (`compute_all()` method)
- UoM field rename (`stock.move.product_uom` to `product_uom_id`)

### Medium Risk:
- Core method overrides - re-test after every upgrade
  - sale.order.line._compute_amount
  - stock.picking.button_validate
  - account.move / account.move.line._compute_*
  - sale.order.create/write/action_confirm
  - stock.move.write/unlink

### Data-layer Items (Low Risk):
- `delivery.return.history` invoice-line links have no `ondelete`
- No `company_id` on some custom models
- Selection keys stored as strings

## Development Goals
1. **Documentation Improvement**: Create comprehensive documentation for the HKSF rental module
2. **Maintenance and Compatibility**: Monitor compatibility issues with future Odoo versions
3. **Enhancement Planning**: Evaluate UI improvements and performance optimizations
4. **Testing and Quality Assurance**: Maintain regression test suite
5. **Customization Support**: Plan for maintainable extensions

## Recommended Actions
- Regular review of upgrade runbook for new risks
- Ensure all custom code paths remain compatible with core Odoo state management
- Keep module stable during upgrades while maintaining functionality