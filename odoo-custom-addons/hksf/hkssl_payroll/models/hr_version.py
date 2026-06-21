# -*- coding: utf-8 -*-
# Extends hr.version (OCA payroll 19.0 contract record).
from odoo import fields, models


class HrVersion(models.Model):
    _inherit = 'hr.version'

    work_type_ids = fields.One2many(
        'contract.worktype', 'contract_id', string='Work Type Rates')

    def action_compute_worktype_rates(self):
        """Back-solve daily/hourly rates from the contract wage and the work
        type's factors. Zero-guarded to avoid ZeroDivisionError on blank
        factors."""
        for contract in self:
            for line in contract.work_type_ids:
                if line.dr_factor:
                    line.daily_rate = contract.wage / line.dr_factor
                if line.r_factor and line.daily_rate:
                    line.rate = line.daily_rate / line.r_factor

    def get_worktype_rate(self, code):
        """Return (daily_rate, hourly_rate) for a work-type code on this
        contract, or (0.0, 0.0) if not configured. Consumed by salary rules."""
        self.ensure_one()
        line = self.work_type_ids.filtered(
            lambda l: l.work_type_id.code == code
        )[:1]
        if not line:
            return (0.0, 0.0)
        return (line.daily_rate, line.rate)
