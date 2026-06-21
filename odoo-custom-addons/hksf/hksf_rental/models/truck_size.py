# -*- coding: utf-8 -*-
from odoo import models, fields


class TruckSize(models.Model):
    _name = 'hksf.truck.size'
    _description = 'Truck Size'
    _order = 'sequence, name'

    name = fields.Char(
        string='Truck Size',
        required=True,
        translate=True,
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
    )
    active = fields.Boolean(
        string='Active',
        default=True,
    )

    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'Truck size name must be unique.'),
    ]
