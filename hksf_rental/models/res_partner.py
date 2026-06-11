# -*- coding: utf-8 -*-
from odoo import models, fields


class ResPartner(models.Model):
    """Adds a fax number field to partner, displayed on the PDF report header."""
    _inherit = 'res.partner'

    custom_fax = fields.Char(string='Fax')
