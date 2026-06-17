# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class StockMove(models.Model):
    _inherit = 'stock.move'

    # ------------------------------------------------------------------
    # Fields from odoo_delivery_collection
    # ------------------------------------------------------------------

    custom_sale_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        index=True,
        copy=False,
        compute='_compute_custom_sale_id',
        store=True,
        readonly=False,
        help="Originating sale order for this move. Auto-derived from the standard "
             "sale_line_id link (fallback: the picking's sale order) so HKSF "
             "features (outstanding products, lost/damaged, collection) work for "
             "deliveries created the standard way. Collection/return wizards set "
             "this explicitly; the compute never overwrites an existing value.",
    )

    @api.depends('sale_line_id', 'sale_line_id.order_id', 'picking_id.custom_sale_order_id')
    def _compute_custom_sale_id(self):
        """Derive the originating sale order from the standard move->sale link,
        falling back to the picking's custom_sale_order_id (the Odoo 11 path for
        manually-created/imported pickings). Never clobber a value that was set
        explicitly (e.g. by the collection wizards on incoming moves)."""
        for move in self:
            if move.custom_sale_id:
                # Preserve explicit values (collection moves, manual edits).
                continue
            order = move.sale_line_id.order_id or move.picking_id.custom_sale_order_id
            move.custom_sale_id = order.id if order else False

    custom_sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Sale Order Line',
        copy=False,
        index=True,
        compute='_compute_custom_sale_line_id',
        store=True,
        readonly=False,
        help="Originating sale order line for this move. Auto-derived from the "
             "standard sale_line_id link so HKSF features that key off the "
             "line (e.g. Lost/Damaged 'Sync from Pickings', which filters on "
             "line_type=='rental') work for deliveries created the standard "
             "way. Invoice/return wizards may set this explicitly; the compute "
             "never overwrites an existing value.",
    )

    @api.depends('sale_line_id')
    def _compute_custom_sale_line_id(self):
        """Derive the originating sale order line from the standard move->line
        link. Never clobber a value that was set explicitly (mirrors the
        custom_sale_id behaviour)."""
        for move in self:
            if move.custom_sale_line_id:
                continue
            move.custom_sale_line_id = move.sale_line_id.id or False

    # ------------------------------------------------------------------
    # Fields from odoo_collection_return
    # ------------------------------------------------------------------

    original_return_move_id = fields.Many2one(
        'stock.move',
        string='Original Return Move',
        copy=False,
        index=True,
    )
    new_return_move_id = fields.Many2one(
        'stock.move',
        string='New Return Move',
        copy=False,
        index=True,
    )

    # ------------------------------------------------------------------
    # Fields from odoo_delivery_invoice
    # ------------------------------------------------------------------

    delivery_return_history_ids = fields.Many2many(
        'delivery.return.history',
        'stock_move_delivery_return_history_rel',
        'move_id',
        'history_id',
        string='Return History',
        copy=False,
    )

    # invoice line back-references
    custom_invoice_line_ids = fields.One2many(
        'account.move.line',
        'custom_move_id',
        string='Invoice Lines',
        copy=False,
    )
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

    # Invoiced quantity — sum of qty on linked invoice lines
    invoiced_quantity = fields.Float(
        string='Invoiced Qty',
        digits='Product Unit of Measure',
        compute='_compute_invoiced_quantity',
        store=True,
    )
    lost_product_invoiced_qty = fields.Float(
        string='Lost Material Invoiced Qty',
        digits='Product Unit of Measure',
        compute='_compute_lost_product_invoiced_qty',
        store=True,
    )

    # New return / remaining / invoicing quantities (based on history records)
    new_return_quantity = fields.Float(
        string='Returned Qty',
        digits='Product Unit of Measure',
        compute='_compute_new_return_quantity',
        store=True,
    )
    new_remaining_quantity = fields.Float(
        string='Remaining Qty',
        digits='Product Unit of Measure',
        compute='_compute_new_remaining_quantity',
        store=True,
    )
    new_reserved_quantity = fields.Float(
        string='Reserved Qty',
        digits='Product Unit of Measure',
        compute='_compute_new_reserved_quantity',
        store=True,
    )
    new_invoicing_quantity = fields.Float(
        string='To Invoice Qty',
        digits='Product Unit of Measure',
        compute='_compute_new_invoicing_quantity',
        store=True,
    )
    qty_to_return = fields.Float(
        string='Qty To Return',
        digits='Product Unit of Measure',
        compute='_compute_qty_to_return',
    )

    # Temporary field used during invoice creation to track partial return invoicing
    tmp_invoiced_qty = fields.Float(
        string='Tmp Invoiced Qty',
        digits='Product Unit of Measure',
        default=0.0,
        copy=False,
    )

    product_internal_reference = fields.Char(
        string='Internal Reference',
        related='product_id.default_code',
        store=True,
        readonly=True,
    )

    # Repair / Damage quantities captured inline on a collection (incoming) move.
    # Entered per the user's workflow as two extra columns next to Demand.
    # On collection validate these spawn collection.repair.damage lines
    # (repair -> repair_price, damage -> lost_price). Kept INDEPENDENT of the
    # rental return credit by design (user manages overlap manually).
    repair_qty = fields.Float(
        string='Repair',
        digits='Product Unit of Measure',
        default=0.0,
        copy=False,
        help="Units returned damaged but repairable. Billed at the product's "
             "Repair Price on a separate Repair & Damage invoice.",
    )
    damage_qty = fields.Float(
        string='Damage',
        digits='Product Unit of Measure',
        default=0.0,
        copy=False,
        help="Units damaged beyond repair. Billed at the product's Lost Price "
             "(damage = lost) on a separate Repair & Damage invoice.",
    )

    @api.onchange('repair_qty', 'damage_qty', 'quantity', 'product_uom_qty')
    def _onchange_repair_damage_qty(self):
        """Soft, non-blocking live warning when Repair + Damage exceeds the
        collected quantity on a collection (incoming) move. Combined check
        (repair + damage vs collected) per the user's decision -- it never
        blocks saving, it only flags a likely data-entry mistake.

        On a not-yet-validated collection the collected qty is the Demand
        (product_uom_qty); on a validated one it is the Done qty (quantity).
        """
        for move in self:
            if move.picking_code != 'incoming':
                continue
            rd = (move.repair_qty or 0.0) + (move.damage_qty or 0.0)
            collected = move.quantity or move.product_uom_qty or 0.0
            if rd > collected + 0.0001:
                return {'warning': {
                    'title': _("Repair/Damage exceeds collected quantity"),
                    'message': _(
                        "%(prod)s: Repair (%(r)s) + Damage (%(d)s) = %(sum)s, "
                        "but only %(c)s were collected. The values are still "
                        "saved — please double-check the quantities."
                    ) % {
                        'prod': move.product_id.display_name or _("This product"),
                        'r': move.repair_qty or 0.0,
                        'd': move.damage_qty or 0.0,
                        'sum': rd,
                        'c': collected,
                    },
                }}

    # ------------------------------------------------------------------
    # Compute methods
    # ------------------------------------------------------------------

    @api.depends('custom_invoice_line_ids', 'custom_invoice_line_ids.quantity',
                 'custom_invoice_line_ids.move_id.state')
    def _compute_invoiced_quantity(self):
        for move in self:
            lines = move.custom_invoice_line_ids.filtered(
                lambda l: l.move_id.state not in ('cancel',)
                and l.move_id.move_type == 'out_invoice'
                and not l.is_transport_product
            )
            move.invoiced_quantity = sum(lines.mapped('quantity'))

    @api.depends('custom_invoice_line_ids', 'custom_invoice_line_ids.quantity',
                 'custom_invoice_line_ids.move_id.state',
                 'custom_invoice_line_ids.move_id.rental_invoice_type')
    def _compute_lost_product_invoiced_qty(self):
        for move in self:
            lines = move.custom_invoice_line_ids.filtered(
                lambda l: l.move_id.state not in ('cancel',)
                and l.move_id.rental_invoice_type == 'lost'
            )
            move.lost_product_invoiced_qty = sum(lines.mapped('quantity'))

    @api.depends(
        'delivery_return_history_ids',
        'delivery_return_history_ids.return_qty',
        'delivery_return_history_ids.return_move_id.state',
    )
    def _compute_new_return_quantity(self):
        for move in self:
            done_histories = move.delivery_return_history_ids.filtered(
                lambda h: h.return_move_id and h.return_move_id.state == 'done'
            )
            move.new_return_quantity = sum(done_histories.mapped('return_qty'))

    @api.depends('quantity', 'new_return_quantity')
    def _compute_new_remaining_quantity(self):
        for move in self:
            move.new_remaining_quantity = move.quantity - move.new_return_quantity

    @api.depends('new_remaining_quantity', 'invoiced_quantity')
    def _compute_new_reserved_quantity(self):
        for move in self:
            move.new_reserved_quantity = move.new_remaining_quantity - move.invoiced_quantity

    @api.depends('quantity', 'invoiced_quantity', 'new_return_quantity')
    def _compute_new_invoicing_quantity(self):
        for move in self:
            invoiceable = move.quantity - move.invoiced_quantity - move.new_return_quantity
            move.new_invoicing_quantity = invoiceable if invoiceable > 0.0 else 0.0

    def _compute_qty_to_return(self):
        """Total quantity still outstanding (not yet returned) across all delivery
        moves for the same product/order combination."""
        relevant = self.filtered(
            lambda m: m.picking_code == 'outgoing' and m.custom_sale_id
        )
        (self - relevant).qty_to_return = 0.0
        if not relevant:
            return
        siblings = self.env['stock.move'].search([
            ('custom_sale_id', 'in', relevant.custom_sale_id.ids),
            ('product_id', 'in', relevant.product_id.ids),
            ('picking_code', '=', 'outgoing'),
            ('state', '=', 'done'),
        ])
        by_key = {}
        for s in siblings:
            by_key.setdefault((s.custom_sale_id.id, s.product_id.id), []).append(s)
        for move in relevant:
            group = by_key.get((move.custom_sale_id.id, move.product_id.id), [])
            total_delivered = sum(g.quantity for g in group)
            total_returned = sum(g.new_return_quantity for g in group)
            move.qty_to_return = max(total_delivered - total_returned, 0.0)

    # ------------------------------------------------------------------
    # Override write — intercept received qty on collection moves
    # ------------------------------------------------------------------

    def write(self, vals):
        """When quantity is set on a done collection move, distribute it across
        the original delivery_return_history records."""
        res = super().write(vals)
        if 'quantity' in vals and not self.env.context.get('_hksf_in_return_split'):
            for move in self.filtered(
                lambda m: m.picking_code == 'incoming' and m.state == 'done'
            ):
                move.with_context(
                    _hksf_in_return_split=True
                )._process_return_move_wizard()
        return res

    def _process_return_move_wizard(self):
        """Distribute received qty across original delivery_return_history records
        FIFO: fill the oldest delivery fully first, remainder to the next
        (mirrors O11 _onchange_total_qty_return). Any leftover beyond all
        delivery caps is dumped on the first (oldest) history."""
        histories = self.delivery_return_history_ids.sorted(
            key=lambda h: (
                h.deliver_move_id.picking_id.scheduled_date_only
                or fields.Date.today(),
                h.deliver_move_id.date or fields.Datetime.now(),
            )
        )
        remaining = self.quantity
        for history in histories:
            cap = history.delivered_qty or 0.0
            take = min(remaining, cap) if remaining > 0.0 else 0.0
            if history.return_qty != take:
                history.return_qty = take
            remaining -= take
        if remaining > 0.0 and histories:
            first = histories[0]
            first.return_qty += remaining

    # ------------------------------------------------------------------
    # Override unlink — clean up history records
    # ------------------------------------------------------------------

    def _own_return_histories(self):
        """Return only the history records this move owns, scoped by role:
        a delivery (outgoing) move owns histories where it is the deliver_move;
        a collection (incoming) move owns histories where it is the return_move.
        Prevents cancelling/unlinking one side of a shared history from silently
        stripping the credit linkage off the other side (O11 scoped the same way).
        """
        histories = self.env['delivery.return.history']
        for move in self:
            histories |= move.delivery_return_history_ids.filtered(
                lambda h: h.deliver_move_id == move or h.return_move_id == move
            )
        return histories

    def unlink(self):
        self._own_return_histories().unlink()
        return super().unlink()

    # ------------------------------------------------------------------
    # Override _action_cancel — remove history records
    # ------------------------------------------------------------------

    def _action_cancel(self):
        self._own_return_histories().unlink()
        return super()._action_cancel()
