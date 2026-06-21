# -*- coding: utf-8 -*-
from odoo import fields, models


class TimesheetWorkType(models.Model):
    _name = 'timesheet.work.type'
    _description = 'Timesheet Work Type'
    _order = 'sequence, name'

    name = fields.Char(string='Work Type', required=True)
    code = fields.Char(string='Code', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    r_factor = fields.Float(string='R Factor (hrs/day)', copy=False)
    dr_factor = fields.Float(string='DR Factor (days/month)', copy=False)
