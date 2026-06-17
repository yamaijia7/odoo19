# -*- coding: utf-8 -*-
from odoo import fields, models


class PayslipTotalhour(models.Model):
    """Summary table on a payslip: total hours and days per work type.
    Used in the payslip PDF report and can be referenced in salary rule
    Python expressions via payslip.work_type_ids.
    """
    _name = 'payslip.totalhour'
    _description = 'Payslip Work Type Summary'
    _order = 'payslip_id, work_type_id'

    payslip_id = fields.Many2one(
        'hr.payslip', string='Payslip', required=True,
        ondelete='cascade', index=True)
    work_type_id = fields.Many2one(
        'timesheet.work.type', string='Work Type', required=True)
    total_hour = fields.Float(string='Total Hours', digits='Payroll Rate')
    total_days = fields.Float(string='Total Days', digits='Payroll Rate')
