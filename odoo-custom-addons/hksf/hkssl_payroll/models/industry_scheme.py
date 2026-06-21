# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PayrollIndustryScheme(models.Model):
    _name = 'payroll.industry.scheme'
    _description = 'MPF / Industry Scheme'

    name = fields.Char(string='Scheme Name', required=True)
    active = fields.Boolean(default=True)
    industry_scheme_line_ids = fields.One2many(
        'payroll.industry.scheme.line', 'scheme_id', string='Brackets'
    )


class PayrollIndustrySchemeLine(models.Model):
    _name = 'payroll.industry.scheme.line'
    _description = 'MPF Bracket'
    _order = 'from_amount'

    scheme_id = fields.Many2one('payroll.industry.scheme', ondelete='cascade')
    name = fields.Char(string='Name', required=True)
    from_amount = fields.Float(string='From Amount', required=True)
    to_amount = fields.Float(string='To Amount', required=True)
    amount_payable_employer = fields.Float(string='Employer Contribution')
    amount_payable_employee = fields.Float(string='Employee Contribution')

    @api.constrains('from_amount', 'to_amount')
    def _check_amount_range(self):
        for rec in self:
            if rec.to_amount <= rec.from_amount:
                raise ValidationError(
                    'To Amount must be greater than From Amount on bracket "%s".' % rec.name
                )
