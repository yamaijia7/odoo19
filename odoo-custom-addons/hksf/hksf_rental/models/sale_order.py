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
        default='normal',
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
    # Linked Sales Orders (billing master / children) + SP reference
    # Option B: native `name` is never mangled. The coded reference is a
    # computed/stored field; linking is relational via billing_master_id.
    # ------------------------------------------------------------------
    billing_master_id = fields.Many2one(
        'sale.order',
        string='Billing Master',
        copy=False,
        index=True,
        help="If set, this order's deliveries roll up to the master order "
             "for billing. Leave empty on the master itself.",
    )
    child_sale_ids = fields.One2many(
        'sale.order', 'billing_master_id',
        string='Linked Orders',
    )
    linked_order_count = fields.Integer(
        string='Linked Orders',
        compute='_compute_linked_order_count',
    )
    sp_reference = fields.Char(
        string='SP Reference',
        compute='_compute_sp_reference',
        store=True,
        help="Salesperson-coded reference printed on all custom reports. "
             "Master: {name}/{SP}/00; Linked: {master.name}/{SP}/NN.",
    )

    def _sp_code(self):
        """SP code fallback chain: signature user -> salesperson -> '??'."""
        self.ensure_one()
        return (self.sale_person_signature.sp_code
                or self.user_id.sp_code
                or '??')

    @api.depends('child_sale_ids')
    def _compute_linked_order_count(self):
        for order in self:
            order.linked_order_count = len(order.child_sale_ids)

    # ------------------------------------------------------------------
    # Active-rental tracking (monthly follow-up worklist)
    #
    # "Active" = a confirmed rental whose equipment is still on site:
    # delivered (outgoing done moves) minus collected (incoming done moves)
    # > 0. Computed LIVE from stock.move so the flag never goes stale and
    # needs no manual product.outstanding recompute.
    #
    # Scoped to the billing MASTER only (billing_master_id is empty):
    # moves are aggregated across the master + its children, so children
    # never appear as separate active rentals in the worklist.
    # ------------------------------------------------------------------
    on_hire_qty = fields.Float(
        string="On-Hire Qty",
        compute="_compute_active_rental",
        store=True,
        help="Total quantity still on site: delivered (done outgoing moves) "
             "minus collected (done incoming moves), aggregated across this "
             "order and its linked child orders.",
    )
    is_active_rental = fields.Boolean(
        string="Active Rental",
        compute="_compute_active_rental",
        store=True,
        help="Confirmed rental (billing master) that still needs follow-up: "
             "either equipment is still on site, OR everything is collected "
             "but billing has not yet caught up to the last collection date "
             "(final invoice still owed). Drops off only when BOTH the "
             "on-hire qty is 0 AND billing covers the last collection date "
             "(or 'Rental Billing Closed' is ticked).",
    )
    # --- Gate 2 inputs: billing-coverage vs last collection ------------
    last_collection_date = fields.Date(
        string="Last Collection Date",
        compute="_compute_active_rental",
        store=True,
        help="Date of the most recent DONE collection (incoming) in this "
             "rental's scope (master + children). Uses the picking's "
             "scheduled collection date, falling back to the move date.",
    )
    last_invoiced_through = fields.Date(
        string="Invoiced Through",
        compute="_compute_active_rental",
        store=True,
        help="Latest billing-period END date across POSTED rental invoice "
             "lines in scope. Gate 2 is cleared once this reaches the last "
             "collection date.",
    )
    rental_billing_closed = fields.Boolean(
        string="Rental Billing Closed",
        default=False,
        copy=False,
        help="Manual override: tick when a fully-collected rental has nothing "
             "left to bill (e.g. the final stub is waived or minimum charge "
             "already met). Satisfies Gate 2 so the rental drops off the "
             "Active Rentals worklist without a closing invoice.",
    )
    rental_followup_state = fields.Selection(
        selection=[
            ('on_hire', 'On Hire'),
            ('final_bill_due', 'Final Bill Due'),
            ('done', 'Done'),
        ],
        string="Follow-up State",
        compute="_compute_active_rental",
        store=True,
        help="Why this rental is (or is not) on the Active Rentals worklist. "
             "on_hire = equipment still out; final_bill_due = collected but "
             "closing invoice still owed; done = wound down.",
    )

    @api.depends(
        'custom_sale_type', 'state', 'billing_master_id', 'rental_billing_closed',
        'child_sale_ids.order_line.move_ids.state',
        'child_sale_ids.order_line.move_ids.quantity',
        'child_sale_ids.order_line.move_ids.date',
        'child_sale_ids.order_line.move_ids.picking_id.scheduled_date_only',
        'order_line.move_ids.state',
        'order_line.move_ids.quantity',
        'order_line.move_ids.date',
        'order_line.move_ids.picking_id.scheduled_date_only',
    )
    def _compute_active_rental(self):
        Move = self.env['stock.move'].sudo()
        AML = self.env['account.move.line'].sudo()
        for order in self:
            # Only billing masters carry the flag; children roll up to master.
            if order.billing_master_id:
                order.on_hire_qty = 0.0
                order.last_collection_date = False
                order.last_invoiced_through = False
                order.is_active_rental = False
                order.rental_followup_state = 'done'
                continue
            scope_ids = (order | order.child_sale_ids).ids
            if not scope_ids:
                order.on_hire_qty = 0.0
                order.last_collection_date = False
                order.last_invoiced_through = False
                order.is_active_rental = False
                order.rental_followup_state = 'done'
                continue
            delivered = sum(Move.search([
                ('custom_sale_id', 'in', scope_ids),
                ('picking_code', '=', 'outgoing'),
                ('state', '=', 'done'),
            ]).mapped('quantity'))
            collection_moves = Move.search([
                ('custom_sale_id', 'in', scope_ids),
                ('picking_code', '=', 'incoming'),
                ('state', '=', 'done'),
            ])
            collected = sum(collection_moves.mapped('quantity'))
            on_hire = delivered - collected
            order.on_hire_qty = on_hire

            # --- Gate 2: last collection date (business date = picking
            #     scheduled collection date; fall back to the move date). ---
            coll_dates = []
            for m in collection_moves:
                d = m.picking_id.scheduled_date_only
                if not d and m.date:
                    d = m.date.date()
                if d:
                    coll_dates.append(d)
            last_coll = max(coll_dates) if coll_dates else False
            order.last_collection_date = last_coll

            # --- Billing coverage: latest period END across POSTED rental
            #     invoice lines in scope. Rental period lines carry end_date
            #     and link back via custom_sale_line_id.order_id; transport /
            #     service lines are excluded from rental coverage. ---
            inv_lines = AML.search([
                ('parent_state', '=', 'posted'),
                ('move_id.move_type', '=', 'out_invoice'),
                ('end_date', '!=', False),
                ('is_transport_product', '=', False),
                ('is_service_product', '=', False),
                ('custom_sale_line_id.order_id', 'in', scope_ids),
            ])
            end_dates = inv_lines.mapped('end_date')
            invoiced_through = max(end_dates) if end_dates else False
            order.last_invoiced_through = invoiced_through

            is_rent = (order.custom_sale_type == 'rent'
                       and order.state == 'sale')
            on_site = on_hire > 0.0
            # Gate 2 unmet = collected, but billing has NOT reached the last
            # collection date (and not manually closed).
            final_bill_due = (
                not on_site
                and bool(last_coll)
                and not order.rental_billing_closed
                and (not invoiced_through or invoiced_through < last_coll)
            )
            order.is_active_rental = is_rent and (on_site or final_bill_due)
            if not is_rent:
                order.rental_followup_state = 'done'
            elif on_site:
                order.rental_followup_state = 'on_hire'
            elif final_bill_due:
                order.rental_followup_state = 'final_bill_due'
            else:
                order.rental_followup_state = 'done'

    @api.depends(
        'name', 'billing_master_id', 'billing_master_id.name',
        'billing_master_id.child_sale_ids',
        'sale_person_signature.sp_code', 'user_id.sp_code',
    )
    def _compute_sp_reference(self):
        for order in self:
            sp = order._sp_code()
            master = order.billing_master_id
            if master:
                siblings = master.child_sale_ids.sorted('id')
                # Position of this child among siblings (1-based) -> 01, 02...
                nn = (list(siblings).index(order) + 1) if order in siblings else 1
                base = master.name or ''
                order.sp_reference = '%s/%s/%02d' % (base, sp, nn)
            else:
                order.sp_reference = '%s/%s/00' % (order.name or '', sp)

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
    service_charge_ids = fields.One2many(
        'service.charge',
        'order_id',
        string='Service Charges',
        copy=False,
        help="Erection / dismantling (and other) service charges. Picked up "
             "by the rental invoice wizard and billed as native invoice "
             "lines through each service product's income account.",
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
            # A billing master aggregates its own + child orders' invoices.
            order_ids = (order | order.child_sale_ids).ids
            moves = self.env['account.move'].search([
                ('rental_sale_id', 'in', order_ids),
                ('move_type', 'in', ('out_invoice', 'out_refund')),
            ])
            moves |= (order | order.child_sale_ids).invoice_ids.filtered(
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
    # NOTE (v19.0.1.70.0): the round-2 "Pull Invoiceable Service Lines" action
    # (action_pull_invoiceable_service_lines / _invoiceable_service_charges)
    # was REMOVED. Service billing is now driven by the per-order-line
    # "Bill on Next Invoice" checkbox; the wizard selects ticked service order
    # lines directly (delivery_invoice_wizard._billable_service_lines).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Linked Sales Order: actions (new linked order, pull prices, views)
    # ------------------------------------------------------------------
    def action_new_linked_order(self):
        """Create a child SO linked to this master via billing_master_id.

        Driven by the wizard context 'copy_lines' (default True): when True
        the master's order lines are copied (product/qty/prices); when False
        the child starts blank. Returns the new SO form."""
        self.ensure_one()
        copy_lines = self.env.context.get('copy_lines', True)
        vals = {
            'partner_id': self.partner_id.id,
            'partner_shipping_id': self.partner_shipping_id.id,
            'partner_invoice_id': self.partner_invoice_id.id,
            'billing_master_id': self.id,
            'custom_sale_type': self.custom_sale_type,
            'charge_type': self.charge_type,
            'user_id': self.user_id.id,
            'sale_person_signature': self.sale_person_signature.id,
            'order_line': [],
        }
        if copy_lines:
            line_cmds = []
            for line in self.order_line.filtered(lambda l: not l.display_type):
                line_cmds.append((0, 0, {
                    'product_id': line.product_id.id,
                    'name': line.name,
                    'product_uom_qty': line.product_uom_qty,
                    'product_uom': line.product_uom.id,
                    'price_unit': line.price_unit,
                    'repair_price': line.repair_price,
                    'lost_price': line.lost_price,
                }))
            vals['order_line'] = line_cmds
        new_order = self.create(vals)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Linked Order'),
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': new_order.id,
        }

    def action_pull_master_prices(self):
        """On a draft linked SO, pull price_unit / repair_price / lost_price
        from the billing master, matching lines by product_id."""
        self.ensure_one()
        master = self.billing_master_id
        if not master:
            raise UserError(_("This order has no billing master to pull from."))
        master_by_product = {}
        for ml in master.order_line.filtered(lambda l: l.product_id):
            master_by_product.setdefault(ml.product_id.id, ml)
        for line in self.order_line.filtered(lambda l: l.product_id):
            ml = master_by_product.get(line.product_id.id)
            if ml:
                line.write({
                    'price_unit': ml.price_unit,
                    'repair_price': ml.repair_price,
                    'lost_price': ml.lost_price,
                })
        return True

    def action_view_linked_orders(self):
        """Smart button on a master: open its linked (child) orders."""
        self.ensure_one()
        children = self.child_sale_ids
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Linked Orders'),
            'res_model': 'sale.order',
            'context': {'default_billing_master_id': self.id},
        }
        if len(children) == 1:
            action.update({'view_mode': 'form', 'res_id': children.id})
        else:
            action.update({
                'view_mode': 'list,form',
                'domain': [('id', 'in', children.ids)],
            })
        return action

    def action_view_billing_master(self):
        """Smart button on a child: open its billing master."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Billing Master'),
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': self.billing_master_id.id,
        }

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
        """ORPHAN (kept intentionally): not wired to any button/action as of
        v19.0.1.30.0. The header 'Create Rental Invoice' button calls the
        window-action XML (action_hksf_delivery_invoice_wizard) directly, so
        this Python helper is currently unreferenced. Retained as a convenient
        programmatic entry point (e.g. server actions / future buttons). If you
        confirm it is truly unneeded, it is safe to delete.

        Header button: open the delivery invoice wizard for this order."""
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
        """Recompute delivered vs collected qty per product for this order.

        When run on a billing master, aggregate moves across the master and
        all its child (linked) orders, so outstanding reflects the whole
        billing group."""
        for order in self:
            # Aggregate across self + linked children when this is a master.
            scope_ids = (order | order.child_sale_ids).ids
            delivery_moves = self.env['stock.move'].search([
                ('custom_sale_id', 'in', scope_ids),
                ('picking_code', '=', 'outgoing'),
                ('state', '=', 'done'),
            ])
            incoming_moves = self.env['stock.move'].search([
                ('custom_sale_id', 'in', scope_ids),
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
        # Linked SOs: stamp newly created pickings' custom_sale_order_id with
        # the billing master so deliveries roll up to the master for billing.
        for order in self.filtered('billing_master_id'):
            pickings = order.picking_ids.filtered(
                lambda p: not p.custom_sale_order_id
            )
            if pickings:
                pickings.write(
                    {'custom_sale_order_id': order.billing_master_id.id}
                )
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

        self._create_lost_return_picking(
            lines_to_invoice,
            invoice.invoice_date or fields.Date.today(),
        )
        return invoice

    # ------------------------------------------------------------------
    # Lost = return: stop renting the lost qty from the lost-invoice date
    # ------------------------------------------------------------------
    def _create_lost_return_picking(self, lost_lines, return_date):
        """Create ONE validated incoming picking for the lost lines so the lost
        qty is treated exactly like stock returned to us on ``return_date``.

        This is what makes a lost invoice stop the rental clock for those
        units. Mirrors ``collection.return.wizard.action_create_collection`` but
        is driven by the lost lines and dated on the lost-invoice date so rental
        billing pro-rates correctly to that date. Per-move ``return_qty`` is
        capped by remaining (delivered - already_returned) capacity, so a
        delivery move can never be over-returned even on an accidental re-run.
        """
        self.ensure_one()
        order = self
        lost_lines = lost_lines.filtered(
            lambda l: l.type == 'lost' and (l.qty or 0.0) > 0.0
        )
        if not lost_lines:
            return self.env['stock.picking']

        warehouse = order.warehouse_id
        if not warehouse:
            return self.env['stock.picking']

        out_type = self.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        return_type = out_type.return_picking_type_id or self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        if not return_type:
            return self.env['stock.picking']

        location_id = return_type.default_location_src_id
        location_dest_id = return_type.default_location_dest_id

        site_contact_ids = (
            [(4, c.id) for c in order.site_contact_ids]
            if 'site_contact_ids' in order._fields else False
        )

        picking_vals = {
            'partner_id': order.partner_shipping_id.id or order.partner_id.id,
            'company_id': order.company_id.id,
            'location_id': location_id.id,
            'location_dest_id': location_dest_id.id,
            'scheduled_date_only': return_date,
            'picking_type_id': return_type.id,
            'origin': '%s (Lost)' % order.name,
            'custom_sale_order_id': order.id,
            'partner_parent_id': order.partner_id.parent_id.id or order.partner_id.id,
            'is_lost_return': True,
        }
        if site_contact_ids:
            picking_vals['site_contact_ids'] = site_contact_ids
        picking = self.env['stock.picking'].sudo().create(picking_vals)

        History = self.env['delivery.return.history'].sudo()
        Move = self.env['stock.move'].sudo()

        delivery_moves_all = Move.search([
            ('custom_sale_id', '=', order.id),
            ('picking_code', '=', 'outgoing'),
            ('state', '=', 'done'),
        ])

        qty_by_product = {}
        for line in lost_lines:
            qty_by_product.setdefault(line.product_id, 0.0)
            qty_by_product[line.product_id] += line.qty

        for product, qty_left in qty_by_product.items():
            if qty_left <= 0.0:
                continue
            return_move = Move.create({
                'description_picking': product.name,
                'company_id': order.company_id.id,
                'product_id': product.id,
                'product_uom': product.uom_id.id,
                'product_uom_qty': qty_left,
                'partner_id': order.partner_shipping_id.id or order.partner_id.id,
                'location_id': location_id.id,
                'location_dest_id': location_dest_id.id,
                'origin': '%s (Lost)' % order.name,
                'picking_id': picking.id,
                'date': return_date,
                'custom_sale_id': order.id,
                'state': 'draft',
            })

            remaining = qty_left
            delivery_moves = delivery_moves_all.filtered(
                lambda m: m.product_id == product
            ).sorted(lambda m: (m.date or m.create_date, m.id))
            for dmove in delivery_moves:
                if remaining <= 0.0:
                    break
                already_returned = sum(
                    h.return_qty for h in dmove.delivery_return_history_ids
                )
                capacity = dmove.quantity - already_returned
                if capacity <= 0.0:
                    continue
                alloc = min(capacity, remaining)
                history = History.create({
                    'deliver_move_id': dmove.id,
                    'return_move_id': return_move.id,
                    'return_qty': alloc,
                    'delivered_qty': dmove.quantity,
                })
                dmove.delivery_return_history_ids = [(4, history.id)]
                return_move.delivery_return_history_ids = [(4, history.id)]
                remaining -= alloc

        # Validate the picking the CORE-SAFE way. ``button_validate`` is the
        # standard entry point and moves quants + posts valuation. In a
        # head-less/rental context it may return an action dict (an
        # immediate-transfer / backorder confirmation wizard that a user would
        # normally click through) instead of completing; when that happens we
        # finish via ``_action_done()`` -- the SAME core primitive
        # ``button_validate`` itself calls -- so quants and valuation are still
        # handled by Odoo. We NEVER force ``state='done'`` directly, which would
        # mark the move done WITHOUT moving stock and desync the ledger.
        try:
            for mv in picking.move_ids:
                mv.quantity = mv.product_uom_qty
            res = picking.button_validate()
            if isinstance(res, dict) and picking.state != 'done':
                picking.move_ids._action_done()
        except Exception:
            # Last-resort completion still goes through the core done routine
            # (moves quants / valuation), never a raw state write.
            picking.move_ids._action_done()

        return picking

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

    # ------------------------------------------------------------------
    # Guard native invoicing against wizard-managed service lines
    # ------------------------------------------------------------------
    def _get_invoiceable_lines(self, final=False):
        """Exclude wizard-managed service lines from NATIVE invoice generation.

        Round 6 linked billed service lines to their order line via the native
        ``sale_line_ids`` m2m so ``qty_invoiced`` / ``invoice_status`` track
        them for DISPLAY. The side effect is that those lines become candidates
        for Odoo's native ``_create_invoices`` flow (Sales > To Invoice,
        server action, scheduled auto-invoice, portal/API) -- which would
        DOUBLE-bill them, since the rental wizard is their sole biller.

        ``_get_invoiceable_lines`` is the single hook ``_create_invoices`` uses
        to decide which lines to invoice, so filtering here stops native line
        creation everywhere without touching the qty_invoiced display.

        Predicate (wizard-managed = excluded): a real product line
        (``not display_type``) whose product is service-type and whose
        ``bill_on_next_invoice`` flag is set. Non-service rental / transport /
        sale lines are NEVER filtered -- they stay natively invoiceable.
        """
        lines = super()._get_invoiceable_lines(final=final)
        return lines.filtered(lambda l: not (
            not l.display_type
            and l.product_id.type == 'service'
            and l.bill_on_next_invoice
        ))

    def _prepare_lost_invoice_values(self):
        """Return dict of values to create a lost-material out_invoice."""
        self.ensure_one()
        company = self.company_id or self.env.company
        # Per-type default journal (LOST), falling back to standard Sales.
        journal = company._hksf_journal_for('lost')
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
