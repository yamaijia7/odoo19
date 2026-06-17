# -*- coding: utf-8 -*-
from odoo import fields, models


class HrContract(models.Model):
    _inherit = 'hr.contract'

    work_type_ids = fields.One2many(
        'contract.worktype', 'contract_id',
        string='Work Type Rates',
        help='Define hourly and daily rates for each work type applicable to this contract.')

    def action_compute_worktype_rates(self):
        """Button: auto-compute daily_rate and rate from contract wage
        using each line's dr_factor and r_factor.

        Formula:
          daily_rate = wage / dr_factor
          rate       = daily_rate / r_factor
        """
        for contract in self:
            for line in contract.work_type_ids:
                if line.dr_factor:
                    line.daily_rate = contract.wage / line.dr_factor
                if line.r_factor and line.daily_rate:
                    line.rate = line.daily_rate / line.r_factor
