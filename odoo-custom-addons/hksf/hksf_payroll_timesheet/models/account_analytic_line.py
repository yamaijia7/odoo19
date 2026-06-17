# -*- coding: utf-8 -*-
from odoo import fields, models, api


class AccountAnalyticLine(models.Model):
    """Extends timesheet lines with scaffolding payroll fields.

    unit_amount is kept as the canonical quantity (hours).
    We auto-compute it as (days * _HOURS_PER_DAY) + hour_days,
    matching the Odoo 11 logic. Override _HOURS_PER_DAY if your
    site uses a different standard working day length.
    """
    _inherit = 'account.analytic.line'

    _HOURS_PER_DAY = 9.0  # standard scaffolding working day

    work_type_id = fields.Many2one(
        'timesheet.work.type', string='Work Type', index=True)

    days = fields.Float(
        string='Days', default=0.0,
        help='Working days (1.0 = full day, 0.5 = half day)')
    hour_days = fields.Float(
        string='Hours (Overtime/Extra)', default=0.0,
        help='Additional hours on top of full days')
    update_work_datetime = fields.Datetime(string='Work Updated On')

    is_payroll_paid = fields.Boolean(
        string='Payroll Paid', readonly=True, copy=False, index=True)
    custom_payslip_id = fields.Many2one(
        'hr.payslip', string='Payslip', copy=False, readonly=True, index=True)

    custom_state = fields.Selection(
        related='sheet_id.state',
        string='Sheet Status',
        store=True,
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            self._sync_unit_amount(values)
        return super().create(vals_list)

    def write(self, values):
        if 'days' in values or 'hour_days' in values:
            for rec in self:
                days = values.get('days', rec.days)
                hour_days = values.get('hour_days', rec.hour_days)
                values['unit_amount'] = (days * self._HOURS_PER_DAY) + hour_days
        return super().write(values)

    @api.model
    def _sync_unit_amount(self, values):
        if 'days' in values or 'hour_days' in values:
            days = values.get('days', 0.0) or 0.0
            hour_days = values.get('hour_days', 0.0) or 0.0
            values['unit_amount'] = (days * self._HOURS_PER_DAY) + hour_days
