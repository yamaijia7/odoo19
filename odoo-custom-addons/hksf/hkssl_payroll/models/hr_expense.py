# -*- coding: utf-8 -*-
# CRITICAL FIX audit #1: hr.expense.sheet does NOT exist in Odoo 19.
# Expense sheet grouping was removed. All logic is on hr.expense directly.
# CRITICAL FIX audit #6: Odoo 19 expense states: draft->approved->posted->paid
from odoo import api, fields, models


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    include_salary = fields.Boolean(
        string='Include in Payslip',
        default=False,
        help='When checked, this expense will be reimbursed or deducted via the next payslip.',
    )
    slip_id = fields.Many2one(
        'hr.payslip', string='Payslip', copy=False, readonly=True,
        help='Payslip this expense was settled through.',
    )
    emp_contribution = fields.Float(
        string='Employee Contribution',
        help='Amount the employee owes back (deducted from payslip).',
    )
    company_contribution = fields.Float(
        string='Company Contribution',
        compute='_compute_company_contribution',
        store=True,
    )

    @api.depends('total_amount', 'emp_contribution')
    def _compute_company_contribution(self):
        for rec in self:
            rec.company_contribution = rec.total_amount - rec.emp_contribution
