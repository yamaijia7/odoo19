# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import pytz
from datetime import datetime, date as _date


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # ------------------------------------------------------------------
    # Fields from odoo_delivery_collection
    # ------------------------------------------------------------------

    scheduled_date_only = fields.Date(
        string='Service Date',
        index=True,
        copy=False,
        help="Date-only version of the scheduled datetime (in user timezone). "
             "Required before validation. Used for invoice date range comparisons.",
    )
    license_plate = fields.Char(
        string='License Plate',
        copy=False,
    )
    truck_size_id = fields.Many2one(
        'hksf.truck.size',
        string='Truck Size',
        ondelete='restrict',
    )
    time_in = fields.Datetime(
        string='Time In',
        copy=False,
    )
    time_out = fields.Datetime(
        string='Time Out',
        copy=False,
    )
    custom_employee_id = fields.Many2one(
        'hr.employee',
        string='Driver / Employee',
        copy=False,
    )
    delivery_type = fields.Selection(
        selection=[
            ('standard', 'Standard'),
            ('urgent', 'Urgent'),
            ('partial', 'Partial'),
        ],
        string='Delivery Type',
        default='standard',
    )

    # collection.repair.damage lines on picking
    collection_repair_ids = fields.One2many(
        'collection.repair.damage',
        'collection_picking_id',
        string='Repair / Damage Records',
        copy=False,
    )

    # ------------------------------------------------------------------
    # Fields from odoo_delivery_invoice
    # ------------------------------------------------------------------

    invoice_id = fields.Many2one(
        'account.move',
        string='Invoice',
        copy=False,
        domain=[('move_type', '=', 'out_invoice')],
    )
    last_invoice_date = fields.Date(
        string='Last Invoice Date',
        copy=False,
    )
    transport_charge_ids = fields.One2many(
        'transport.charge',
        'picking_id',
        string='Transport Charges',
        copy=False,
    )
    transportation_method = fields.Selection(
        selection=[
            ('by_us', 'Our Transport'),
            ('by_customer', 'Customer Arranged'),
        ],
        string='Transportation Method',
        default='by_us',
    )
    site_contact_ids = fields.Many2many(
        'res.partner',
        'stock_picking_site_contact_rel',
        'picking_id',
        'partner_id',
        string='Site Contacts',
    )
    partner_parent_id = fields.Many2one(
        'res.partner',
        string='Parent Customer',
        compute='_compute_partner_parent_id',
        store=True,
    )
    is_from_previous_month = fields.Boolean(
        string='From Previous Month',
        default=False,
        copy=False,
    )
    original_return_picking_id = fields.Many2one(
        'stock.picking',
        string='Original Return Picking',
        copy=False,
    )
    new_return_picking_id = fields.Many2one(
        'stock.picking',
        string='New Return Picking',
        copy=False,
    )
    custom_sale_order_id = fields.Many2one(
        'sale.order',
        string='Custom Sale Order',
        copy=False,
        index=True,
        help="Explicitly linked sale order (used for collection pickings not "
             "created through normal stock routing).",
    )
    is_create_transport_invoice = fields.Boolean(
        string='Transport Invoice Created',
        default=False,
        copy=False,
    )
    is_lost_return = fields.Boolean(
        string='Lost Material Return',
        default=False,
        copy=False,
        help="Set on the auto-generated incoming picking that represents lost "
             "materials being written off. These returns stop the rental clock "
             "for the lost qty from the lost-invoice date.",
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('partner_id', 'partner_id.parent_id')
    def _compute_partner_parent_id(self):
        for rec in self:
            rec.partner_parent_id = rec.partner_id.parent_id or rec.partner_id

    # ------------------------------------------------------------------
    # Onchange: auto-fill scheduled_date_only from scheduled_date
    # ------------------------------------------------------------------

    @api.onchange('scheduled_date')
    def _onchange_scheduled_date(self):
        """Convert scheduled datetime to a date-only value using user timezone."""
        for rec in self:
            if rec.scheduled_date:
                user_tz = self.env.user.tz or 'UTC'
                try:
                    tz = pytz.timezone(user_tz)
                    local_dt = rec.scheduled_date.astimezone(tz)
                    rec.scheduled_date_only = local_dt.date()
                except Exception:
                    rec.scheduled_date_only = rec.scheduled_date.date()

    # ------------------------------------------------------------------
    # Override button_validate — require scheduled_date_only
    # ------------------------------------------------------------------

    def button_validate(self):
        for rec in self:
            if not rec.scheduled_date_only:
                raise UserError(
                    _("Please set the Service Date on picking '%s' before validating.")
                    % rec.name
                )
        res = super().button_validate()
        # After a collection (incoming) picking is validated, re-sync its
        # delivery.return.history rows to the ACTUAL collected quantity.
        for rec in self:
            if rec.picking_type_code == 'incoming' and rec.state == 'done':
                rec._resync_collection_histories()
                rec._generate_repair_damage_lines()
        # Refresh the active-rental flag on the affected billing master(s)
        # the moment a delivery/collection is validated, so the monthly
        # worklist (is_active_rental) is always live.
        orders = self.move_ids.mapped('custom_sale_id')
        masters = self.env['sale.order']
        for o in orders:
            masters |= (o.billing_master_id or o)
        if masters:
            masters._compute_active_rental()
        return res

    def action_sync_collection(self):
        """ONE button on a validated (done) collection picking that re-syncs
        EVERYTHING derived from the collection, in the right order:

          1. ``_resync_collection_histories`` -> rebuilds the rental return
             credit (delivery.return.history) to match the corrected collected
             ``move.quantity``. Needed after you edit a Done qty (e.g. validated
             at 20, actually 19), because histories are otherwise only rebuilt
             inside ``button_validate``.
          2. ``_generate_repair_damage_lines`` -> (re)creates the
             collection.repair.damage lines from the inline Repair / Damage
             columns (repair -> repair_price, damage -> lost_price). Needed when
             the columns are filled in / edited after validation.

        NOTE on stock: the stock ledger itself is corrected by CORE Odoo the
        moment you edit the *Done* quantity on the move line of a done picking
        (stock.move.line.write re-posts the quant). Edit the qty FIRST in
        Detailed Operations, THEN press this button to realign credit + R&D.

        Both steps are idempotent -- safe to press repeatedly. If any move has
        Repair + Damage greater than the collected qty, a non-blocking warning
        is shown (the data is still saved, per design).
        """
        done_incoming = self.filtered(
            lambda p: p.picking_type_code == 'incoming' and p.state == 'done'
        )
        if not done_incoming:
            raise UserError(_(
                "A collection can only be synced once it is validated (done)."
            ))
        done_incoming._resync_collection_histories()
        done_incoming._generate_repair_damage_lines()
        warn = done_incoming._collect_overqty_warnings()
        if warn:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Collection synced (with warnings)"),
                    'message': warn,
                    'type': 'warning',
                    'sticky': True,
                },
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Collection synced"),
                'message': _("Rental credit and Repair/Damage lines are up to date."),
                'type': 'success',
                'sticky': False,
            },
        }

    # Backward-compat aliases: any old stored button reference keeps working
    # and now performs the full combined sync.
    def action_sync_repair_damage(self):
        return self.action_sync_collection()

    def action_sync_collection_qty(self):
        return self.action_sync_collection()

    def _collect_overqty_warnings(self):
        """Return a human-readable warning string (or empty) listing any move
        where Repair + Damage exceeds the collected (Done) quantity. Combined
        comparison per the user's decision; soft / non-blocking.
        """
        msgs = []
        for rec in self:
            for move in rec.move_ids:
                rd = (move.repair_qty or 0.0) + (move.damage_qty or 0.0)
                collected = move.quantity or 0.0
                if rd > collected + 0.0001:
                    msgs.append(_(
                        "%(prod)s: Repair (%(r)s) + Damage (%(d)s) = %(sum)s "
                        "exceeds collected %(c)s."
                    ) % {
                        'prod': move.product_id.display_name,
                        'r': move.repair_qty or 0.0,
                        'd': move.damage_qty or 0.0,
                        'sum': rd,
                        'c': collected,
                    })
        return "\n".join(msgs)

    def _generate_repair_damage_lines(self):
        """Turn inline repair_qty / damage_qty on collection moves into
        collection.repair.damage lines.

        Per the user's pricing model:
          - repair -> sale-line repair_price (own price)
          - damage -> sale-line lost_price   (damage = lost, beyond repair)
        Lines are kept INDEPENDENT of the rental return credit (no qty cap,
        no exclusion from rental) by explicit design decision.
        Idempotent: existing un-invoiced auto lines for a move+type are updated
        in place so re-validating / editing doesn't create duplicates.
        """
        RD = self.env['collection.repair.damage'].sudo()
        for rec in self:
            picking_order = rec.custom_sale_order_id
            for move in rec.move_ids:
                if not move.product_id:
                    continue
                # Resolve the originating sale order ROBUSTLY so the generated
                # collection.repair.damage record always links back to the SO
                # (order_id). Without a valid order_id the line is orphaned and
                # the Create Repair/Damage wizard -- which reads the SO's
                # collection_lost_material_ids (One2many on order_id) -- shows
                # NOTHING even though repair_qty/damage_qty were entered and
                # synced. Fallback chain:
                #   1. picking.custom_sale_order_id (set by Create Collection)
                #   2. move.sale_line_id.order_id
                #   3. the SO behind the linked delivery move (return history)
                # Needed for collections created outside the Create Collection
                # button (manual returns, migrated O11 data).
                order = picking_order or move.sale_line_id.order_id
                if not order:
                    dmove = move.delivery_return_history_ids.deliver_move_id[:1]
                    order = dmove.sale_line_id.order_id if dmove else False
                sale_line = move.sale_line_id or (
                    order.order_line.filtered(lambda l: l.product_id == move.product_id)[:1]
                    if order else False
                )
                specs = [
                    ('repair', move.repair_qty,
                     sale_line.repair_price if sale_line else move.product_id.lst_price),
                    ('damage', move.damage_qty,
                     sale_line.lost_price if sale_line else move.product_id.lst_price),
                ]
                for rd_type, qty, price in specs:
                    existing = RD.search([
                        ('move_id', '=', move.id),
                        ('type', '=', rd_type),
                        ('collection_picking_id', '=', rec.id),
                    ], limit=1)
                    if qty and qty > 0.0:
                        vals = {
                            'product_id': move.product_id.id,
                            'internal_ref': '%s %s' % (move.product_id.name, rec.name),
                            'qty': qty,
                            'uom_id': move.product_id.uom_id.id,
                            'price_unit': price or 0.0,
                            'type': rd_type,
                            'move_id': move.id,
                            'collection_picking_id': rec.id,
                            'order_id': order.id if order else False,
                        }
                        if existing and not existing.invoice_id:
                            existing.write(vals)
                        elif not existing:
                            RD.create(vals)
                    elif existing and not existing.invoice_id:
                        # qty cleared back to 0 -> remove the stale auto line
                        existing.with_context(force_unlink=True).unlink()
        return True

    def _resync_collection_histories(self):
        """Rebuild collection return histories with a TWO-DIMENSIONAL FIFO so
        the invoice reproduces Odoo 11 exactly, independent of the order in
        which collections were created or validated.

        The two FIFO axes
        -----------------
        1. COLLECTIONS are processed EARLIEST-FIRST (by ``scheduled_date_only``,
           picking id tie-break). The earliest collection claims stock first.
        2. DELIVERY TRANCHES are consumed OLDEST-FIRST (by the delivery
           picking's ``scheduled_date_only`` / move date). Each collection draws
           its collected quantity from the oldest delivery tranche that still
           has free capacity, spilling into newer tranches only once older ones
           are exhausted.

        Why both axes matter
        --------------------
        A product is often delivered in several tranches (e.g. 189 on 23/05 then
        130 more on 12/06) and collected in several batches (e.g. 147 on 23/06,
        75 on 30/06, ...). The MONEY depends on WHICH delivery tranche each
        collected unit is attributed to, because a recent tranche may still be
        inside its minimum rental period (e.g. the 12/06 tranche collected 30/06
        has only been on hire ~19 days, so an 11-day MINIMUM CHARGE applies),
        whereas an old tranche is past the minimum and credits cleanly.

        Odoo 11 attributes the EARLIEST collection to the OLDEST tranche first;
        a later collection finishes the old tranche (no minimum charge) and then
        dips into the recent tranche (triggering its minimum charge). Reproducing
        that requires rebuilding the delivery.return.history ``deliver_move_id``
        links, not only the ``return_qty`` -- which is what this method now does.

        Earlier implementations
        -----------------------
        * <=v44: per-picking resync that derived capacity from siblings' booked
          return_qty -> order-dependent; a later collection could starve an
          earlier one of a shared move's capacity.
        * v45: global, collection-date FIFO for ``return_qty`` BUT kept each
          history pinned to its original ``deliver_move_id`` -> fixed the credit
          quantities yet still mis-attributed the delivery tranche, so short
          re-rentals lost their minimum charge.
        * v46 (this): global FIFO on BOTH axes, rebuilding the history rows so
          tranche attribution matches O11 and minimum charges land correctly.

        Idempotent: a pure function of the validated move quantities + delivery
        and collection dates, so re-running converges to the same allocation.
        """
        History = self.env['delivery.return.history'].sudo()

        # ---- 1. Resolve the set of collections to rebuild together ----------
        # Start from the done incoming pickings in ``self`` and widen to every
        # sibling collection of the same sale order, so a shared delivery
        # tranche's capacity is always allocated across ALL its claimants at
        # once (allocation must see the whole picture to be order-independent).
        seed = self.filtered(
            lambda p: p.picking_type_code == 'incoming' and p.state == 'done'
        )
        if not seed:
            return

        orders = seed.mapped('custom_sale_order_id')
        # Fallback: derive the order via existing histories / sale lines when
        # custom_sale_order_id is not set (manual / migrated collections).
        for coll in seed:
            if coll.custom_sale_order_id:
                continue
            dmove = coll.move_ids.mapped(
                'delivery_return_history_ids.deliver_move_id')[:1]
            if dmove and dmove.sale_line_id:
                orders |= dmove.sale_line_id.order_id
            else:
                sl = coll.move_ids.mapped('sale_line_id')[:1]
                if sl:
                    orders |= sl.order_id

        Picking = self.env['stock.picking'].sudo()
        all_collections = seed
        if orders:
            all_collections |= Picking.search([
                ('custom_sale_order_id', 'in', orders.ids),
                ('picking_type_code', '=', 'incoming'),
                ('state', '=', 'done'),
            ])
        # Always include the seed even if order lookup missed it.
        all_collections = all_collections.filtered(
            lambda p: p.picking_type_code == 'incoming' and p.state == 'done'
        )

        def _coll_sort_key(pick):
            # scheduled_date_only is required before validation; guard unset
            # values to sort LAST so they claim last.
            return (pick.scheduled_date_only or _date.max, pick.id)

        ordered_collections = all_collections.sorted(key=_coll_sort_key)

        # ---- 2. Gather delivery tranches per product ------------------------
        # For each product (by product_id) collect every DONE delivery move on
        # the relevant sale order(s), oldest delivery first.
        def _deliver_sort_key(move):
            pick = move.picking_id
            d = pick.scheduled_date_only if pick else False
            return (
                d or (move.date.date() if move.date else _date.max),
                move.id,
            )

        # Build the universe of delivery moves: those already referenced by any
        # collection's histories PLUS all outgoing done moves of the order(s),
        # so newly-needed tranches are available even if not yet linked.
        deliver_moves = self.env['stock.move']
        for coll in ordered_collections:
            deliver_moves |= coll.move_ids.mapped(
                'delivery_return_history_ids.deliver_move_id')
        if orders:
            deliver_moves |= self.env['stock.move'].sudo().search([
                ('picking_id.sale_id', 'in', orders.ids),
                ('picking_id.picking_type_code', '=', 'outgoing'),
                ('state', '=', 'done'),
            ])

        # Remaining capacity per delivery move (its full done quantity).
        remaining_capacity = {dm.id: dm.quantity for dm in deliver_moves}

        # Index delivery moves by product, oldest-first.
        tranches_by_product = {}
        for dm in deliver_moves.sorted(key=_deliver_sort_key):
            tranches_by_product.setdefault(dm.product_id.id, []).append(dm)

        # ---- 3. Rebuild histories: each collection draws tranches FIFO ------
        keep_history_ids = set()
        for coll in ordered_collections:
            for rmove in coll.move_ids.filtered(lambda m: m.state == 'done'):
                product_id = rmove.product_id.id
                to_allocate = rmove.quantity
                tranches = tranches_by_product.get(product_id, [])
                # Existing histories on THIS return move, indexed by tranche so
                # we can update-in-place (preserves invoice links where valid).
                existing_by_dmove = {}
                for h in rmove.delivery_return_history_ids:
                    if h.deliver_move_id:
                        existing_by_dmove.setdefault(
                            h.deliver_move_id.id, []).append(h)

                for dm in tranches:
                    if to_allocate <= 0.0:
                        break
                    cap = remaining_capacity.get(dm.id, 0.0)
                    if cap <= 0.0:
                        continue
                    alloc = min(cap, to_allocate)
                    if alloc <= 0.0:
                        continue
                    # Reuse an existing history for this (return move, tranche)
                    # if present; else create a fresh one.
                    pool = existing_by_dmove.get(dm.id)
                    if pool:
                        hist = pool.pop(0)
                        if (hist.return_qty != alloc
                                or hist.delivered_qty != dm.quantity):
                            hist.write({
                                'return_qty': alloc,
                                'delivered_qty': dm.quantity,
                            })
                    else:
                        hist = History.create({
                            'deliver_move_id': dm.id,
                            'return_move_id': rmove.id,
                            'return_qty': alloc,
                            'delivered_qty': dm.quantity,
                        })
                        dm.delivery_return_history_ids = [(4, hist.id)]
                        rmove.delivery_return_history_ids = [(4, hist.id)]
                    keep_history_ids.add(hist.id)
                    remaining_capacity[dm.id] = cap - alloc
                    to_allocate -= alloc

        # ---- 4. Drop stale / zero-qty histories on the rebuilt collections --
        # Any history on the processed collections that we did NOT keep is now
        # superseded (wrong tranche, or zero qty) and must be removed so it does
        # not feed the invoice. Only touch UN-invoiced histories to avoid
        # disturbing posted invoice links.
        for coll in ordered_collections:
            for rmove in coll.move_ids.filtered(lambda m: m.state == 'done'):
                for h in rmove.delivery_return_history_ids:
                    if h.id in keep_history_ids:
                        continue
                    if h.invoice_id and h.invoice_id.state == 'posted':
                        # Never silently break a posted invoice's links.
                        continue
                    h.sudo().unlink()
