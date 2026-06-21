# -*- coding: utf-8 -*-
# OCA-NATIVE REFACTOR (2026-06-21)
# Removed _get_work_type_salary() and get_analytic_line(): work-type quantities
# now flow through native worked-days lines (see hr_payslip.get_worked_day_lines)
# and salary rules read worked_days.<CODE> directly. This model now only owns
# the HK MPF logic (monthly floor/cap + Industry Scheme per-day brackets) and
# the attendance worked-days helper used as a fallback by salary rules.
# FIX: parameterised SQL. FIX: tz-aware datetime bounds for attendance.
from datetime import datetime, time

from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    mpf_scheme_type = fields.Selection(
        [('monthly', 'Monthly (Salaried)'),
         ('industry', 'Industry Scheme (Daily-paid Casual)')],
        string='MPF Scheme Type',
        default='monthly',
        required=True,
        help='Monthly: 5% with $7,100 floor / $30,000 cap (per MPFA).\n'
             'Industry Scheme: per-day bracket lookup for casual daily-paid workers.',
    )
    industry_scheme_id = fields.Many2one(
        'payroll.industry.scheme',
        string='MPF / Industry Scheme',
        domain=[('active', '=', True)],
    )

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        default_scheme = self.env['payroll.industry.scheme'].search(
            [('active', '=', True)], limit=1
        )
        for emp in employees:
            # only auto-assign the daily bracket table for industry-scheme staff
            if emp.mpf_scheme_type == 'industry' and not emp.industry_scheme_id and default_scheme:
                emp.industry_scheme_id = default_scheme
        return employees

    # --- MPF constants (current MPFA figures) ---
    _MPF_RATE = 0.05
    _MPF_MONTHLY_MIN = 7100.0    # floor: below this the employee is exempt
    _MPF_MONTHLY_MAX = 30000.0   # cap: contributions capped at $1,500 each

    def _get_mpf_employer_contribution(self, gross, date_from=None, date_to=None):
        self.ensure_one()
        if self.mpf_scheme_type == 'industry':
            return self._get_industry_scheme_mpf(
                date_from, date_to, 'amount_payable_employer')
        # monthly salaried: employer always contributes 5%, capped at $1,500
        if gross <= 0:
            return 0.0
        capped = min(gross, self._MPF_MONTHLY_MAX)
        return round(capped * self._MPF_RATE, 0)

    def _get_mpf_employee_contribution(self, gross, date_from=None, date_to=None):
        self.ensure_one()
        if self.mpf_scheme_type == 'industry':
            return self._get_industry_scheme_mpf(
                date_from, date_to, 'amount_payable_employee')
        # monthly salaried: employee exempt below the floor
        if gross < self._MPF_MONTHLY_MIN:
            return 0.0
        capped = min(gross, self._MPF_MONTHLY_MAX)
        return round(capped * self._MPF_RATE, 0)

    def _get_industry_scheme_mpf(self, date_from, date_to, field_name):
        """Per-day Industry Scheme MPF: bucket each worked day's daily relevant
        income against the daily bracket table, then sum across the period.
        Restores the v11 per-day behaviour using parameterised SQL."""
        self.ensure_one()
        if not self.industry_scheme_id or not date_from or not date_to:
            return 0.0
        self.env.cr.execute(
            """
            SELECT date, SUM(ABS(amount)) AS daily_income
            FROM account_analytic_line
            WHERE employee_id = %s AND date >= %s AND date <= %s
              AND work_type_id IS NOT NULL
            GROUP BY date
            """,
            (self.id, date_from, date_to),
        )
        brackets = self.industry_scheme_id.industry_scheme_line_ids
        total = 0.0
        for row in self.env.cr.dictfetchall():
            income = row['daily_income'] or 0.0
            for bracket in brackets:
                if bracket.from_amount < income <= bracket.to_amount:
                    total += bracket[field_name]
                    break
        return total

    def _get_attendance_worked_days(self, date_from, date_to):
        self.ensure_one()
        # Build proper naive UTC datetime bounds instead of string concatenation.
        # hr.attendance.check_in/out are stored in UTC, so the payslip period
        # (date objects) is converted to [00:00:00, 23:59:59].
        start_dt = datetime.combine(fields.Date.to_date(date_from), time.min)
        end_dt = datetime.combine(fields.Date.to_date(date_to), time.max)
        attendances = self.env['hr.attendance'].search([
            ('employee_id', '=', self.id),
            ('check_in', '>=', fields.Datetime.to_string(start_dt)),
            ('check_in', '<=', fields.Datetime.to_string(end_dt)),
            ('check_out', '!=', False),
        ])
        unique_dates = set()
        total_hours = 0.0
        for att in attendances:
            unique_dates.add(att.check_in.date())
            total_hours += (att.check_out - att.check_in).total_seconds() / 3600.0
        return {'days': float(len(unique_dates)), 'hours': round(total_hours, 4)}
