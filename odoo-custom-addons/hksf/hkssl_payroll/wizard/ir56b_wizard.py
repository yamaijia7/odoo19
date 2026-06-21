# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class Ir56bWizard(models.TransientModel):
    _name = 'hkssl.ir56b.wizard'
    _description = 'IR56B Annual Tax Report Wizard'

    tax_year_start = fields.Date(required=True, default=lambda s: s._default_start())
    tax_year_end = fields.Date(required=True, default=lambda s: s._default_end())
    employee_ids = fields.Many2many('hr.employee', string='Employees (blank = all)')
    line_ids = fields.One2many('hkssl.ir56b.line', 'wizard_id', string='Lines', readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('computed', 'Computed')], default='draft')

    @api.model
    def _default_start(self):
        from datetime import date
        t = date.today()
        y = t.year if t.month >= 4 else t.year - 1
        return date(y, 4, 1)

    @api.model
    def _default_end(self):
        from datetime import date
        t = date.today()
        y = t.year if t.month >= 4 else t.year - 1
        return date(y + 1, 3, 31)

    def action_compute(self):
        self.ensure_one()
        if self.tax_year_end <= self.tax_year_start:
            raise UserError(_('Tax year end must be after tax year start.'))
        self.line_ids.unlink()
        domain = [
            ('state', '=', 'done'),
            ('date_from', '>=', self.tax_year_start),
            ('date_to', '<=', self.tax_year_end),
        ]
        if self.employee_ids:
            domain.append(('employee_id', 'in', self.employee_ids.ids))
        payslips = self.env['hr.payslip'].search(domain)
        if not payslips:
            raise UserError(_('No confirmed payslips found for the selected period.'))
        data = {}
        for slip in payslips:
            eid = slip.employee_id.id
            data.setdefault(eid, {'employee_id': eid, 'gross': 0.0, 'mpf_ee': 0.0, 'mpf_er': 0.0})
            for line in slip.line_ids:
                if line.category_id.code == 'GROSS':
                    data[eid]['gross'] += line.total
                elif line.code == 'MPF_EE':
                    data[eid]['mpf_ee'] += abs(line.total)
                elif line.code == 'MPF_ER':
                    data[eid]['mpf_er'] += abs(line.total)
        self.write({
            'line_ids': [(0, 0, {
                'wizard_id': self.id,
                'employee_id': v['employee_id'],
                'total_remuneration': v['gross'],
                'mpf_employee': v['mpf_ee'],
                'mpf_employer': v['mpf_er'],
            }) for v in data.values()],
            'state': 'computed',
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hkssl.ir56b.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_print(self):
        self.ensure_one()
        if self.state != 'computed':
            raise UserError(_('Please compute the report first.'))
        return self.env.ref('hkssl_payroll.action_report_ir56b').report_action(self)


class Ir56bLine(models.TransientModel):
    _name = 'hkssl.ir56b.line'
    _description = 'IR56B Line'
    _order = 'employee_id'

    wizard_id = fields.Many2one('hkssl.ir56b.wizard', ondelete='cascade')
    employee_id = fields.Many2one('hr.employee', required=True)
    identification_id = fields.Char(
        related='employee_id.identification_id', string='HKID', readonly=True
    )
    total_remuneration = fields.Float(string='Total Remuneration (HKD)', digits=(16, 2))
    mpf_employee = fields.Float(string='MPF Employee (HKD)', digits=(16, 2))
    mpf_employer = fields.Float(string='MPF Employer (HKD)', digits=(16, 2))
