# -*- coding: utf-8 -*-
from odoo import models, fields, api, _, tools
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta


class HksfDeliveryInvoiceWizard(models.TransientModel):
    """Main delivery billing wizard supporting Normal and Charge First methods.

    Consolidates:
     - odoo_delivery_invoice  (base wizard)
     - odoo_delivery_invoice_extend (Charge First + minimum_charge_method)
     - mass_delivery_invoice_create (consolidated wizard)
    """
    _name = 'hksf.delivery.invoice.wizard'
    _description = 'Delivery Invoice Wizard'

    # ------------------------------------------------------------------
    # Header fields
    # ------------------------------------------------------------------

    start_date = fields.Date(
        string='Start Date',
        required=True,
    )
    end_date = fields.Date(
        string='End Date',
        required=True,
    )
    charge_type = fields.Selection(
        selection=[
            ('monthly', 'Monthly'),
            ('weekly', 'Weekly'),
        ],
        string='Charge Type',
        required=True,
        default='monthly',
    )
    invoice_for = fields.Selection(
        selection=[
            ('rent', 'Rent'),
            ('damage', 'Damage'),
            ('rent_e_w_d', 'Rent with Damage'),
        ],
        string='Invoice For',
        required=True,
        default='rent',
    )
    minimum_charge_method = fields.Selection(
        selection=[
            ('normal', 'Normal'),
            ('first_charge', 'Charge First'),
        ],
        string='Minimum Charge Method',
        required=True,
        default='first_charge',
    )
    journal_id = fields.Many2one(
        'account.journal',
        string='Journal',
        domain=[('type', '=', 'sale')],
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
    )

    # ------------------------------------------------------------------
    # default_get — pre-fill from sale order
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Default billing period to the current month (auto mode)
        today = fields.Date.context_today(self)
        res.setdefault('start_date', today.replace(day=1))
        res.setdefault(
            'end_date',
            (today.replace(day=1) + relativedelta(months=1)) - relativedelta(days=1),
        )
        active_id = self._context.get('active_id')
        if active_id:
            order = self.env['sale.order'].browse(active_id)
            if order.charge_type:
                res['charge_type'] = order.charge_type
            if order.minimum_charge_method:
                res['minimum_charge_method'] = order.minimum_charge_method
            # Default journal
            journal = self.env['account.journal'].search([
                ('type', '=', 'sale'),
                ('company_id', '=', (order.company_id or self.env.company).id),
            ], limit=1)
            if journal:
                res['journal_id'] = journal.id
            res['company_id'] = (order.company_id or self.env.company).id
        return res

    # ------------------------------------------------------------------
    # Onchange: auto-calculate the billing period unless Manual is checked
    # ------------------------------------------------------------------

    @api.onchange('start_date', 'charge_type')
    def _onchange_billing_period(self):
        for rec in self:
            if not rec.start_date:
                continue
            if rec.charge_type == 'weekly':
                rec.end_date = rec.start_date + timedelta(days=6)
            else:
                # Last day of the month containing start_date
                rec.end_date = (
                    rec.start_date.replace(day=1) + relativedelta(months=1)
                ) - relativedelta(days=1)

    # ------------------------------------------------------------------
    # Main action
    # ------------------------------------------------------------------

    def action_create_delivery_invoice(self):
        self.ensure_one()
        if self.minimum_charge_method == 'first_charge' and self.invoice_for in ('rent', 'rent_e_w_d'):
            return self._create_charge_first_invoice()
        return self._create_normal_invoice()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_sale_order(self):
        active_id = self._context.get('active_id')
        if not active_id:
            raise UserError(_("No active sale order found."))
        return self.env['sale.order'].browse(active_id)

    def _prepare_invoice_vals(self, order):
        """Return base dict for account.move creation."""
        return {
            'move_type': 'out_invoice',
            'partner_id': order.partner_id.id,
            'partner_shipping_id': order.partner_shipping_id.id,
            'invoice_date': self.end_date,
            'rental_sale_id': order.id,
            'user_id': order.user_id.id,
            'invoice_origin': order.name,
            'ref': order.client_order_ref or order.name,
            'company_id': order.company_id.id,
            'invoice_payment_term_id': order.payment_term_id.id,
            'journal_id': self.journal_id.id if self.journal_id else False,
            'charge_type': self.charge_type,
            'minimum_charge_method': self.minimum_charge_method,
            'rental_invoice_type': 'rent' if self.invoice_for in ('rent', 'rent_e_w_d') else 'damage',
            'narration': order.note and tools.html2plaintext(order.note) or '',
            # Propagate the Manual Invoice flag from the sale order so the
            # created invoice uses days-based (manual) pricing, matching the
            # Odoo 11 manual_customer_invoice behaviour. Without this the SO
            # checkbox had no effect on the generated delivery invoice.
            'is_manual_invoice': order.is_manual_invoice,
        }

    def _strip_tax(self, vals):
        """Force invoice lines to carry no tax (Hong Kong: no sales tax/VAT).
        Clears any default product/fiscal-position tax that Odoo would auto-apply.
        """
        if isinstance(vals, list):
            for v in vals:
                v['tax_ids'] = [(5, 0, 0)]
            return vals
        vals['tax_ids'] = [(5, 0, 0)]
        return vals

    def _get_product_account(self, product, company):
        """Resolve the income account for a RENTAL invoice line.

        Cascade (first non-empty wins):
          1. product's Rental Income Account   (property_rental_income_account_id)
          2. category's Rental Income Account   (property_rental_income_account_categ_id)
          3. category's standard income account (property_account_income_categ_id)
          4. empty -> caller omits account_id and Odoo's standard
             _compute_account_id picks the default (never blocks invoicing).

        company_dependent fields are read in the target company's context so a
        multi-company setup resolves the right per-company account.
        """
        tmpl = product.product_tmpl_id
        categ = product.categ_id
        if company:
            tmpl = tmpl.with_company(company)
            categ = categ.with_company(company)
        return (
            tmpl.property_rental_income_account_id
            or categ.property_rental_income_account_categ_id
            or categ.property_account_income_categ_id
        )

    def _get_transport_charge(self, transport, order=None):
        """Return the effective price for a transport.charge line.

        Mirrors Odoo 11 (odoo_delivery_invoice/wizard/create_invoice.py
        _get_transport_charge): if the SAME transport product also exists on a
        sale order line, the SALES ORDER price wins; otherwise fall back to the
        price entered on the Transport Info line of the transfer.
        """
        if order is None:
            active_id = self._context.get('active_id', False)
            order = self.env['sale.order'].sudo().browse(active_id) if active_id else None
        if order:
            so_line = order.order_line.filtered(
                lambda t: t.product_id == transport.product_id
            )
            if so_line:
                return so_line[0].price_unit
        return transport.price_unit

    def _get_transport_description(self, transport, fallback_name, order=None):
        """Build human-readable transport line description.

        Like Odoo 11, if the SAME transport product exists on a sale order line
        the SO line's description takes precedence; otherwise use the Transport
        Info line description. The picking license plate / truck size are then
        prefixed for readability.
        """
        if order is None:
            active_id = self._context.get('active_id', False)
            order = self.env['sale.order'].sudo().browse(active_id) if active_id else None
        base = transport.name or fallback_name
        if order:
            so_line = order.order_line.filtered(
                lambda t: t.product_id == transport.product_id
            )
            if so_line and so_line[0].name:
                base = so_line[0].name
        picking = transport.picking_id
        parts = []
        if picking.license_plate:
            parts.append(picking.license_plate)
        if picking.truck_size_selection:
            parts.append(dict(
                self.env['stock.picking']._fields['truck_size_selection'].selection
            ).get(picking.truck_size_selection, ''))
        parts.append(base)
        return ' / '.join(filter(None, parts)) or base

    # ------------------------------------------------------------------
    # Normal billing
    # ------------------------------------------------------------------

    def _create_normal_invoice(self):
        order = self._get_sale_order()
        PickingObj = self.env['stock.picking'].sudo()

        delivery_ids = PickingObj.search([
            ('sale_id', '=', order.id),
            ('picking_type_code', '=', 'outgoing'),
            ('state', '=', 'done'),
        ], order='id asc')

        if not delivery_ids:
            raise UserError(_("No invoiceable delivery orders available."))

        invoice = self.env['account.move'].sudo().create(self._prepare_invoice_vals(order))
        period_days = 7 if self.charge_type == 'weekly' else 30
        line_vals_list = []
        # Collection credit lines are grouped (mirrors charge-first path) so a
        # single collection that touches several delivery moves consolidates to
        # one negative credit line. Built via _process_collection_credit_line
        # and flushed after the per-delivery loop.
        collection_lines = {}
        collection_transport_lines = []

        for picking in delivery_ids:
            if not picking.scheduled_date_only:
                raise UserError(
                    _("Please set Scheduled Date on picking '%s' before invoicing.") % picking.name
                )
            if picking.scheduled_date_only > self.end_date:
                continue
            # Skip already invoiced in this range
            if (picking.invoice_id
                    and picking.invoice_id.invoice_date
                    and self.start_date <= picking.invoice_id.invoice_date <= self.end_date
                    and picking.invoice_id.state != 'cancel'):
                continue

            del_date = picking.scheduled_date_only
            end_date = self.end_date
            # ----------------------------------------------------------------
            # Strict 30-day (or 7-day weekly) billing grid.
            #
            # HKSF treats every month as exactly 30 chargeable days,
            # regardless of the real calendar length (Jan 31, Feb 28/29 are
            # all billed on a 30-day grid). The first month is prorated from
            # the delivery day to the end of the grid, INCLUSIVE of the
            # delivery day itself (always at least 1 day charged):
            #
            #     rent_days = (period_days - delivery_day_index) + 1
            #
            # Examples (monthly, period_days=30):
            #   delivered 07th -> (30 - 7) + 1 = 24 days
            #   delivered 20th -> (30 - 20) + 1 = 11 days
            #   delivered 30th/31st -> 1 day (floored)
            #
            # This deliberately uses the day-of-month index instead of a
            # calendar (end_date - del_date) diff so that short months
            # (Feb) and long months (31-day) bill identically -- matching
            # the Odoo 11 production invoices.
            # ----------------------------------------------------------------
            # ----------------------------------------------------------------
            # Balance brought forward (standing inventory).
            #
            # When the delivery happened in a PRIOR period (del_date earlier
            # than the invoice window start), the goods are still on hire and
            # carry forward as a full "One Month" (or "One Week") balance --
            # NOT a first-month proration. This mirrors the Odoo 11 follow-up
            # invoices (seq 1 "Balance brought from last month", days==30,
            # range = full invoice period). The normal path previously lacked
            # this branch, so a Feb delivery billed in March was mis-rendered
            # as a fresh delivery (seq 2) instead of the balance group (seq 1).
            # ----------------------------------------------------------------
            is_previous = self.start_date > del_date
            if is_previous:
                picking.is_from_previous_month = True
                rent_days = period_days          # full "One Month" / "One Week"
                line_start = self.start_date     # ( 01/03/2023 - 31/03/2023 )
                line_end = end_date
            else:
                day_index = min(del_date.day, period_days)
                rent_days = (period_days - day_index) + 1
                if rent_days > period_days:
                    rent_days = period_days
                if rent_days < 1:
                    rent_days = 1
                line_start = del_date
                line_end = end_date

            for move in picking.move_ids.filtered(
                lambda m: m.state == 'done'
                and m.sale_line_id
                and m.sale_line_id.line_type == 'rental'
            ):
                if is_previous:
                    # Balance brought forward = standing quantity that was still
                    # on hire at the START of this invoice window, billed EVERY
                    # month as recurring rent.
                    #
                    # It must NOT subtract invoiced_quantity: that field sums all
                    # prior out-invoice lines, so for stock already billed last
                    # month it would be 0 and the whole balance group would
                    # vanish (the exact bug that hid the March balance group on
                    # follow-up invoices).
                    #
                    # It must ALSO NOT subtract returns that happen WITHIN this
                    # window: those are shown separately as the collection credit
                    # line (seq 3). Netting them out of the balance too would
                    # double-count the reduction. So we start from the full
                    # delivered quantity and subtract only returns whose
                    # collection move is dated BEFORE this window's start_date
                    # (i.e. already gone in a prior period).
                    def _return_date(rmove):
                        # Canonical collection date in this module is the
                        # picking-level scheduled_date_only; fall back to the
                        # move's own date / create_date.
                        pick = rmove.picking_id
                        if pick and pick.scheduled_date_only:
                            return pick.scheduled_date_only
                        if rmove.date:
                            return rmove.date.date()
                        if rmove.create_date:
                            return rmove.create_date.date()
                        return None
                    prior_returned = sum(
                        h.return_qty
                        for h in move.delivery_return_history_ids
                        if h.return_move_id
                        and h.return_move_id.state == 'done'
                        and _return_date(h.return_move_id)
                        and _return_date(h.return_move_id) < self.start_date
                    )
                    invoiceable = move.quantity - prior_returned
                else:
                    # First-month delivery: only bill what has not already been
                    # invoiced, so re-running the wizard never double-bills a
                    # fresh delivery.
                    invoiceable = move.quantity - move.invoiced_quantity
                if invoiceable <= 0.0:
                    continue
                line_price = move.sale_line_id.price_unit if move.sale_line_id else move.price_unit
                price_unit = (abs(line_price) / period_days) * rent_days
                line_vals_list.append({
                    'move_id': invoice.id,
                    'product_id': move.product_id.id,
                    'quantity': invoiceable,
                    'product_uom_id': move.product_uom.id,
                    'price_unit': price_unit,
                    'custom_price_unit': line_price,
                    'days': rent_days,
                    'start_date': line_start,
                    'end_date': line_end,
                    'picking_id': picking.id,
                    'is_from_previous_month': is_previous,
                    'custom_move_id': move.id,
                    'custom_sale_line_id': move.sale_line_id.id if move.sale_line_id else False,
                    'delivery_history_ids': [(4, h.id) for h in move.delivery_return_history_ids],
                })
                picking.write({'invoice_id': invoice.id, 'last_invoice_date': fields.Date.today()})

                # ---- Collection credit lines ----
                # Normal path mirrors Charge-First: every return history on this
                # delivery move that falls in the invoice window produces a
                # negative credit line. In Odoo 11 BOTH the base (normal) and
                # extended (charge-first) wizards ran this loop; v19 was missing
                # it on the normal path, so collections never showed on invoices
                # of orders using minimum_charge_method = 'normal'.
                for history in move.delivery_return_history_ids:
                    self._process_collection_credit_line(
                        collection_lines, collection_transport_lines, history,
                        move, picking, invoice, order,
                        # wiz_end / orig_start / orig_end for the normal grid are
                        # simply the wizard window (matches O11 base path which
                        # uses end_date_wiz = self.end_date for the return calc).
                        self.end_date, self.start_date, self.end_date, del_date,
                        period_days, line_price, move.sale_line_id.name if move.sale_line_id else False
                    )

        self._add_transport_lines(line_vals_list, delivery_ids, invoice, order, period_days)

        if line_vals_list:
            self.env['account.move.line'].sudo().create(self._strip_tax(line_vals_list))

        # Flush grouped collection credit lines
        collection_line_vals = [v for v in collection_lines.values() if v.get('quantity', 0.0) != 0.0]
        if collection_line_vals:
            self.env['account.move.line'].sudo().create(self._strip_tax(collection_line_vals))

        # Flush collection transport lines
        for tl in collection_transport_lines:
            self.env['account.move.line'].sudo().create(self._strip_tax(tl[2]))

        if not invoice.invoice_line_ids:
            invoice.unlink()
            raise UserError(_("No lines were generated. The invoice has been discarded."))

        return self._open_invoice(invoice)

    # ------------------------------------------------------------------
    # Charge First billing
    # ------------------------------------------------------------------

    def _create_charge_first_invoice(self):
        """Full Charge First logic ported from odoo_delivery_invoice_extend."""
        order = self._get_sale_order()
        PickingObj = self.env['stock.picking'].sudo()

        delivery_ids = PickingObj.search([
            ('sale_id', '=', order.id),
            ('picking_type_code', '=', 'outgoing'),
            ('state', '=', 'done'),
        ], order='id asc')

        # Collection pickings in the date range that are not yet done
        # (transport charges may need to be billed even before validation)
        non_delivery_ids = PickingObj.search([
            '|',
            ('sale_id', '=', order.id),
            ('custom_sale_order_id', '=', order.id),
            ('picking_type_code', 'in', ['outgoing', 'incoming']),
            ('state', 'in', ['assigned', 'waiting', 'confirmed']),
            ('scheduled_date_only', '>=', self.start_date),
            ('scheduled_date_only', '<=', self.end_date),
        ], order='id asc')

        if not delivery_ids and not non_delivery_ids:
            raise UserError(_("No invoiceable delivery orders available."))

        invoice = self.env['account.move'].sudo().create(self._prepare_invoice_vals(order))
        period_days = 7 if self.charge_type == 'weekly' else 30
        lines = {}         # grouped line dict — key → line_vals
        transport_lines = []

        for picking in delivery_ids:
            if not picking.scheduled_date_only:
                raise UserError(
                    _("Please set Scheduled Date on picking '%s' before invoicing.") % picking.name
                )
            if picking.scheduled_date_only > self.end_date:
                continue
            # Skip already invoiced in range
            if (picking.invoice_id
                    and picking.invoice_id.invoice_date
                    and self.start_date <= picking.invoice_id.invoice_date <= self.end_date
                    and picking.invoice_id.state != 'cancel'):
                continue

            orig_start = self.start_date
            orig_end = self.end_date

            for move in picking.move_ids.filtered(
                lambda m: m.state == 'done'
                and m.sale_line_id
                and m.sale_line_id.line_type == 'rental'
            ):
                # Skip fully invoiced
                if (move.quantity - move.invoiced_quantity <= 0.0) and \
                   (move.quantity - move.new_invoicing_quantity <= 0.0):
                    continue

                # Lost product invoice lines for this move in date range
                lost_invoice_lines = move.custom_invoice_line_ids.filtered(
                    lambda l: l.move_id.rental_invoice_type == 'lost'
                    and l.move_id.invoice_date
                    and self.start_date <= l.move_id.invoice_date <= self.end_date
                )

                del_date = picking.scheduled_date_only
                line_price = move.sale_line_id.price_unit if move.sale_line_id else move.price_unit
                discount = move.sale_line_id.discount if move.sale_line_id else 0.0
                description = move.sale_line_id.name if move.sale_line_id else False

                is_previous = self.start_date > del_date
                start_date = del_date
                add_one_day = False

                if is_previous:
                    picking.is_from_previous_month = True
                    next_date = del_date + relativedelta(months=1)
                    if self.charge_type == 'weekly':
                        next_date = del_date + timedelta(days=7)
                    if next_date < orig_start:
                        next_date = orig_start
                    wiz_start = next_date
                    wiz_end = orig_end
                else:
                    wiz_start = del_date
                    last_inv = del_date + relativedelta(months=1) - relativedelta(days=1)
                    month_last = del_date + relativedelta(day=31)
                    if month_last.day == 31:
                        add_one_day = True
                    if self.charge_type == 'weekly':
                        last_inv = del_date + timedelta(days=6)
                    wiz_end = last_inv

                rent_days = (wiz_end - wiz_start).days + 1

                if self.charge_type == 'monthly':
                    if orig_end.day in (28, 29):
                        rent_days += 30 - orig_end.day

                if is_previous and orig_end.day > 30:
                    rent_days -= 1
                if rent_days > 30:
                    rent_days = 30

                start_str = wiz_start.strftime('%Y-%m-%d')
                end_str = wiz_end.strftime('%Y-%m-%d')
                group_by = picking if not is_previous else ('is_from_previous_month', rent_days)

                quantity = move.quantity - move.invoiced_quantity
                lost_qty = sum(ll.quantity for ll in lost_invoice_lines) if lost_invoice_lines else 0.0
                if lost_qty > 0.0:
                    quantity += lost_qty

                key = (rent_days, move.product_id, line_price, group_by)
                price_unit = (abs(line_price) / period_days) * rent_days

                if key not in lines:
                    line_vals = {
                        'move_id': invoice.id,
                        'product_id': move.product_id.id,
                        'quantity': quantity,
                        'product_uom_id': move.product_uom.id,
                        'start_date': start_str,
                        'end_date': end_str,
                        'picking_id': picking.id,
                        'is_from_previous_month': is_previous,
                        'price_unit': price_unit,
                        'custom_price_unit': line_price,
                        'days': rent_days,
                        'discount': discount,
                        'custom_sale_line_id': move.sale_line_id.id if move.sale_line_id else False,
                        'custom_move_id': move.id,
                        'delivery_history_ids': [(4, h.id) for h in move.delivery_return_history_ids],
                    }
                    if description:
                        line_vals['name'] = description
                    # Route rental revenue to the product/category Rental Income
                    # Account when configured; otherwise leave account_id unset so
                    # Odoo resolves its standard default (blank is always safe).
                    rental_account = self._get_product_account(
                        move.product_id, order.company_id)
                    if rental_account:
                        line_vals['account_id'] = rental_account.id
                    lines[key] = line_vals
                else:
                    lines[key]['quantity'] += quantity
                    for h in move.delivery_return_history_ids:
                        lines[key]['delivery_history_ids'].append((4, h.id))

                picking.write({'invoice_id': invoice.id, 'last_invoice_date': fields.Date.today()})

                # ---- Lost product credit lines ----
                for lost_line in lost_invoice_lines.filtered(lambda l: l.quantity > 0.0):
                    self._process_lost_credit_line(
                        lines, lost_line, move, picking, invoice,
                        wiz_end, orig_end, del_date, period_days, line_price, description
                    )

                # ---- Collection credit lines ----
                for history in move.delivery_return_history_ids:
                    self._process_collection_credit_line(
                        lines, transport_lines, history, move, picking, invoice,
                        order, wiz_end, orig_start, orig_end, del_date,
                        period_days, line_price, description
                    )

            # Transport on outgoing delivery
            if picking.transportation_method == 'by_us':
                if picking.scheduled_date_only and \
                        self.start_date <= picking.scheduled_date_only <= self.end_date:
                    for tc in picking.transport_charge_ids:
                        if not tc.invoice_id or tc.invoice_id.state == 'cancel':
                            self._add_single_transport_line(
                                transport_lines, tc, picking, invoice, order, period_days
                            )
                            picking.is_create_transport_invoice = True
                            tc.is_create_transport_invoice = True
                            tc.invoice_id = invoice.id

        # Non-delivery transport lines
        for picking in non_delivery_ids:
            if picking.transportation_method == 'by_us':
                for tc in picking.transport_charge_ids:
                    if not tc.invoice_id or tc.invoice_id.state == 'cancel':
                        self._add_single_transport_line(
                            transport_lines, tc, picking, invoice, order, period_days
                        )
                        picking.is_create_transport_invoice = True
                        tc.is_create_transport_invoice = True
                        tc.invoice_id = invoice.id

        # Write all grouped lines
        line_vals_to_create = [v for v in lines.values() if v.get('quantity', 0.0) != 0.0]
        if line_vals_to_create:
            self.env['account.move.line'].sudo().create(self._strip_tax(line_vals_to_create))

        # Write transport lines
        for tl in transport_lines:
            self.env['account.move.line'].sudo().create(self._strip_tax(tl[2]))

        if not invoice.invoice_line_ids:
            invoice.unlink()
            raise UserError(_("No lines were generated. The invoice has been discarded."))

        return self._open_invoice(invoice)

    # ------------------------------------------------------------------
    # Credit line helpers
    # ------------------------------------------------------------------

    def _process_lost_credit_line(
        self, lines, lost_line, move, picking, invoice,
        wiz_end, orig_end, del_date, period_days, line_price, description
    ):
        """Create a negative credit line for a lost-product invoice."""
        apply_min = move.product_id.product_tmpl_id.ia_apply_minimum_charge
        lost_inv = lost_line.move_id
        lost_date = lost_inv.invoice_date
        if not lost_date or not (self.start_date <= lost_date <= self.end_date):
            return
        if lost_inv.state == 'cancel':
            return

        return_days = (wiz_end - lost_date).days
        month_day = 30
        if orig_end.day != 30 and lost_date.month != del_date.month:
            return_days += month_day - orig_end.day
        rental_days = (lost_date - del_date).days + 1

        minimum_charge_days = 0.0
        if self.charge_type == 'weekly':
            if apply_min and rental_days < 7:
                minimum_charge_days = 7 - rental_days
        else:
            if apply_min and rental_days < 30:
                minimum_charge_days = 30 - rental_days

        return_price = ((abs(line_price) / period_days) * (return_days - minimum_charge_days)) * -1

        key = (-1 * return_days, move.product_id, lost_line.quantity, line_price)
        if key not in lines:
            lv = {
                'move_id': invoice.id,
                'product_id': move.product_id.id,
                'quantity': lost_line.quantity,
                'product_uom_id': move.product_uom.id,
                'price_unit': return_price,
                'custom_price_unit': line_price,
                'start_date': lost_date,
                'end_date': wiz_end,
                'days': return_days * -1,
                'custom_move_id': move.id,
                'minimum_charge_days': minimum_charge_days,
            }
            if description:
                lv['name'] = description
            lines[key] = lv
        else:
            lines[key]['quantity'] += lost_line.quantity

    def _process_collection_credit_line(
        self, lines, transport_lines, history, move, picking, invoice,
        order, wiz_end, orig_start, orig_end, del_date,
        period_days, line_price, description
    ):
        """Create a negative credit line for a return collection."""
        apply_min = move.product_id.product_tmpl_id.ia_apply_minimum_charge
        return_picking = history.return_move_id.picking_id if history.return_move_id else False
        if not return_picking:
            return
        scheduled_date = return_picking.scheduled_date_only
        if not scheduled_date:
            return
        if not (self.start_date <= scheduled_date <= self.end_date):
            return
        if return_picking.state != 'done':
            return

        # Reset temp qty
        history.return_move_id.tmp_invoiced_qty = 0.0

        end_str = wiz_end.strftime('%Y-%m-%d')
        return_days = (wiz_end - scheduled_date).days
        month_day = 30
        if orig_end.day != 30 and scheduled_date.month != del_date.month:
            return_days += month_day - orig_end.day
            if orig_end.day == 31 and return_days == -1:
                return_days += 1
        elif orig_end.month == 2 and self.charge_type == 'monthly':
            return_days += month_day - orig_end.day

        rental_days = (scheduled_date - del_date).days
        del_month_end = (del_date + relativedelta(day=31)).day
        if del_month_end < 31 and self.charge_type == 'monthly':
            rental_days += 1
        elif self.charge_type == 'weekly':
            rental_days += 1

        minimum_charge_days = 0.0
        if self.charge_type == 'weekly':
            if apply_min and rental_days < 7:
                minimum_charge_days = 7 - rental_days
        else:
            if apply_min and rental_days < 30:
                minimum_charge_days = 30 - rental_days

        return_qty = history.return_qty
        if return_qty <= 0.0:
            return

        day_group_by = return_days
        return_price = ((abs(line_price) / period_days) * (return_days - minimum_charge_days)) * -1

        key = (-1 * day_group_by, move.product_id, line_price, return_picking, minimum_charge_days)

        if key not in lines:
            # NOTE (v19.0.1.21.0): the historical clamp here re-derived per-batch
            # capacity from the product-wide ``tmp_invoiced_qty`` of OTHER
            # collections. That double-counts when the SAME delivery move is
            # legitimately returned across MULTIPLE collections (e.g. WH/OUT/00007
            # delivered 80, returned 31 in Apr + 49 in May). The Apr collection's
            # return move carried tmp_invoiced_qty=61 (its whole-product total),
            # which wrongly subtracted 30 from the May 49 -> 19, collapsing the
            # 64-unit credit to 34 (=49-15). The FIFO ``_resync_collection_histories``
            # on stock.picking already caps each delivery move at its own quantity
            # across all collections, so ``history.return_qty`` is final and
            # correct here. We keep only the per-history safety floor.
            if return_qty > history.delivered_qty:
                return_qty = history.delivered_qty

            start_str = scheduled_date.strftime('%Y-%m-%d')
            lv = {
                'move_id': invoice.id,
                'product_id': move.product_id.id,
                'quantity': return_qty,
                'product_uom_id': move.product_uom.id,
                'picking_id': return_picking.id,
                'price_unit': return_price,
                'custom_price_unit': line_price,
                'start_date': start_str,
                'end_date': end_str,
                'days': return_days,
                'custom_move_id': move.id,
                'minimum_charge_days': minimum_charge_days,
                'delivery_history_ids': [(4, history.id)],
            }
            if description:
                lv['name'] = description
            history.return_move_id.tmp_invoiced_qty = return_qty
            lines[key] = lv

            # Transport on collection picking
            if return_picking.transportation_method == 'by_us':
                for tc in return_picking.transport_charge_ids:
                    if not tc.invoice_id or tc.invoice_id.state == 'cancel':
                        self._add_single_transport_line(
                            transport_lines, tc, return_picking, invoice, order,
                            period_days, start_date_override=picking.scheduled_date_only
                        )
                        picking.is_create_transport_invoice = True
                        tc.is_create_transport_invoice = True
                        tc.invoice_id = invoice.id

            history.return_move_id.write({'last_invoice_date': fields.Date.today()})
            return_picking.write({'invoice_id': invoice.id, 'last_invoice_date': fields.Date.today()})
        else:
            qty = lines[key]['quantity']
            del_qty = min(history.return_qty, history.delivered_qty)
            lines[key]['quantity'] = qty + del_qty
            lines[key]['delivery_history_ids'].append((4, history.id))
            history.return_move_id.tmp_invoiced_qty = lines[key]['quantity']

    def _add_single_transport_line(
        self, transport_lines, tc, picking, invoice, order,
        period_days, start_date_override=None
    ):
        charge = self._get_transport_charge(tc, order)
        description = self._get_transport_description(tc, tc.name, order)
        lv = {
            'move_id': invoice.id,
            'product_id': tc.product_id.id,
            'quantity': tc.product_uom_qty,
            'product_uom_id': tc.product_uom.id,
            'price_unit': charge,
            'is_transport_product': True,
            'picking_id': picking.id,
            'custom_price_unit': charge,
            'start_date': start_date_override or picking.scheduled_date_only,
            'end_date': self.end_date,
            'name': description,
        }
        transport_lines.append((0, 0, lv))

    def _add_transport_lines(self, line_vals_list, delivery_ids, invoice, order, period_days):
        """Add transport lines for Normal billing path."""
        for picking in delivery_ids:
            if picking.transportation_method != 'by_us':
                continue
            if not (picking.scheduled_date_only
                    and self.start_date <= picking.scheduled_date_only <= self.end_date):
                continue
            for tc in picking.transport_charge_ids:
                if not tc.invoice_id or tc.invoice_id.state == 'cancel':
                    charge = self._get_transport_charge(tc, order)
                    desc = self._get_transport_description(tc, tc.name, order)
                    line_vals_list.append({
                        'move_id': invoice.id,
                        'product_id': tc.product_id.id,
                        'quantity': tc.product_uom_qty,
                        'product_uom_id': tc.product_uom.id,
                        'price_unit': charge,
                        'is_transport_product': True,
                        'picking_id': picking.id,
                        'custom_price_unit': charge,
                        'start_date': picking.scheduled_date_only,
                        'end_date': self.end_date,
                        'name': desc,
                    })
                    picking.is_create_transport_invoice = True
                    tc.is_create_transport_invoice = True
                    tc.invoice_id = invoice.id

    # ------------------------------------------------------------------
    # Return invoice action
    # ------------------------------------------------------------------

    def _open_invoice(self, invoice):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': invoice.id,
            'target': 'current',
        }
