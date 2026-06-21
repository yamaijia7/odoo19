# -*- coding: utf-8 -*-
from odoo import models, fields


class ProductTemplate(models.Model):
    """
    Consolidated product.template extension.

    Merges:
      - line_type + CRM tag support (from hksf_rental)
      - ia_apply_minimum_charge + special income accounts
        (from hksf_delivery_invoice)
    """
    _inherit = 'product.template'

    # ------------------------------------------------------------------
    # Rental line classification + tags (from hksf_rental)
    # ------------------------------------------------------------------
    line_type = fields.Selection(
        selection=[('rental', 'Rental'), ('sale', 'Sale')],
        string='Default Line Type',
        default='rental',
    )
    tag_ids = fields.Many2many(
        'crm.tag',
        string='CRM Tags',
    )

    # ------------------------------------------------------------------
    # Minimum charge flag (from hksf_delivery_invoice)
    # ------------------------------------------------------------------
    ia_apply_minimum_charge = fields.Boolean(
        string='Apply Minimum Charge',
        default=True,
        help="When enabled, a minimum rental period (7 days weekly / 30 days monthly) "
             "is enforced when generating the delivery invoice credit for early returns.",
    )

    # ------------------------------------------------------------------
    # Per-company income accounts for special invoice types
    # (from hksf_delivery_invoice)
    # ------------------------------------------------------------------
    property_lost_account_income_id = fields.Many2one(
        'account.account',
        string='Lost Material Income Account',
        company_dependent=True,
        help="Income account used when invoicing lost/unrecovered rental materials.",
    )
    property_r_and_d_account_income_id = fields.Many2one(
        'account.account',
        string='R&D Income Account',
        company_dependent=True,
        help="Income account used for repair & damage invoices.",
    )
    # Renamed from property_custom_sale_income_product_id (v19.0.1.27.2):
    # this is the income account a product books to on RENTAL (delivery)
    # invoices. Previously the field existed but was never applied; it is now
    # wired into the rental invoice line creation (with category + standard
    # fallbacks). Blank = fall back to category override, then Odoo default.
    property_rental_income_account_id = fields.Many2one(
        'account.account',
        string='Rental Income Account',
        company_dependent=True,
        help="Income account this product books to on rental (delivery) "
             "invoices. If blank, falls back to the product category's Rental "
             "Income Account, then to Odoo's standard income account.",
    )


class ProductCategory(models.Model):
    _inherit = 'product.category'

    # Renamed from property_custom_sale_income_categ_id (v19.0.1.27.2).
    property_rental_income_account_categ_id = fields.Many2one(
        'account.account',
        string='Rental Income Account',
        company_dependent=True,
        help="Default rental-invoice income account for all products in this "
             "category. Used when a product has no Rental Income Account set.",
    )
