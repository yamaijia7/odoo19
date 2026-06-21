# -*- coding: utf-8 -*-
"""Unified "Create Invoice" dispatcher wizard.

A thin front-door wizard that lets the user pick ONE invoice kind
(Rental/Service, Repair + Damage, or Lost Material) from a single form and
then delegates to the existing, battle-tested billing logic:

  - rental -> hksf.delivery.invoice.wizard.action_create_delivery_invoice()
  - damage -> damage.lost.invoice.wizard.action_create_invoice()
  - lost   -> sale.order.action_outstanding_create_lost_invoice()

It contains NO billing logic of its own: each branch builds the target
wizard (with active_id in context so their own default_get / _onchange run)
and calls the target's own action. This keeps the Service-journal rule,
charge-first logic, overbill guards and partial-lost tracking in exactly one
place.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class HksfCreateInvoiceWizard(models.TransientModel):
    _name = 'hksf.create.invoice.wizard'
    _description = 'Create Invoice (Unified Dispatcher)'

    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        required=True,
        default=lambda self: self.env.context.get('active_id'),
    )
    company_id = fields.Many2one(
        related='sale_order_id.company_id',
        string='Company',
        readonly=True,
    )

    invoice_kind = fields.Selection(
        selection=[
            ('rental', 'Rental / Service'),
            ('damage', 'Repair + Damage'),
            ('lost', 'Lost Material'),
            ('standard', 'Standard (Sales Order)'),
        ],
        string='Invoice Kind',
        required=True,
        default='rental',
        help="Rental/Service, Repair+Damage and Lost route to the HKSF billing "
             "flows. Standard runs Odoo's native invoicing (down-payment / "
             "regular invoice) for plain Sales orders.",
    )

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------
    journal_id = fields.Many2one(
        'account.journal',
        string='Journal',
        domain=[('type', '=', 'sale')],
        help="Leave blank to let the target flow pick its own default journal "
             "(rental orders auto-route service-charge invoices to the Service "
             "journal regardless of this value).",
    )

    # ------------------------------------------------------------------
    # Rental subset (shown when invoice_kind == 'rental')
    # Mirrors hksf.delivery.invoice.wizard scalar inputs.
    # ------------------------------------------------------------------
    charge_type = fields.Selection(
        selection=[
            ('monthly', 'Monthly'),
            ('weekly', 'Weekly'),
        ],
        string='Charge Type',
        default='monthly',
    )
    invoice_for = fields.Selection(
        selection=[
            ('rent', 'Rent'),
            ('damage', 'Damage'),
            ('rent_e_w_d', 'Rent with Damage'),
        ],
        string='Invoice For',
        default='rent',
    )
    minimum_charge_method = fields.Selection(
        selection=[
            ('normal', 'Normal'),
            ('first_charge', 'Charge First'),
        ],
        string='Minimum Charge Method',
        default='first_charge',
    )
    start_date = fields.Date(string='Period Start')
    end_date = fields.Date(string='Period End')

    # ------------------------------------------------------------------
    # Damage subset (shown when invoice_kind == 'damage')
    # date range is forwarded to the damage wizard; lines are pulled by
    # the damage wizard's own _onchange_order so all overbill / partial
    # tracking stays there.
    # ------------------------------------------------------------------
    date_from = fields.Date(string='From Date')
    date_to = fields.Date(string='To Date')

    # ==================================================================
    # default_get — pre-fill rental scalars + period from the SO, mirroring
    # the delivery wizard's defaults so the rental branch feels identical.
    # ==================================================================
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        from dateutil.relativedelta import relativedelta
        today = fields.Date.context_today(self)
        # Rental invoices are raised in the first week of the FOLLOWING month,
        # so the billed period defaults to the PREVIOUS calendar month.
        prev_month_first = today.replace(day=1) - relativedelta(months=1)
        res.setdefault('start_date', prev_month_first)
        # Last day of the previous month = day before the current month's 1st
        res.setdefault('end_date', today.replace(day=1) - relativedelta(days=1))
        active_id = self.env.context.get('active_id')
        if active_id:
            order = self.env['sale.order'].browse(active_id)
            # Default the kind to match the order type: rental orders default to
            # the rental flow; plain Sales orders default to standard Odoo
            # invoicing.
            if 'invoice_kind' in fields_list:
                res['invoice_kind'] = (
                    'rental' if order.custom_sale_type == 'rent' else 'standard'
                )
            if order.charge_type:
                res['charge_type'] = order.charge_type
            if order.minimum_charge_method:
                res['minimum_charge_method'] = order.minimum_charge_method
        return res

    @api.onchange('start_date', 'charge_type')
    def _onchange_billing_period(self):
        """Mirror delivery wizard period auto-calc for the rental branch."""
        from datetime import timedelta
        from dateutil.relativedelta import relativedelta
        for rec in self:
            if rec.invoice_kind != 'rental' or not rec.start_date:
                continue
            if rec.charge_type == 'weekly':
                rec.end_date = rec.start_date + timedelta(days=6)
            else:
                rec.end_date = (
                    rec.start_date.replace(day=1) + relativedelta(months=1)
                ) - relativedelta(days=1)

    # ==================================================================
    # Dispatch
    # ==================================================================
    def action_create(self):
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_("No sale order found."))

        # Every target flow resolves the SO from active_id in context.
        ctx = dict(self.env.context, active_id=self.sale_order_id.id,
                   active_ids=[self.sale_order_id.id],
                   active_model='sale.order')

        if self.invoice_kind == 'rental':
            return self._dispatch_rental(ctx)
        if self.invoice_kind == 'damage':
            return self._dispatch_damage(ctx)
        if self.invoice_kind == 'lost':
            return self._dispatch_lost()
        if self.invoice_kind == 'standard':
            return self._dispatch_standard(ctx)
        raise UserError(_("Unknown invoice kind."))

    # ------------------------------------------------------------------
    def _dispatch_rental(self, ctx):
        """Delegate to the delivery invoice wizard."""
        vals = {
            'charge_type': self.charge_type or 'monthly',
            'invoice_for': self.invoice_for or 'rent',
            'minimum_charge_method': self.minimum_charge_method or 'first_charge',
            'start_date': self.start_date,
            'end_date': self.end_date,
        }
        if self.journal_id:
            vals['journal_id'] = self.journal_id.id
        wiz = self.env['hksf.delivery.invoice.wizard'].with_context(ctx).create(vals)
        return wiz.action_create_delivery_invoice()

    def _dispatch_damage(self, ctx):
        """Delegate to the damage/lost (Repair + Damage) wizard.

        Build the wizard with active_id in context (its default_get sets the
        SO + repair journal), forward the date range, then trigger its own
        _onchange_order to pull the eligible lines. This keeps overbill /
        partial-lost logic inside the damage wizard.
        """
        wiz = self.env['damage.lost.invoice.wizard'].with_context(ctx).create({
            'sale_order_id': self.sale_order_id.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        if self.journal_id:
            wiz.journal_id = self.journal_id
        # Populate line_ids via the wizard's own onchange (respects date range,
        # remaining-qty filter and include defaults).
        wiz._onchange_order()
        if not wiz.line_ids.filtered(lambda l: l.include):
            raise UserError(_(
                "No uninvoiced Repair / Damage lines found for this order"
                "%s." % (
                    " in the selected date range" if (self.date_from or self.date_to)
                    else ""
                )
            ))
        return wiz.action_create_invoice()

    def _dispatch_lost(self):
        """Delegate to the sale order's one-click lost-material flow."""
        return self.sale_order_id.action_outstanding_create_lost_invoice()

    def _dispatch_standard(self, ctx):
        """Delegate to Odoo's native Create Invoice wizard
        (sale.advance.payment.inv) for plain Sales orders.

        We open the standard wizard rather than invoke it headlessly so the
        user still gets the native down-payment / regular-invoice choices.
        """
        if self.sale_order_id.state not in ('sale', 'done'):
            raise UserError(_(
                "The sale order must be confirmed before creating a standard "
                "invoice."
            ))
        action = self.env['ir.actions.act_window']._for_xml_id(
            'sale.action_view_sale_advance_payment_inv'
        )
        action['context'] = dict(ctx)
        return action
