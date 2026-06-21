# -*- coding: utf-8 -*-
from decimal import Decimal
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    """Extend account.move with rental invoicing header fields.

    Header fields relocated from hksf_rental_invoice, plus the charge_type /
    minimum_charge_method fields written by the delivery invoice wizard.
    Line-level billing fields live on account.move.line below.
    """
    _inherit = 'account.move'

    rental_subject_line = fields.Text(
        string='Rental Subject',
        copy=False,
        readonly=False,
    )
    cc_partner_id = fields.Many2one(
        'res.partner',
        string='CC',
        copy=False,
        help="Contact shown on the 'CC :' line of the rental invoice header. "
             "Auto-filled from the sale order's contact; can be overridden.",
    )
    rental_sale_id = fields.Many2one(
        'sale.order',
        string='Source Sale Order',
        copy=False,
        readonly=True,
    )
    rental_salesperson_id = fields.Many2one(
        'res.users',
        string='Salesperson (Rental)',
        copy=False,
    )
    rental_invoice_type = fields.Selection(
        selection=[
            ('rent', 'Rental'),
            ('sale', 'Sale'),
            ('repair', 'Repair'),
            ('damage', 'Damage'),
            ('lost', 'Lost Material'),
        ],
        string='Invoice Type',
        copy=False,
    )
    rental_charge_type = fields.Selection(
        selection=[('monthly', 'Monthly'), ('weekly', 'Weekly')],
        string='Charge Type',
        copy=False,
    )
    # Written by hksf.delivery.invoice.wizard._prepare_invoice_vals
    charge_type = fields.Selection(
        selection=[('monthly', 'Monthly'), ('weekly', 'Weekly')],
        string='Rental Charge Type',
        copy=False,
    )
    minimum_charge_method = fields.Selection(
        selection=[
            ('normal', 'Normal'),
            ('first_charge', 'Charge First'),
        ],
        string='Minimum Charge Method',
        copy=False,
    )
    # Manual invoice mode (ported from Odoo 11 manual_customer_invoice)
    is_manual_invoice = fields.Boolean(
        string='Manual Invoice',
        copy=False,
    )

    # ------------------------------------------------------------------
    # Release service charges when the rental invoice is cancelled, so the
    # erection / dismantling charge can be billed again on a later invoice.
    # Additive override -> core cancellation runs first (super()), we only
    # clear our OWN custom stamp afterwards. No core state is forced.
    # ------------------------------------------------------------------
    def button_cancel(self):
        res = super().button_cancel()
        cancelled = self.filtered(lambda m: m.state == 'cancel')
        if cancelled:
            charges = self.env['service.charge'].sudo().search([
                ('invoice_id', 'in', cancelled.ids),
            ])
            if charges:
                # Clear ONLY the invoice stamp so is_billed recomputes to False
                # and the charge becomes billable again. We deliberately KEEP
                # the user's 'Create Service Invoice' tick -- they marked it for
                # billing on purpose, and a cancellation should not silently
                # untick it (otherwise the next wizard run would skip it).
                charges.write({'invoice_id': False})
        return res

    # ------------------------------------------------------------------
    # Reverse a Lost invoice and resume rental on the lost qty
    # ------------------------------------------------------------------
    def action_reverse_lost_resume_rental(self, reverse_stock=True):
        """Undo a Lost-Material invoice so the lost qty starts being rent-charged
        again -- for the common case where a client asked for a "cut off" (lost
        write-off) that they NEVER paid, so rental simply continues.

        What it does (all reversible bookkeeping, no destructive stock cancel):
          1. Finds the auto-generated lost-return picking(s) for this invoice's
             order (``is_lost_return=True``).
          2. Unlinks the ``delivery.return.history`` records that tied those
             returns to the delivery moves. Rental billing only nets a return
             while its history points at a DONE return move, so dropping the
             history immediately makes ``new_return_quantity`` fall back to 0 and
             the rent resume from the NEXT invoice -- no past invoice is touched.
          3. Voids the lost-return picking (state -> cancel) so it no longer
             shows as a live collection / does not affect stock going forward.
          4. Releases the invoiced lost lines (``invoice_id`` -> False) so the
             Outstanding-Products balance shows the qty as on-hire again.
          5. Cancels THIS lost invoice (draft -> cancel). It is left in the
             system as a cancelled record for audit; it is never auto-deleted.

        IMPORTANT -- Odoo logic is preserved end to end:
          * The validated lost-return picking is a REAL stock movement (it
            genuinely brought the qty back on-hand). We therefore NEVER force
            ``state='cancel'`` on a done move -- that would desync quants and
            valuation. If a physical reversal is wanted we use the STANDARD
            ``stock.return.picking`` wizard to create a proper opposite move so
            the ledger stays balanced.
          * Rent resumes purely by severing the module's own
            ``delivery.return.history`` link -- a custom relation, not core
            stock -- which is the same mechanism the rental wizard reads.
          * The lost invoice is cancelled through the NORMAL accounting flow
            (button_draft -> button_cancel), never deleted.

        :param reverse_stock: when True (default) also create a standard return
            picking that takes the qty back OUT of the warehouse, restoring the
            on-hand level to what it was before the lost write-off (client keeps
            the goods on hire). When False, the stock stays returned and only
            the rental billing resumes.

        Safe to run only on a Lost invoice. Idempotent.
        """
        Picking = self.env['stock.picking'].sudo()
        for move in self:
            if move.rental_invoice_type != 'lost':
                raise UserError(_(
                    "Reverse Lost is only available on a Lost Material invoice."
                ))
            order = move.rental_sale_id
            if not order:
                raise UserError(_(
                    "This lost invoice has no source rental order to resume."
                ))

            # 1. The lost lines invoiced by THIS move.
            lost_lines = order.collection_lost_material_ids.filtered(
                lambda l: l.invoice_id == move and l.type == 'lost'
            )

            # 2. The DONE lost-return picking(s) for this order.
            lost_returns = Picking.search([
                ('custom_sale_order_id', '=', order.id),
                ('is_lost_return', '=', True),
                ('state', '=', 'done'),
            ])

            for pick in lost_returns:
                return_moves = pick.move_ids
                # 2a. Sever ONLY the custom rental link -> rent resumes. This
                #     touches NO core stock state / quant / valuation record.
                histories = self.env['delivery.return.history'].sudo().search([
                    ('return_move_id', 'in', return_moves.ids),
                ])
                if histories:
                    histories.with_context(force_unlink=True).unlink()
                # 2b. Optionally reverse the PHYSICAL stock the Odoo-correct
                #     way (standard return wizard -> proper opposite move; core
                #     handles quants + valuation). Never force a done move to
                #     cancel.
                if reverse_stock and return_moves:
                    self._reverse_lost_picking_stock(pick)
                # 2c. Untag the old (now-reversed) lost-return picking so it is
                #     no longer treated as a LIVE lost return. Without this it
                #     would be re-discovered the next time a lost invoice is
                #     created/reversed for this order, causing duplicate /
                #     phantom returns. We also detach its moves from the order
                #     (``custom_sale_id``) so they stop counting as COLLECTED on
                #     the Outstanding page -- otherwise the qty would never
                #     reappear as on-hire and a future lost invoice could not be
                #     built. These are CUSTOM rental flags only; we do NOT touch
                #     core stock state / quants / valuation.
                untag = {'is_lost_return': False}
                if 'custom_sale_order_id' in pick._fields:
                    untag['custom_sale_order_id'] = False
                pick.write(untag)
                if 'custom_sale_id' in return_moves._fields:
                    return_moves.write({'custom_sale_id': False})

            # 3. Drop the now-orphaned lost lines so the qty reappears purely
            #    as on-hire (delivered - collected) on the Outstanding page.
            #    We DELETE rather than merely clear ``invoice_id``: a released
            #    but kept line would be re-picked as an uninvoiced lost line on
            #    the next lost invoice, double-counting the qty.
            if lost_lines:
                lost_lines.with_context(force_unlink=True).unlink()

            # 4. Recompute the Outstanding page so the balance shows again.
            order.action_compute_outstanding_products()

            # 5. Cancel this lost invoice (kept for audit, not deleted).
            if move.state == 'posted':
                move.button_draft()
            if move.state != 'cancel':
                move.button_cancel()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Lost invoice reversed"),
                'message': _(
                    "The lost return was undone and rental will resume on the "
                    "affected quantity from the next invoice. The lost invoice "
                    "has been cancelled (kept for audit)."
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def _reverse_lost_picking_stock(self, pick):
        """Reverse the PHYSICAL stock of a done lost-return picking using the
        STANDARD Odoo ``stock.return.picking`` wizard, so quants and valuation
        are handled by core (no manual state writes).

        The wizard builds an opposite picking (here: outgoing, taking the qty
        back out to the customer). We flag the resulting picking so it is never
        treated as a rental collection.
        """
        pick.ensure_one()
        ReturnWizard = self.env['stock.return.picking'].sudo()
        # default_get populates product_return_moves from the source picking.
        wiz = ReturnWizard.with_context(
            active_id=pick.id,
            active_model='stock.picking',
        ).create({'picking_id': pick.id})
        # Return the full done quantity of each line.
        for rline in wiz.product_return_moves:
            rline.quantity = rline.move_id.quantity
        action = wiz.action_create_returns()
        rev_picking = self.env['stock.picking'].sudo().browse(action.get('res_id'))
        if rev_picking:
            # Not a rental collection; keep it off the rental nets and tag it.
            vals = {'is_lost_return': False}
            if 'custom_sale_order_id' in rev_picking._fields:
                vals['custom_sale_order_id'] = False
            rev_picking.write(vals)
            rev_picking.move_ids.write({'custom_sale_id': False})
            # Validate it so the stock actually goes back out (core handles
            # quants/valuation). Mirror the qty onto the moves first.
            for mv in rev_picking.move_ids:
                if mv.product_uom_qty and not mv.quantity:
                    mv.quantity = mv.product_uom_qty
            try:
                if not rev_picking.scheduled_date_only:
                    rev_picking.scheduled_date_only = fields.Date.context_today(self)
                res = rev_picking.button_validate()
                if isinstance(res, dict) and rev_picking.state != 'done':
                    # An immediate-transfer/backorder wizard would normally pop
                    # up; in this rental context there is nothing for the user
                    # to confirm, so complete via the standard done action.
                    rev_picking.move_ids._action_done()
            except Exception:
                # Leave the reverse picking in a ready state for the user to
                # validate manually rather than corrupting stock state.
                pass
        return rev_picking

    # ------------------------------------------------------------------
    # Print helpers (ported from Odoo 11 odoo_delivery_invoice_report)
    # ------------------------------------------------------------------
    def change_quantity_format(self, quantity):
        """Render a quantity without trailing zeros (e.g. 12.00 -> 12)."""
        return "%g" % (Decimal(round(quantity or 0.0, 2)))

    def print_cutom_invoice(self):
        """Group invoice lines into the buckets used by the material-rent
        reports. Mirrors the Odoo 11 helper, adapted to account.move(.line).

          1 = Balance Brought From Last Month/Week
          2 = Rental (outgoing deliveries)
          3 = Deduction (incoming collections)
          4 = Transportation Charges
          5 = Lost Products (no picking)
          6 = Erection / dismantling services (is_service_product)
        """
        new_val_dict = {1: {}, 2: {}, 3: {}, 4: {}, 5: {}, 6: {}}

        report_print_type = self._context.get('report_print_type', 'detail_rent')
        AML = self.env['account.move.line'].sudo()

        for rec in self:
            inv_lines = rec.invoice_line_ids.filtered(
                lambda l: l.display_type in (
                    'product', 'cogs',
                    'non_deductible_product', 'non_deductible_product_total',
                )
            )
            if report_print_type == 'material_rent':
                inv_lines = inv_lines.filtered(lambda l: l.price_total != 0.0)

            for line in inv_lines:
                picking = line.picking_id
                code = picking.picking_type_code if picking else False
                if line.is_from_previous_month:                          # Balance brought forward
                    # Standing inventory carried forward is prorated by EACH
                    # line's own delivery date, so two items delivered on
                    # different days (e.g. 04/02 -> 27 days, 07/02 -> 24 days)
                    # carry different ``days``. The group header renders a
                    # single day count (first_line.days), so lines MUST be
                    # split by days -- collapsing them all into one
                    # 'previous_month' group made every balance line inherit a
                    # single, mis-paired header day count. Key by days (mirrors
                    # the seq-5 lost-product grouping).
                    new_val_dict[1].setdefault(line.days, AML)
                    new_val_dict[1][line.days] += line
                elif line.is_service_product:                            # Erection / dismantling services
                    # Group by the service product (the product name already
                    # carries the meaning, e.g. 'Scaffold Erection'); no
                    # separate service-type field needed.
                    key = line.product_id
                    new_val_dict[6].setdefault(key, AML)
                    new_val_dict[6][key] += line
                elif line.is_transport_product:                          # Transport
                    new_val_dict[4].setdefault(picking, AML)
                    new_val_dict[4][picking] += line
                elif picking and code == 'outgoing' and line.price_subtotal >= 0.0:  # Rental
                    new_val_dict[2].setdefault(picking, AML)
                    new_val_dict[2][picking] += line
                elif picking and code == 'incoming':                     # Deduction
                    new_val_dict[3].setdefault(picking, AML)
                    new_val_dict[3][picking] += line
                elif not picking:                                        # Lost products
                    new_val_dict[5].setdefault(line.days, AML)
                    new_val_dict[5][line.days] += line

        # Sort pickings by scheduled date, lines by product name
        for seq in new_val_dict:
            new_val_dict[seq] = dict(sorted(
                new_val_dict[seq].items(),
                key=lambda x: (
                    getattr(x[0], 'scheduled_date_only', None) or ''
                    if not isinstance(x[0], (str, int)) else ''
                ),
            ))
            for k in new_val_dict[seq]:
                new_val_dict[seq][k] = new_val_dict[seq][k].sorted(
                    key=lambda r: r.product_id.name or ''
                )
        return {'new_val_dict': new_val_dict}


class AccountMoveLine(models.Model):
    """Extend invoice lines with delivery-specific billing fields."""
    _inherit = 'account.move.line'

    # ------------------------------------------------------------------
    # Billing period and rental rate fields
    # ------------------------------------------------------------------

    start_date = fields.Date(
        string='Start Date',
        copy=False,
    )
    end_date = fields.Date(
        string='End Date',
        copy=False,
    )
    days = fields.Integer(
        string='Days',
        default=0,
        copy=False,
    )
    custom_price_unit = fields.Float(
        string='Monthly Rate',
        digits='Product Price',
        readonly=True,
        copy=False,
        help="Full monthly/weekly rate before pro-rata calculation.",
    )
    # NOTE: price_unit is left as the NATIVE Odoo 19 field (digits='Product
    # Price'). The pro-rated Unit Price is written at full precision by the
    # delivery-invoice wizard and the native _compute_totals rounds the line
    # subtotal ONCE from it, so Unit x Qty reconciles with the Amount/PDF. To
    # SHOW the extra decimals, set the global 'Product Price' decimal accuracy
    # to 5 (Settings > Technical > Decimal Accuracy). No field-level display
    # override is used.
    monthly_amount = fields.Float(
        string='Period Amount',
        digits='Product Price',
        compute='_compute_monthly_amount',
        store=True,
        help="quantity × monthly rate",
    )
    minimum_charge_days = fields.Float(
        string='Minimum Charge Days',
        digits='Product Unit of Measure',
        default=0.0,
        copy=False,
    )
    # Manual invoice mode: Manual Invoice Month = days / 30
    custom_month = fields.Float(
        string='Manual Invoice Month',
        compute='_compute_custom_month',
        store=True,
        copy=False,
    )

    # ------------------------------------------------------------------
    # Classification / linking fields
    # ------------------------------------------------------------------

    is_transport_product = fields.Boolean(
        string='Is Transport Line',
        default=False,
        copy=False,
    )
    is_service_product = fields.Boolean(
        string='Is Service Line',
        default=False,
        copy=False,
        help="Erection / dismantling service charge billed onto this rental "
             "invoice.",
    )
    service_charge_id = fields.Many2one(
        'service.charge',
        string='Service Charge',
        copy=False,
        index=True,
    )
    is_from_previous_month = fields.Boolean(
        string='From Previous Month',
        default=False,
        copy=False,
    )
    picking_id = fields.Many2one(
        'stock.picking',
        string='Delivery / Collection',
        copy=False,
        index=True,
    )
    custom_sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Sale Order Line',
        copy=False,
        index=True,
    )
    custom_move_id = fields.Many2one(
        'stock.move',
        string='Stock Move',
        copy=False,
        index=True,
    )
    delivery_history_ids = fields.Many2many(
        'delivery.return.history',
        'account_move_line_delivery_history_rel',
        'line_id',
        'history_id',
        string='Return History',
        copy=False,
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('quantity', 'custom_price_unit')
    def _compute_monthly_amount(self):
        for line in self:
            line.monthly_amount = line.quantity * line.custom_price_unit

    @api.depends('days')
    def _compute_custom_month(self):
        """Manual Invoice Month = days / 30 (Odoo 11 manual_customer_invoice)."""
        for line in self:
            line.custom_month = (line.days / 30.0) if line.days else 0.0

    # ------------------------------------------------------------------
    # Manual invoice price override (ported from Odoo 11
    # account.invoice.line._compute_price)
    # ------------------------------------------------------------------
    @api.depends('quantity', 'discount', 'price_unit', 'tax_ids', 'currency_id',
                 'custom_month', 'days', 'move_id.is_manual_invoice')
    def _compute_totals(self):
        """Manual-Invoice price override only.

        Manual Invoice mode: price = price_unit * (1 - disc%) * custom_month,
        with the line total rounded ONCE via the tax engine.

        Standard rental-proration lines use the NATIVE Odoo 19 computation:
        the delivery-invoice wizard writes the FULL-PRECISION pro-rated unit
        into the native `price_unit` (an unbounded NUMERIC column), and Odoo's
        own _compute_totals rounds the line subtotal ONCE from that full unit
        (e.g. 2.133333 * 189 = 403.20). Set the global 'Product Price' decimal
        accuracy to 5 to SHOW those extra decimals so Unit x Qty visibly
        reconciles with the Amount/PDF. No custom rounding logic is needed for
        these lines.
        """
        AccountTax = self.env['account.tax']
        product_types = ('product', 'cogs',
                         'non_deductible_product',
                         'non_deductible_product_total')
        manual_lines = self.filtered(
            lambda l: l.move_id.is_manual_invoice and l.days > 0
            and l.display_type in product_types
            and l.move_id
        )
        # Native computation for everything else (incl. standard rentals,
        # collection credits, transport, service).
        super(AccountMoveLine, self - manual_lines)._compute_totals()

        for line in manual_lines:
            company = line.company_id or self.env.company
            price = line.price_unit * (1.0 - (line.discount or 0.0) / 100.0) * line.custom_month
            base_line = line.move_id._prepare_product_base_line_for_taxes_computation(line)
            # Override the unit price fed into the tax engine with the
            # manual-invoice price; keep quantity = 1 so the engine does not
            # multiply custom_month by quantity twice.
            base_line['price_unit'] = price
            base_line['quantity'] = line.quantity
            base_line['discount'] = 0.0
            AccountTax._add_tax_details_in_base_line(base_line, company)
            AccountTax._round_base_lines_tax_details([base_line], company)
            line.price_subtotal = base_line['tax_details']['total_excluded_currency']
            line.price_total = base_line['tax_details']['total_included_currency']

    @api.onchange('start_date', 'end_date')
    def _onchange_days(self):
        """Recompute Days from Start/End dates in Manual Invoice mode."""
        for rec in self:
            if rec.move_id.is_manual_invoice and rec.start_date and rec.end_date:
                custom_days = (rec.end_date - rec.start_date).days + 1
                if custom_days > 0:
                    rec.days = custom_days

    # ------------------------------------------------------------------
    # Override create — write back invoice line reference on history records
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            if not line.delivery_history_ids:
                continue
            picking = line.picking_id
            if picking and picking.picking_type_code == 'incoming':
                # collection line → mark as return invoice line
                line.delivery_history_ids.write({
                    'return_invoice_line_id': line.id,
                })
            elif picking and picking.picking_type_code == 'outgoing':
                # delivery line → mark as delivery invoice line
                line.delivery_history_ids.write({
                    'deliver_invoice_line_id': line.id,
                })
        return lines
