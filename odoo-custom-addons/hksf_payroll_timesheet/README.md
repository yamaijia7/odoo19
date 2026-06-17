# hksf_payroll_timesheet

Odoo 19 CE module for **H.K. Scafframe Systems Limited**.

Links approved employee timesheets to payslips, enabling timesheet-based
payroll calculation for scaffolding workers.

## Features

| Feature | Detail |
|---|---|
| Work Types | `timesheet.work.type` – Normal, Overtime, Sunday, PH, etc. with R/DR factors |
| Contract Rates | `contract.worktype` – per-contract hourly + daily rates per work type |
| Timesheet Fields | `days`, `hour_days`, `work_type_id`, `is_payroll_paid` on `account.analytic.line` |
| Payslip Summary | `payslip.totalhour` – hours + days per work type tab on payslip |
| Worked Day Line | `TIMESHEET_WORKING_DAYS` injected into every payslip automatically |
| Salary Rule Helper | `employee._get_work_type_salary('CODE', payslip.id)` for use in salary rules |
| Mark as Paid | Confirming a payslip sets `is_payroll_paid=True` on all linked timesheet lines |
| Smart Button | Payslip shows count of linked timesheet lines with drill-down |

## Salary Rule Usage

In your salary structure, add a rule with **Python Code** computation:

```python
# Normal Working Hours pay
result = employee._get_work_type_salary('NORM', payslip.id)
```

```python
# Overtime pay
result = employee._get_work_type_salary('OT', payslip.id)
```

## Depends

- `payroll` (OCA community payroll)
- `hr_timesheet`
- `project`

## Upgrade from Odoo 11

This module consolidates:
- `construction_contracting_payroll` (Probuse)
- `construction_contracting_payroll_extends` (HKSF)
- `bi_hr_payroll_timesheet`
- `hr_payroll_timesheet`
- `hr_worked_days_from_timesheet`
- `ki_payroll_extend` / `odoo_payroll_extend`
