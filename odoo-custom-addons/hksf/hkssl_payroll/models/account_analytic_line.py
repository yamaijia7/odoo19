# -*- coding: utf-8 -*-
from odoo import fields, models, api


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'
    _HOURS_PER_DAY = 9.0

    work_type_id = fields.Many2one('timesheet.work.type', string='Work Type', index=True)
    days = fields.Float(string='Days', default=0.0)
    hour_days = fields.Float(string='Hours (Overtime/Extra)', default=0.0)
    update_work_datetime = fields.Datetime(string='Work Updated On')
    is_payroll_paid = fields.Boolean(string='Payroll Paid', readonly=True, copy=False, index=True)
    custom_payslip_id = fields.Many2one('hr.payslip', string='Payslip', copy=False, readonly=True, index=True)
    # BUG4 FIX: custom_state removed — sheet_id not guaranteed without hr_timesheet_sheet

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            self._sync_unit_amount(values)
        return super().create(vals_list)

    def write(self, values):
        # BUG10 FIX: per-record write to avoid shared dict overwrite
        if 'days' in values or 'hour_days' in values:
            for rec in self:
                days = values.get('days', rec.days)
                hour_days = values.get('hour_days', rec.hour_days)
                unit_amount = (days * self._HOURS_PER_DAY) + hour_days
                super(AccountAnalyticLine, rec).write(dict(values, unit_amount=unit_amount))
            return True
        return super().write(values)

    @api.model
    def _sync_unit_amount(self, values):
        if 'days' in values or 'hour_days' in values:
            days = values.get('days', 0.0) or 0.0
            hour_days = values.get('hour_days', 0.0) or 0.0
            values['unit_amount'] = (days * self._HOURS_PER_DAY) + hour_days

    def _compute_wage_cost(self, contract):
        """Return this line's wage cost in HKD as a NEGATIVE analytic amount,
        using the per-work-type rate table on the given hr.version contract.

        days     -> daily_rate  (RGL-style daily-paid work)
        hour_days-> hourly_rate (OT / OVRN overtime hours)

        Convention: analytic costs are stored negative. Returns 0.0 when the
        line has no work type or the contract has no matching rate row, so a
        missing rate never silently turns into a positive/zero surprise.
        """
        self.ensure_one()
        if not self.work_type_id or not self.work_type_id.code or not contract:
            return 0.0
        daily_rate, hourly_rate = contract.get_worktype_rate(self.work_type_id.code)
        cost = (self.days or 0.0) * daily_rate + (self.hour_days or 0.0) * hourly_rate
        return -abs(cost)
