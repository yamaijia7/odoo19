# -*- coding: utf-8 -*-
from odoo import fields, models, api


class ContractWorktype(models.Model):
    _name = 'contract.worktype'
    _description = 'Contract Work Type Rate'

    # BUG1 FIX: linked to hr.version, not hr.contract
    contract_id = fields.Many2one('hr.version', string='Contract', required=True, ondelete='cascade', index=True)
    work_type_id = fields.Many2one('timesheet.work.type', string='Work Type', required=True)
    daily_rate = fields.Float(string='Daily Rate (HKD)', digits='Worktype Rates')
    rate = fields.Float(string='Hourly Rate (HKD)', digits='Worktype Rates')
    r_factor = fields.Float(string='R Factor', digits='Worktype Rates', copy=False)
    dr_factor = fields.Float(string='DR Factor', digits='Worktype Rates', copy=False)

    @api.onchange('work_type_id')
    def _onchange_work_type_id(self):
        if self.work_type_id:
            self.r_factor = self.work_type_id.r_factor
            self.dr_factor = self.work_type_id.dr_factor
