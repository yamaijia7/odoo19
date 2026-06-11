# -*- coding: utf-8 -*-
from odoo import models, fields


class ResUsers(models.Model):
    """Adds a signature image to users, printed on the PDF footer."""
    _inherit = 'res.users'

    signature_custom = fields.Binary(string='Signature Image')
