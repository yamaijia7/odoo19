# -*- coding: utf-8 -*-
# OCA-NATIVE REFACTOR (2026-06-21)
# This module now plugs into the OCA `payroll` 19.0 engine using its REAL
# public API instead of the Enterprise method names it previously (silently)
# overrode:
#   * get_worked_day_lines(self, contracts, date_from, date_to)  [@api.model]
#   * get_inputs(self, contracts, date_from, date_to)            [@api.model]
# Salary amounts are produced by native salary rules reading
# `worked_days.<CODE>.number_of_days/number_of_hours` and the per-contract
# `contract.worktype` rate table — NOT by a bespoke Python aggregator.
# Retired: _get_work_type_salary(), payslip.totalhour, the manual SQL
# _get_worked_day_lines_values()/_get_inputs_values() Enterprise overrides.
from odoo import api, fields, models, _


class HrPayslip(models.Model):
    _inherit = 'hr.payslip'

    timesheet_ids = fields.Many2many(
        'account.analytic.line', string='Timesheet Lines', readonly=True
    )
    timesheet_line_count = fields.Integer(
        compute='_compute_timesheet_line_count', store=True
    )
    is_construction_payroll = fields.Boolean(
        compute='_compute_is_construction_payroll', store=False
    )

    @api.depends('timesheet_ids')
    def _compute_timesheet_line_count(self):
        for rec in self:
            rec.timesheet_line_count = len(rec.timesheet_ids)

    @api.depends('contract_id')
    def _compute_is_construction_payroll(self):
        for rec in self:
            rec.is_construction_payroll = bool(
                rec.contract_id and rec.contract_id.work_type_ids
            )

    # ------------------------------------------------------------------
    # Worked-days lines — OCA native override
    # ------------------------------------------------------------------
    @api.model
    def get_worked_day_lines(self, contracts, date_from, date_to):
        """Emit ONE worked-days line per timesheet work type.

        OCA's default implementation (a) only processes contracts that have a
        resource_calendar_id, and (b) emits a single WORK100 line from the
        working schedule. HKSSL daily-paid casual scaffolders frequently have
        NO calendar, so we build the lines directly from their timesheet data,
        keyed by the work-type code (RGL/OT/OVRN/...). These codes are what the
        salary rules consume via `worked_days.<CODE>`.

        We still call super() so any leave lines / calendar-based WORK100 for
        salaried staff who DO have a calendar are preserved.
        """
        res = super().get_worked_day_lines(contracts, date_from, date_to)
        WorkType = self.env['timesheet.work.type']
        for contract in contracts:
            employee = contract.employee_id
            if not employee:
                continue
            # Aggregate this employee's unpaid timesheet days/hours per work
            # type for the period (parameterised SQL — one round-trip).
            self.env.cr.execute(
                """
                SELECT work_type_id,
                       COALESCE(SUM(days), 0.0)      AS days,
                       COALESCE(SUM(hour_days), 0.0) AS hours
                FROM account_analytic_line
                WHERE employee_id = %s
                  AND date >= %s AND date <= %s
                  AND work_type_id IS NOT NULL
                  AND COALESCE(is_payroll_paid, FALSE) = FALSE
                GROUP BY work_type_id
                """,
                (employee.id, date_from, date_to),
            )
            rows = self.env.cr.dictfetchall()
            if not rows:
                continue
            wt_map = {
                wt.id: wt for wt in WorkType.browse([r['work_type_id'] for r in rows])
            }
            for row in rows:
                wt = wt_map.get(row['work_type_id'])
                if not wt or not wt.code:
                    continue
                if not row['days'] and not row['hours']:
                    continue
                res.append({
                    'name': wt.name or wt.code,
                    'sequence': (wt.sequence or 10),
                    'code': wt.code,
                    'number_of_days': row['days'] or 0.0,
                    'number_of_hours': row['hours'] or 0.0,
                    'contract_id': contract.id,
                })
        return res

    # ------------------------------------------------------------------
    # Inputs — OCA native override (expense reimburse / deduction)
    # ------------------------------------------------------------------
    @api.model
    def get_inputs(self, contracts, date_from, date_to):
        """Append EXPENSEREM / EXPENSE input lines to OCA's native inputs."""
        res = super().get_inputs(contracts, date_from, date_to)
        for contract in contracts:
            employee = contract.employee_id
            if not employee:
                continue
            rem_amt = ded_amt = 0.0
            for expense in self._get_payslip_expenses(employee, date_from, date_to):
                if expense.payment_mode == 'own_account':
                    rem_amt += expense.total_amount
                elif (expense.payment_mode == 'company_account'
                      and expense.emp_contribution > 0.0):
                    ded_amt += expense.emp_contribution
            if rem_amt:
                res.append({
                    'name': _('Expense Reimburse'),
                    'code': 'EXPENSEREM',
                    'amount': rem_amt,
                    'contract_id': contract.id,
                })
            if ded_amt:
                res.append({
                    'name': _('Expense Deduction'),
                    'code': 'EXPENSE',
                    'amount': ded_amt,
                    'contract_id': contract.id,
                })
        return res

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _link_period_timesheets(self):
        """Stamp the payslip onto its period's unpaid timesheet lines so the
        UI 'Timesheet Lines' tab and the mark-as-paid flow have a recordset.
        Called from compute_sheet (after worked-days are computed)."""
        for slip in self:
            if not slip.employee_id or not slip.date_from or not slip.date_to:
                continue
            lines = self.env['account.analytic.line'].search([
                ('employee_id', '=', slip.employee_id.id),
                ('date', '>=', slip.date_from),
                ('date', '<=', slip.date_to),
                ('work_type_id', '!=', False),
                ('custom_payslip_id', '=', False),
            ])
            if lines:
                slip.timesheet_ids = [(4, l.id) for l in lines]

    def compute_sheet(self):
        res = super().compute_sheet()
        self._link_period_timesheets()
        return res

    def _get_payslip_expenses(self, employee, date_from, date_to):
        """No hr.expense.sheet model in Odoo 19; filter by expense state."""
        return self.env['hr.expense'].search([
            ('employee_id', '=', employee.id),
            ('include_salary', '=', True),
            ('slip_id', '=', False),
            ('state', 'in', ['approved', 'posted']),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
        ])

    def action_payslip_done(self):
        """OCA: super() computes the sheet, then we stamp timesheets/expenses.

        We ALSO write the real wage cost back onto each timesheet line's
        analytic `amount` (negative, per analytic convention) so the
        project / analytic account reports the HKD cost from wages, day by
        day, on the same lines that were manually entered. The cost is taken
        from the employee's per-work-type rate table on the payslip's
        hr.version contract (days x daily_rate + hour_days x hourly_rate).
        """
        res = super().action_payslip_done()
        for payslip in self:
            contract = payslip.contract_id
            for line in payslip.timesheet_ids:
                vals = {
                    'is_payroll_paid': True,
                    'custom_payslip_id': payslip.id,
                }
                if contract:
                    # Writing `amount` alone is NOT intercepted by the
                    # account.analytic.line.write() override (which only fires
                    # on days/hour_days), so unit_amount (hours) is preserved.
                    vals['amount'] = line._compute_wage_cost(contract)
                line.with_context(skip_warning=True).write(vals)
            for expense in self._get_payslip_expenses(
                payslip.employee_id, payslip.date_from, payslip.date_to
            ):
                expense.slip_id = payslip.id
                expense.write({'state': 'done'})
        return res

    def show_paid_timesheetline(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Paid Timesheet Lines'),
            'res_model': 'account.analytic.line',
            'view_mode': 'list,form',
            'domain': [('custom_payslip_id', '=', self.id)],
        }
