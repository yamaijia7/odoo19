# -*- coding: utf-8 -*-
from odoo import api, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def _get_unpaid_timesheet_lines(self, date_from, date_to, work_type):
        """Return approved, unpaid timesheet lines for this employee
        in the given date range and work type.
        Requires sheet state 'done' if hr_timesheet_sheet is installed.
        """
        domain = [
            ('employee_id', '=', self.id),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('work_type_id', '=', work_type.id),
            ('is_payroll_paid', '=', False),
        ]
        if self.env['account.analytic.line']._fields.get('sheet_id'):
            domain += [
                ('sheet_id', '!=', False),
                ('sheet_id.state', '=', 'done'),
            ]
        return self.env['account.analytic.line'].search(domain)

    @api.model
    def _get_work_type_salary(self, code, payslip_id):
        """Salary rule helper. Call from a salary rule Python expression::

            result = employee._get_work_type_salary('NORM', payslip.id)

        Steps:
        1. Find work type by code.
        2. Collect unpaid approved timesheet lines for the payslip period.
        3. Link lines to payslip via timesheet_ids M2M.
        4. Look up hourly rate from contract.worktype.
        5. Return rate x total hours.
        """
        payslip = self.env['hr.payslip'].browse(payslip_id)
        work_type = self.env['timesheet.work.type'].search(
            [('code', '=', code)], limit=1)
        if not work_type:
            return 0.0

        lines = self._get_unpaid_timesheet_lines(
            payslip.date_from, payslip.date_to, work_type)

        unlinked = lines.filtered(lambda l: not l.custom_payslip_id)
        if unlinked:
            payslip.write({'timesheet_ids': [(4, l.id) for l in unlinked]})

        rate = 0.0
        for wt_line in payslip.contract_id.work_type_ids:
            if wt_line.work_type_id == work_type:
                rate = wt_line.rate
                break

        total_hours = sum(lines.mapped('hour_days'))
        return rate * total_hours
