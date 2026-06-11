# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class ManualMonthUpdateWizard(models.TransientModel):
    """Select Days popup (ported from Odoo 11 manual_customer_invoice).

    Lets the user type a number of days that overrides the Start/End date
    calculation on the active line. Works for both:
      - sale.order.line   -> writes wizard_days
      - account.move.line -> writes days     (Odoo 11 account.invoice.line)
    """
    _name = 'manual.month.update.wizard'
    _description = 'Manual Invoice — Select Days'

    days_count = fields.Float(
        string='Days',
        required=True,
    )

    def action_update_days(self):
        active_id = self._context.get('active_id', False)
        active_model = self._context.get('active_model', False)
        if not active_id or not active_model:
            return
        active_line = self.env[active_model].sudo().browse(active_id)
        if active_model == 'sale.order.line':
            active_line.wizard_days = self.days_count
        elif active_model == 'account.move.line':
            active_line.days = self.days_count
        return {'type': 'ir.actions.act_window_close'}
