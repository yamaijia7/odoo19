# -*- coding: utf-8 -*-
{
    'name': 'HKSF Payroll Timesheet',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Payroll',
    'summary': 'Timesheet-based payroll for scaffolding workers – links approved timesheets to payslip salary rules',
    'description': '''
HKSF Payroll Timesheet
======================
Full port of construction_contracting_payroll + construction_contracting_payroll_extends
for Odoo 19 CE using OCA payroll as base.

Features
--------
- Work Types (timesheet.work.type) with hourly rate, daily rate, R-factor and DR-factor
- Contract Work Type Rates (contract.worktype) per hr.contract
- Timesheet lines track days / hours separately; auto-compute unit_amount
- Approved timesheet lines auto-linked to payslip on computation
- TIMESHEET_WORKING_DAYS worked-day line injected into every payslip
- Salary rule helper: WORK_TYPE_TOTAL(code) for use in structures
- Mark timesheet lines as paid when payslip is confirmed
- Smart buttons on payslip: view linked timesheets
    ''',
    'author': 'H.K. Scafframe Systems Limited',
    'website': 'https://hkssl.com',
    'license': 'LGPL-3',
    'depends': [
        'payroll',
        'hr_timesheet',
        'project',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/decimal_precision_data.xml',
        'views/timesheet_work_type_views.xml',
        'views/hr_contract_views.xml',
        'views/account_analytic_line_views.xml',
        'views/hr_payslip_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
