# -*- coding: utf-8 -*-
from odoo import models, fields


class ResCompany(models.Model):
    """
    Adds company-level images used in the PDF report:
      - custom_header_logo  : printed full-width at the top of every page
      - company_stamp       : printed in the bottom-right footer area
      - custom_chop_and_sign: printed in the bottom-centre footer area
                              (only when sale_order.custom_chop_and_sign is True)
    """
    _inherit = 'res.company'

    company_stamp = fields.Binary(string='Company Stamp')
    custom_header_logo = fields.Binary(string='Report Header Logo')
    custom_chop_and_sign = fields.Binary(string='Chop & Sign Image')
