# -*- coding: utf-8 -*-
"""Set Product Price decimal precision to 5 on upgrade.

post_init_hook only runs on fresh install; this migration ensures an existing
installation (e.g. production hkssl) gets the precision bump when upgraded with
-u hksf_rental. See __init__._set_product_price_precision for rationale.
"""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    prec = env['decimal.precision'].search([('name', '=', 'Product Price')], limit=1)
    if prec and prec.digits < 5:
        prec.digits = 5
