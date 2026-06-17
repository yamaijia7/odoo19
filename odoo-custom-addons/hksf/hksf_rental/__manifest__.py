# -*- coding: utf-8 -*-
{
    'name': "HKSF Rental",
    'version': '19.0.1.27.9',
    'category': 'Sales',
    'summary': "Consolidated HKSF rental: quotations, duration-based pricing, "
               "delivery/collection tracking and pro-rata rental invoicing.",
    'description': """
HKSF Rental — Consolidated Module (Odoo 19)
============================================
Single module merging the previous three-module HKSF rental chain:

  - hksf_rental          (quotation/pricing base — kept in full)
  - hksf_rental_invoice  (redundant wizard dropped; only account.move header
                          fields and sale.order.minimum_charge_method kept)
  - hksf_delivery_invoice (canonical billing chain — kept in full, including
                          ALL wizards)

Features
--------
- Sale order rental/sale toggle, per-line duration in months, weekly/monthly
  charge types, weight/volume tracking, custom PDF quotation report.
- Delivery & collection tracking on stock.picking / stock.move.
- delivery.return.history linking deliveries to collection returns.
- Transport charges, lost/damaged material tracking, outstanding-product summary.
- Pro-rata rental invoicing via hksf.delivery.invoice.wizard
  (Normal + Charge First billing methods, minimum-charge enforcement).
- Return collection, damage/lost invoice, and sale-line qty update wizards.
    """,
    'author': "HKSF",
    'depends': [
        'sale_management',
        'sale_stock',
        'stock',
        'account',
        'crm',
        'hr',
        'uom',
        'analytic',
    ],
    'data': [
        # Security first
        'security/ir.model.access.csv',
        # Select Days wizard action (referenced by sale/invoice line buttons)
        'wizard/manual_month_update_view.xml',
        # Create Collection from sale order wizard (button is on sale_order_view)
        'wizard/collection_return_view.xml',
        # Base model views
        'views/res_company_view.xml',
        'views/res_partner_view.xml',
        'views/res_users_view.xml',
        'views/product_template_view.xml',
        'views/product_view.xml',
        'views/product_category_view.xml',
        'views/sale_order_view.xml',
        # Return/collection wizard actions must load BEFORE stock_picking_view
        # because the picking-form header buttons reference these actions by id.
        'wizard/return_move_select_view.xml',
        'wizard/return_move_collection_view.xml',
        'views/stock_picking_view.xml',
        'views/stock_move_view.xml',
        'views/delivery_return_history_view.xml',
        'views/transport_charge_view.xml',
        'views/account_move_view.xml',
        # Wizards
        'wizard/delivery_invoice_wizard_view.xml',
        'wizard/damage_lost_invoice_view.xml',
        'wizard/sale_line_qty_update_view.xml',
        # Reports / actions
        'report/paperformat.xml',
        'report/report_action.xml',
        'report/report_sale_rental.xml',
        'report/report_delivery_invoice.xml',
    ],
    # Product Price precision = 5 (pro-rata invoicing must not truncate unit price)
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
