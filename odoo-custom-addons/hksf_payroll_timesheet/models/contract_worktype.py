# -*- coding: utf-8 -*-
from odoo import fields, models, api


class ContractWorktype(models.Model):
    """Per-contract rate table: one line per work type with hourly and daily rates.
    Rates can be computed automatically from the contract wage using R/DR factors,
    or entered manually.
    """
    _name = 'contract.worktype'
    _description = 'Contract Work Type Rate'

    contract_id = fields.Many2one(
        'hr.contract', string='Contract', required=True, ondelete='cascade', index=True)
    work_type_id = fields.Many2one(
        'timesheet.work.type', string='Work Type', required=True)

    daily_rate = fields.Float(
        string='Daily Rate (HKD)', digits='Payroll Rate')
    rate = fields.Float(
        string='Hourly Rate (HKD)', digits='Payroll Rate')

    r_factor = fields.Float(
        string='R Factor', digits='Payroll Rate', copy=False,
        help='Hourly rate = daily_rate / r_factor')
    dr_factor = fields.Float(
        string='DR Factor', digits='Payroll Rate', copy=False,
        help='Daily rate = contract.wage / dr_factor')

    @api.onchange('work_type_id')
    def _onchange_work_type_id(self):
        if self.work_type_id:
            self.r_factor = self.work_type_id.r_factor
            self.dr_factor = self.work_type_id.dr_factor
