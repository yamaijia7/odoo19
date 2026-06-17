# -*- coding: utf-8 -*-
from odoo import fields, models, api, _


class HrPayslip(models.Model):
    _inherit = 'hr.payslip'

    timesheet_ids = fields.Many2many(
        'account.analytic.line',
        'hksf_payslip_timesheet_rel',
        'payslip_id',
        'line_id',
        string='Timesheet Lines',
        readonly=True,
        copy=False,
    )
    timesheet_line_count = fields.Integer(
        compute='_compute_timesheet_line_count', store=True)

    work_type_ids = fields.One2many(
        'payslip.totalhour', 'payslip_id',
        string='Work Type Summary',
        compute='_compute_work_type_summary',
        store=True,
    )

    @api.depends('timesheet_ids')
    def _compute_timesheet_line_count(self):
        for slip in self:
            slip.timesheet_line_count = len(slip.timesheet_ids)

    @api.depends('timesheet_ids', 'timesheet_ids.work_type_id',
                 'timesheet_ids.hour_days', 'timesheet_ids.days')
    def _compute_work_type_summary(self):
        WorkType = self.env['timesheet.work.type']
        for slip in self:
            slip.work_type_ids = [(5, 0, 0)]
            type_vals = []
            for wtype in WorkType.search([]):
                matching = slip.timesheet_ids.filtered(
                    lambda l, wt=wtype: l.work_type_id == wt)
                total_hour = sum(matching.mapped('hour_days'))
                total_days = sum(matching.mapped('days'))
                type_vals.append((0, 0, {
                    'work_type_id': wtype.id,
                    'total_hour': total_hour,
                    'total_days': total_days,
                    'payslip_id': slip.id,
                }))
            slip.work_type_ids = type_vals

    def _get_worked_day_lines(self):
        """Inject TIMESHEET_WORKING_DAYS into the payslip worked-day lines.
        Counts distinct calendar day entries; full-day=1.0, half-day=0.5.
        Uses parameterized SQL for safety.
        """
        res = super()._get_worked_day_lines()
        if not self.contract_id:
            return res

        self.env.cr.execute("""
            SELECT SUM(hour_days) AS total_hours
            FROM account_analytic_line
            WHERE employee_id = %s
              AND date >= %s
              AND date <= %s
        """, (self.employee_id.id, self.date_from, self.date_to))
        row = self.env.cr.dictfetchone()
        total_hours = (row.get('total_hours') or 0.0) if row else 0.0

        self.env.cr.execute("""
            SELECT days
            FROM account_analytic_line
            WHERE employee_id = %s
              AND date >= %s
              AND date <= %s
              AND days > 0
        """, (self.employee_id.id, self.date_from, self.date_to))
        day_rows = self.env.cr.fetchall()
        total_days = 0.0
        for (d,) in day_rows:
            if d >= 1.0:
                total_days += 1.0
            elif d >= 0.5:
                total_days += 0.5

        res = [r for r in res if r.get('code') != 'TIMESHEET_WORKING_DAYS']

        work_entry_type = self.env.ref(
            'hr_work_entry.hr_work_entry_type_attendance',
            raise_if_not_found=False)

        res.append({
            'name': _('Working Days Based On Timesheet'),
            'code': 'TIMESHEET_WORKING_DAYS',
            'number_of_days': total_days,
            'number_of_hours': total_hours,
            'work_entry_type_id': work_entry_type.id if work_entry_type else False,
            'contract_id': self.contract_id.id,
            'sequence': 6,
        })
        return res

    def action_payslip_done(self):
        res = super().action_payslip_done()
        for slip in self:
            slip.timesheet_ids.write({
                'is_payroll_paid': True,
                'custom_payslip_id': slip.id,
            })
        return res

    def action_view_timesheet_lines(self):
        self.ensure_one()
        return {
            'name': _('Timesheet Lines'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.analytic.line',
            'view_mode': 'list,form',
            'domain': [('custom_payslip_id', '=', self.id)],
            'context': {'default_employee_id': self.employee_id.id},
        }
