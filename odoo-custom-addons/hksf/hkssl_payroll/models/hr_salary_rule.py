# -*- coding: utf-8 -*-
# FIX: write() override so rounding fires on rule recompute, not just create
from odoo import api, fields, models


class HrSalaryRule(models.Model):
    _inherit = 'hr.salary.rule'

    rule_decimal_precision = fields.Integer(
        string='Decimal Precision',
        default=-1,
        help='Decimal places to round the computed amount to. '
             '0 = integer (required for MPF statutory amounts). -1 = no rounding.',
    )


class HrPayslipLine(models.Model):
    _inherit = 'hr.payslip.line'

    def _apply_rule_rounding(self):
        precision = self.salary_rule_id.rule_decimal_precision
        if precision >= 0:
            rounded = round(self.amount, precision)
            if rounded != self.amount:
                super(HrPayslipLine, self).write({'amount': rounded})

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            line._apply_rule_rounding()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'amount' in vals:
            for line in self:
                line._apply_rule_rounding()
        return res
