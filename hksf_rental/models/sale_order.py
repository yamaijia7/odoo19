# -*- coding: utf-8 -*-
from odoo import models, fields, api, _, tools
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    """
    Consolidated sale.order extension.

    Merges:
      - rental base fields/logic (custom_sale_type, charge_type, totals, tags)
        from hksf_rental
      - lost/R&D pricelists, lost/outstanding lines and their actions
        from hksf_delivery_invoice
      - minimum_charge_method from hksf_rental_invoice
    """

    _inherit = 'sale.order'

    # ------------------------------------------------------------------
    # Type & charge method (from hksf_rental)
    # ------------------------------------------------------------------
    custom_sale_type = fields.Selection(
        selection=[('rent', 'Rental'), ('sale', 'Sales')],
        string='Sale Type',
        default='rent',
    )
    charge_type = fields.Selection(
        selection=[('monthly', 'Monthly'), ('weekly', 'Weekly')],
        string='Charge Type',
        default='monthly',
    )

    # ------------------------------------------------------------------
    # Minimum charge method (relocated from hksf_rental_invoice)
    # ------------------------------------------------------------------
    minimum_charge_method = fields.Selection(
        selection=[
            ('normal', 'Normal'),
            ('first_charge', 'Charge First'),
        ],
        string='Minimum Charge Method',
        default='first_charge',
    )

    # ------------------------------------------------------------------
    # Manual invoice mode (ported from Odoo 11 manual_customer_invoice)
    # When checked, order lines are priced by an explicit Day's / month
    # value entered per line (Start/End Date or the Select Days popup)
    # instead of the standard rental month computation.
    # ------------------------------------------------------------------
    is_manual_invoice = fields.Boolean(
        string='Manual Invoice',
        copy=False,
    )

    # ------------------------------------------------------------------
    # Quotation presentation (from hksf_rental)
    # ------------------------------------------------------------------
    subject_line = fields.Char(string='Subject')
    subject_description = fields.Text(string='Subject Description')
    grand_total_label = fields.Char(string='Grand Total Label')
    custom_chop_and_sign = fields.Boolean(string='Chop & Sign?', default=True)
    body_font_size = fields.Selection(
        selection=[
            ('10', '10px'), ('11', '11px'), ('12', '12px'),
            ('13', '13px'), ('14', '14px'), ('15', '15px'), ('16', '16px'),
        ],
        default='14',
        string='Body Font Size',
    )
    total_pages = fields.Integer(string='Total Pages', default=1)
    sale_person_signature = fields.Many2one(
        'res.users',
        string='Sales Person Signature',
    )

    # ------------------------------------------------------------------
    # Weight / volume aggregates (from hksf_rental)
    # ------------------------------------------------------------------
    total_weight = fields.Float(
        string='Total Weight (kg)',
        compute='_compute_totals',
        store=True,
    )
    total_volume = fields.Float(
        string='Total Volume (m³)',
        compute='_compute_totals',
        store=True,
    )

    # ------------------------------------------------------------------
    # Lost / R&D pricelists (from hksf_delivery_invoice)
    # ------------------------------------------------------------------
    lost_pricelist_id = fields.Many2one(
        'product.pricelist',
        string='Lost / Damage Pricelist',
        help="Pricelist used to stamp each line's Lost Price onto the order. "
             "Damage uses the same price as Lost (damage = lost).",
    )
    repair_pricelist_id = fields.Many2one(
        'product.pricelist',
        string='Repair Pricelist',
        help="Pricelist used to stamp each line's Repair Price onto the order.",
    )
    # NB: r_and_d_pricelist_id was a dead field in earlier versions (declared +
    # shown but never used). Renamed/repurposed to repair_pricelist_id and now
    # actually drives line stamping. A pre-migration copies the old column.

    # ------------------------------------------------------------------
    # Related lines (from hksf_delivery_invoice)
    # ------------------------------------------------------------------
    collection_lost_material_ids = fields.One2many(
        'collection.repair.damage',
        'order_id',
        string='Lost / Damaged Materials',
        copy=False,
    )
    product_outstanding_ids = fields.One2many(
        'product.outstanding',
        'order_id',
        string='Outstanding Products',
        copy=False,
    )

    # ------------------------------------------------------------------
    # Delivery invoices created by the wizard (linked via rental_sale_id)
    # ------------------------------------------------------------------
    delivery_invoice_count = fields.Integer(
        string='Delivery Invoices',
        compute='_compute_delivery_invoice_count',
    )

    # ------------------------------------------------------------------
    # Incoming shipments / Collections smart button (ported from O11)
    # ------------------------------------------------------------------
    incoming_shipment_count = fields.Integer(
        string='Collections',
        compute='_compute_incoming_shipment_count',
    )

    def _incoming_picking_ids(self):
        """Return the set of incoming (collection/return) pickings linked to
        this order. Mirrors O11 logic: moves tagged with custom_sale_id whose
        picking is an incoming type, plus any edited-return pickings that
        reference those via original_return_picking_id."""
        self.ensure_one()
        moves = self.env['stock.move'].sudo().search([
            ('custom_sale_id', '=', self.id),
            ('picking_code', '=', 'incoming'),
        ])
        pickings = moves.mapped('picking_id')
        edited = self.env['stock.picking'].sudo().search([
            ('original_return_picking_id', 'in', pickings.ids),
        ])
        return pickings | edited

    def _compute_incoming_shipment_count(self):
        for order in self:
            order.incoming_shipment_count = len(order._incoming_picking_ids())

    def action_view_incoming_shipment(self):
        """Open the incoming (collection) pickings for this order."""
        self.ensure_one()
        pickings = self._incoming_picking_ids()
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Collections'),
            'res_model': 'stock.picking',
            'context': {'default_custom_sale_order_id': self.id},
        }
        if len(pickings) == 1:
            action.update({
                'view_mode': 'form',
                'res_id': pickings.id,
            })
        else:
            action.update({
                'view_mode': 'list,form',
                'domain': [('id', 'in', pickings.ids)],
            })
        return action

    def _compute_delivery_invoice_count(self):
        """Count invoices created by the delivery wizard (rental_sale_id) plus
        any standard invoice_ids — so the smart button reflects every invoice
        produced from this order."""
        for order in self:
            moves = self.env['account.move'].search([
                ('rental_sale_id', '=', order.id),
                ('move_type', 'in', ('out_invoice', 'out_refund')),
            ])
            moves |= order.invoice_ids.filtered(
                lambda m: m.move_type in ('out_invoice', 'out_refund')
            )
            order.delivery_invoice_count = len(moves)

    def action_view_delivery_invoice(self):
        """Open the invoices linked to this order (wizard + standard)."""
        self.ensure_one()
        moves = self.env['account.move'].search([
            ('rental_sale_id', '=', self.id),
            ('move_type', 'in', ('out_invoice', 'out_refund')),
        ])
        moves |= self.invoice_ids.filtered(
            lambda m: m.move_type in ('out_invoice', 'out_refund')
        )
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Invoices'),
            'res_model': 'account.move',
            'context': {'create': False},
        }
        if len(moves) == 1:
            action.update({
                'view_mode': 'form',
                'res_id': moves.id,
            })
        else:
            action.update({
                'view_mode': 'list,form',
                'domain': [('id', 'in', moves.ids)],
            })
        return action

    # ------------------------------------------------------------------
    # Computed totals (from hksf_rental)
    # ------------------------------------------------------------------
    @api.depends('order_line', 'order_line.subtotal_weight', 'order_line.subtotal_volume')
    def _compute_totals(self):
        """
        total_weight = Σ subtotal_weight
        total_volume = Σ subtotal_volume
        """
        for rec in self:
            rec.total_weight = sum(rec.order_line.mapped('subtotal_weight'))
            rec.total_volume = sum(rec.order_line.mapped('subtotal_volume'))

    # ------------------------------------------------------------------
    # Tag propagation (from hksf_rental)
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        for order in orders:
            tag_ids = []
            for line in order.order_line:
                tag_ids += line.product_id.tag_ids.ids
            if tag_ids:
                order.tag_ids = [(4, tid) for tid in tag_ids]
        return orders

    def write(self, vals):
        res = super().write(vals)
        if 'order_line' in vals:
            for rec in self:
                tag_ids = list(rec.tag_ids.ids)
                for line in rec.order_line:
                    tag_ids += line.product_id.tag_ids.ids
                rec.tag_ids = [(6, 0, list(set(tag_ids)))]
        return res

    # ------------------------------------------------------------------
    # Action: Open the Create Delivery Invoice wizard from the form header
    # ------------------------------------------------------------------
    def action_open_delivery_invoice_wizard(self):
        """Header button: open the delivery invoice wizard for this order."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Delivery Invoice'),
            'res_model': 'hksf.delivery.invoice.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': dict(
                self.env.context,
                active_id=self.id,
                active_ids=self.ids,
                active_model='sale.order',
            ),
        }

    # ------------------------------------------------------------------
    # Action: Compute outstanding products (from hksf_delivery_invoice)
    # ------------------------------------------------------------------
    def action_compute_outstanding_products(self):
        """Recompute delivered vs collected qty per product for this order."""
        for order in self:
            delivery_moves = self.env['stock.move'].search([
                ('custom_sale_id', '=', order.id),
                ('picking_code', '=', 'outgoing'),
                ('state', '=', 'done'),
            ])
            incoming_moves = self.env['stock.move'].search([
                ('custom_sale_id', '=', order.id),
                ('picking_code', '=', 'incoming'),
                ('state', '=', 'done'),
            ])

            products = delivery_moves.mapped('product_id') | incoming_moves.mapped('product_id')

            for product in products:
                prod_delivery = delivery_moves.filtered(lambda m: m.product_id == product)
                prod_incoming = incoming_moves.filtered(lambda m: m.product_id == product)
                delivered_qty = sum(prod_delivery.mapped('quantity'))
                collected_qty = sum(prod_incoming.mapped('quantity'))

                existing = order.product_outstanding_ids.filtered(
                    lambda r: r.product_id == product
                )
                if existing:
                    existing.write({
                        'delivered_qty': delivered_qty,
                        'collected_qty': collected_qty,
                        'outgoing_move_ids': [(6, 0, prod_delivery.ids)],
                        'incoming_move_ids': [(6, 0, prod_incoming.ids)],
                    })
                else:
                    self.env['product.outstanding'].create({
                        'order_id': order.id,
                        'product_id': product.id,
                        'uom_id': product.uom_id.id,
                        'delivered_qty': delivered_qty,
                        'collected_qty': collected_qty,
                        'outgoing_move_ids': [(6, 0, prod_delivery.ids)],
                        'incoming_move_ids': [(6, 0, prod_incoming.ids)],
                    })

    # ------------------------------------------------------------------
    # Repair / Lost price stamping from pricelists
    # ------------------------------------------------------------------
    def _stamp_rd_prices(self, force=False):
        """Stamp each order line's repair_price / lost_price from the order's
        repair_pricelist_id / lost_pricelist_id.

        Design (per user): prices are STATIC to the sale order. They are
        stamped here (auto on confirm, or via the Reload button) and then frozen
        on the line -- editing a pricelist later does NOT change existing orders
        until this runs again. Damage shares the Lost price (damage = lost).

        :param force: if False, only fill lines whose price is still 0.0 (so a
            manual override on a line is preserved). If True (Reload button),
            overwrite every line from the current pricelists.
        """
        for order in self:
            repair_pl = order.repair_pricelist_id
            lost_pl = order.lost_pricelist_id
            if not repair_pl and not lost_pl:
                continue
            for line in order.order_line:
                if not line.product_id or line.display_type:
                    continue
                qty = line.product_uom_qty or 1.0
                if repair_pl and (force or not line.repair_price):
                    line.repair_price = repair_pl._get_product_price(
                        line.product_id, qty)
                if lost_pl and (force or not line.lost_price):
                    line.lost_price = lost_pl._get_product_price(
                        line.product_id, qty)
        return True

    def action_reload_rd_prices(self):
        """Header button: re-pull Repair + Lost prices from the pricelists,
        overwriting whatever is on the lines now. Use after changing a pricelist
        or to refresh an order on purpose."""
        self.ensure_one()
        if not self.repair_pricelist_id and not self.lost_pricelist_id:
            raise UserError(_(
                "Set a Repair Pricelist and/or Lost / Damage Pricelist on the "
                "Other Info tab first."
            ))
        self._stamp_rd_prices(force=True)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Prices reloaded"),
                'message': _("Repair and Lost prices were re-pulled from the "
                             "pricelists onto every line."),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_confirm(self):
        """On confirm, auto-stamp Repair/Lost prices for any line that doesn't
        already carry one (non-destructive -- manual overrides are kept). The
        Reload button does a forced overwrite when you want a clean re-pull."""
        res = super().action_confirm()
        self._stamp_rd_prices(force=False)
        return res

    # ------------------------------------------------------------------
    # Action: Compute lost products (from hksf_delivery_invoice)
    # ------------------------------------------------------------------
    def action_compute_lost_product(self):
        """Recompute lost / damaged material lines from the order's DELIVERY moves.

        Ported from Odoo 11 (odoo_delivery_invoice/models/sale_order.py
        action_compute_lost_product). The original scanned *outgoing* done
        pickings and flagged any rental move whose delivered quantity had not
        been fully returned/invoiced as a lost line. The earlier v19 port wrongly
        looked at *incoming* (collection) pickings' collection_repair_ids — which
        are empty unless lines are keyed in by hand — so nothing ever appeared
        when the order only had deliveries. This restores the O11 behaviour.
        """
        for order in self:
            # Delivery (outgoing, done) moves for this order.
            delivery_moves = self.env['stock.move'].search([
                ('custom_sale_id', '=', order.id),
                ('picking_code', '=', 'outgoing'),
                ('state', '=', 'done'),
            ])
            # Only rental lines that are not already (uncancelled) invoiced.
            delivery_moves = delivery_moves.filtered(
                lambda m: (not m.invoice_id or m.invoice_id.state == 'cancel')
                and m.custom_sale_line_id.line_type == 'rental'
            )

            # Preserve already-invoiced lost lines; rebuild the uninvoiced ones.
            invoiced_lost = order.collection_lost_material_ids.filtered(
                lambda l: l.invoice_id
            )
            already_moves = invoiced_lost.mapped('move_id')
            order.collection_lost_material_ids.filtered(
                lambda l: not l.invoice_id
            ).with_context(force_unlink=True).unlink()

            new_lines = []
            product_inv_qty = {}
            for move in delivery_moves:
                if move in already_moves:
                    continue
                delivered_qty = move.quantity
                returned_qty = move.new_return_quantity
                invoiced_qty = move.invoiced_quantity
                if not invoiced_qty and product_inv_qty.get(move.product_id.id):
                    invoiced_qty = product_inv_qty.get(move.product_id.id, 0.0)

                remaining_invoice_qty = 0.0
                if invoiced_qty > delivered_qty:
                    remaining_invoice_qty = invoiced_qty - delivered_qty
                    invoiced_qty = delivered_qty
                if remaining_invoice_qty:
                    product_inv_qty[move.product_id.id] = remaining_invoice_qty

                # Lost = delivered but neither returned nor invoiced.
                accounted = invoiced_qty + returned_qty
                if accounted < delivered_qty:
                    sale_line = move.custom_sale_line_id
                    new_lines.append((0, 0, {
                        'product_id': move.product_id.id,
                        'internal_ref': '%s %s' % (
                            move.product_id.name,
                            move.picking_id.name or '',
                        ),
                        'qty': delivered_qty - accounted,
                        'uom_id': move.product_id.uom_id.id,
                        'price_unit': sale_line.lost_price if sale_line else 0.0,
                        'type': 'lost',
                        'move_id': move.id,
                        'invoiced_qty': invoiced_qty,
                    }))

            # Pricing now comes from each line's STAMPED lost_price (set on
            # confirm / Reload from the Lost pricelist). The old guard required
            # order.lost_pricelist_id even when prices were already stamped,
            # which contradicted the new static-price model and blocked the
            # lost-from-Outstanding flow. Only block if a generated line has no
            # price AND there is no pricelist to fall back on. (v19.0.1.27.0)
            unpriced = [v for (_c, _i, v) in new_lines if not v.get('price_unit')]
            if unpriced and not order.lost_pricelist_id:
                raise UserError(_(
                    "Some lost lines have no Lost Price. Either set a Lost Price "
                    "on the order lines (Confirm / Reload R&D / Lost Prices) or "
                    "set a Lost / Damage Pricelist on the order's Other Info tab."
                ))
            # If a pricelist is set, fill any unpriced generated lines from it.
            if unpriced and order.lost_pricelist_id:
                for vals in (v for (_c, _i, v) in new_lines
                             if not v.get('price_unit')):
                    prod = self.env['product.product'].browse(vals['product_id'])
                    vals['price_unit'] = order.lost_pricelist_id._get_product_price(
                        prod, vals.get('qty') or 1.0)

            if new_lines:
                order.collection_lost_material_ids = new_lines

    # ------------------------------------------------------------------
    # Action: Create lost product invoice (from hksf_delivery_invoice)
    # ------------------------------------------------------------------
    def action_create_lost_product_invoice(self, lines_to_invoice=None):
        """Create an invoice for lost/damaged materials on this order.

        :param lines_to_invoice: recordset of collection.repair.damage lines.
                                 Defaults to all uninvoiced lost-type lines.
        """
        self.ensure_one()
        if lines_to_invoice is None:
            lines_to_invoice = self.collection_lost_material_ids.filtered(
                lambda l: not l.invoice_id and l.type == 'lost'
            )
        if not lines_to_invoice:
            raise UserError(_("No uninvoiced lost material lines found."))

        move_vals = self._prepare_lost_invoice_values()
        invoice = self.env['account.move'].sudo().create(move_vals)

        # Read the company-dependent Lost Material Income Account in the
        # INVOICE's company context (the field is company_dependent=True, so a
        # plain read could resolve under the wrong company and come back blank).
        company = invoice.company_id or self.company_id or self.env.company
        for line in lines_to_invoice:
            product = line.product_id
            tmpl = product.product_tmpl_id.with_company(company)
            account = (
                tmpl.property_lost_account_income_id
                or product.categ_id.property_account_income_categ_id
            )
            line_vals = {
                'move_id': invoice.id,
                'product_id': product.id,
                'quantity': line.qty,
                'price_unit': line.price_unit,
                'name': line.internal_ref or product.name,
                'account_id': account.id if account else False,
            }
            new_line = self.env['account.move.line'].sudo().create(line_vals)
            # Odoo recomputes account_id from the product on create; if we have an
            # explicit Lost Material Income Account, re-assert it so it wins.
            if account and new_line.account_id != account:
                new_line.with_context(check_move_validity=False).account_id = account.id
            line.invoice_id = invoice.id

        return invoice

    # ------------------------------------------------------------------
    # Action: Create lost invoice straight from the Outstanding page
    # ------------------------------------------------------------------
    def action_compute_lost_from_outstanding(self):
        """Build lost-material lines DIRECTLY from the Outstanding Products page
        Balance (delivered_qty - collected_qty), priced from the row's stamped
        lost price.

        Why this exists (v19.0.1.27.7):
        ``action_compute_lost_product`` derives "returned" from the module's own
        delivery_return_history records (``new_return_quantity``) plus invoiced
        qty -- a DIFFERENT definition than the Outstanding page, which uses raw
        incoming done-move quantities (``collected_qty``). The two can diverge:
        when collections are recorded via the collection flow, the histories
        fully cover the delivery, so the history-based builder finds NOTHING and
        the button raised "No uninvoiced lost material lines found." -- even
        though the page clearly shows a positive Balance.

        Since this button lives on the Outstanding page and the page has already
        computed the correct Balance, we build the lost lines straight from those
        rows. This guarantees the invoice always matches what the user sees.
        """
        self.ensure_one()
        # Make sure the rows reflect the latest deliveries/collections.
        self.action_compute_outstanding_products()

        # Preserve already-invoiced lost lines; rebuild only the uninvoiced ones.
        self.collection_lost_material_ids.filtered(
            lambda l: not l.invoice_id
        ).with_context(force_unlink=True).unlink()
        invoiced_products = self.collection_lost_material_ids.filtered(
            lambda l: l.invoice_id
        ).mapped('product_id')

        new_lines = []
        for row in self.product_outstanding_ids:
            balance = row.lost_products_qty  # already max(delivered - collected, 0)
            if balance <= 0.0:
                continue
            if row.product_id in invoiced_products:
                # already has an invoiced lost line for this product on this order
                continue
            price = row.price_unit
            if not price and self.lost_pricelist_id:
                price = self.lost_pricelist_id._get_product_price(
                    row.product_id, balance or 1.0)
            # Link back to a representative delivery move for traceability.
            move = row.outgoing_move_ids[:1]
            new_lines.append((0, 0, {
                'product_id': row.product_id.id,
                'internal_ref': '%s%s' % (
                    row.product_id.name,
                    (' ' + move.picking_id.name) if move and move.picking_id else '',
                ),
                'qty': balance,
                'uom_id': (row.uom_id or row.product_id.uom_id).id,
                'price_unit': price,
                'type': 'lost',
                'move_id': move.id if move else False,
            }))

        if new_lines:
            self.collection_lost_material_ids = new_lines
        return True

    def action_outstanding_create_lost_invoice(self):
        """One-click button on the Outstanding Products page: build the lost
        lines from the page Balance (delivered - collected) and raise the lost
        invoice for them.

        This replaces the old Lost / Damaged Materials tab workflow (which has
        been retired from the UI). The created invoice is opened in a form view
        so the user can review it.
        """
        self.ensure_one()
        # Step 1: build lost lines from the Outstanding page Balance.
        self.action_compute_lost_from_outstanding()
        # Step 2: invoice the uninvoiced lost lines.
        invoice = self.action_create_lost_product_invoice()
        # Open the resulting invoice.
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lost Material Invoice'),
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _prepare_lost_invoice_values(self):
        """Return dict of values to create a lost-material out_invoice."""
        self.ensure_one()
        company = self.company_id or self.env.company
        journal = self.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', company.id),
        ], limit=1)
        return {
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'partner_shipping_id': self.partner_shipping_id.id,
            'invoice_date': fields.Date.today(),
            'rental_sale_id': self.id,
            'rental_invoice_type': 'lost',
            'user_id': self.user_id.id,
            'invoice_origin': self.name,
            'ref': self.client_order_ref or self.name,
            'company_id': company.id,
            'invoice_payment_term_id': self.payment_term_id.id,
            'journal_id': journal.id if journal else False,
            'narration': self.note and tools.html2plaintext(self.note) or '',
        }
