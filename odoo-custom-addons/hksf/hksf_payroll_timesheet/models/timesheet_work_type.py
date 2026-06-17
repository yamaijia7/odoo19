# -*- coding: utf-8 -*-
from odoo import fields, models


class TimesheetWorkType(models.Model):
    """Work types used on timesheet lines (e.g. Normal, Overtime, Sunday, PH).
    Each type carries its own rate factors that feed into contract rates.
    """
    _name = 'timesheet.work.type'
    _description = 'Timesheet Work Type'
    _order = 'sequence, name'

    name = fields.Char(string='Work Type', required=True, translate=True)
    code = fields.Char(
        string='Code', required=True, size=32,
        help='Used in salary rule Python expressions: employee._get_work_type_salary("CODE", payslip.id)')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    r_factor = fields.Float(
        string='R Factor',
        digits='Payroll Rate',
        help='Divisor to calculate hourly rate from daily rate (e.g. 9 for 9h/day)',
        copy=False,
    )
    dr_factor = fields.Float(
        string='DR Factor',
        digits='Payroll Rate',
        help='Divisor to calculate daily rate from monthly wage (e.g. 26 working days/month)',
        copy=False,
    )

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'Work Type code must be unique.'),
    ]
