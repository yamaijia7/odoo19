# -*- coding: utf-8 -*-
from odoo import models, fields


class ResPartner(models.Model):
    """Adds a fax number field to partner, displayed on the PDF report header."""
    _inherit = 'res.partner'

    custom_fax = fields.Char(string='Fax')
    company_name_cn = fields.Char(
        string='Chinese Name (中文名稱)',
        help="Chinese company/contact name shown as a second line under "
             "'Client :' on the rental invoice (matches the Odoo 11 layout).",
    )
