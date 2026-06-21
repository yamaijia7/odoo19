# -*- coding: utf-8 -*-
from odoo import models, fields


class ResUsers(models.Model):
    """Adds a signature image to users, printed on the PDF footer."""
    _inherit = 'res.users'

    signature_custom = fields.Binary(string='Signature Image')
    sp_code = fields.Char(
        string='SP Code',
        size=2,
        help="2-character salesperson code printed in the SP reference "
             "(e.g. JC -> S00001/JC/00).",
    )
