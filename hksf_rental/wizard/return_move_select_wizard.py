# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ReturnMoveSelectWizard(models.TransientModel):
    """Assigns the collected qty on a return (incoming) stock.move back to the
    original delivery moves and creates delivery.return.history records linking
    them together."""
    _name = 'return.move.select.wizard'
    _description = 'Return Move Select Wizard'

    picking_id = fields.Many2one(
        'stock.picking',
        string='Collection Picking',
        required=True,
    )
    move_id = fields.Many2one(
        'stock.move',
        string='Collection Move',
        required=True,
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        compute='_compute_sale_order_id',
        store=True,
    )
    line_ids = fields.One2many(
        'return.move.select.wizard.line',
        'wizard_id',
        string='Delivery Move Lines',
    )
    total_qty_return = fields.Float(
        string='Total Qty To Return',
        digits='Product Unit of Measure',
        help='Enter the total collected quantity; it is spread across the '
             'delivery lines oldest-first (FIFO). You can still fine-tune each '
             'line manually afterwards.',
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('picking_id', 'picking_id.custom_sale_order_id', 'picking_id.sale_id')
    def _compute_sale_order_id(self):
        for rec in self:
            rec.sale_order_id = (
                rec.picking_id.custom_sale_order_id
                or rec.picking_id.sale_id
            )

    # ------------------------------------------------------------------
    # default_get — pre-populate delivery move lines
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self._context
        picking_id = ctx.get('default_picking_id') or ctx.get('active_id')
        move_id = ctx.get('default_move_id')
        if picking_id:
            res['picking_id'] = picking_id
        if move_id:
            res['move_id'] = move_id
        return res

    @api.onchange('move_id', 'picking_id')
    def _onchange_move_id(self):
        """Load delivery moves for the same product / sale order."""
        self.line_ids = [(5, 0, 0)]
        if not self.move_id or not self.picking_id:
            return
        sale_order = (
            self.picking_id.custom_sale_order_id
            or self.picking_id.sale_id
        )
        if not sale_order:
            return
        delivery_moves = self.env['stock.move'].search([
            ('custom_sale_id', '=', sale_order.id),
            ('product_id', '=', self.move_id.product_id.id),
            ('picking_code', '=', 'outgoing'),
            ('state', '=', 'done'),
        ], order='id asc')

        lines = []
        for dm in delivery_moves:
            lines.append((0, 0, {
                'deliver_move_id': dm.id,
                'delivered_qty': dm.quantity,
                'return_qty': 0.0,
            }))
        self.line_ids = lines

    # ------------------------------------------------------------------
    # FIFO spread — auto-allocate the entered total oldest-first
    # ------------------------------------------------------------------

    @api.onchange('total_qty_return')
    def _onchange_total_qty_return(self):
        """Spread total_qty_return across delivery lines FIFO: fill the oldest
        delivery (earliest scheduled_date_only, then move date) fully first,
        remainder to the next. Mirrors O11 _onchange_total_qty_return."""
        if self.total_qty_return <= 0.0:
            return
        remaining = self.total_qty_return
        sorted_lines = self.line_ids.sorted(
            key=lambda l: (
                l.deliver_move_id.picking_id.scheduled_date_only
                or fields.Date.today(),
                l.deliver_move_id.date or fields.Datetime.now(),
            )
        )
        for line in sorted_lines:
            cap = line.delivered_qty or 0.0
            if remaining > cap:
                line.return_qty = cap
                remaining -= cap
            elif remaining > 0.0:
                line.return_qty = remaining
                remaining = 0.0
            else:
                line.return_qty = 0.0

    # ------------------------------------------------------------------
    # Confirm — create / update delivery.return.history records
    # ------------------------------------------------------------------

    def action_confirm(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("No delivery lines to assign return quantities to."))

        total_return = sum(self.line_ids.mapped('return_qty'))
        if total_return <= 0.0:
            raise UserError(_("Please enter return quantities before confirming."))

        move = self.move_id
        for line in self.line_ids.filtered(lambda l: l.return_qty > 0.0):
            existing = self.env['delivery.return.history'].search([
                ('deliver_move_id', '=', line.deliver_move_id.id),
                ('return_move_id', '=', move.id),
            ], limit=1)
            if existing:
                existing.return_qty = line.return_qty
            else:
                history = self.env['delivery.return.history'].create({
                    'deliver_move_id': line.deliver_move_id.id,
                    'return_move_id': move.id,
                    'return_qty': line.return_qty,
                    'delivered_qty': line.delivered_qty,
                })
                # Link history to both moves
                line.deliver_move_id.delivery_return_history_ids = [(4, history.id)]
                move.delivery_return_history_ids = [(4, history.id)]
        return {'type': 'ir.actions.act_window_close'}


class ReturnMoveSelectWizardLine(models.TransientModel):
    _name = 'return.move.select.wizard.line'
    _description = 'Return Move Select Wizard Line'

    wizard_id = fields.Many2one(
        'return.move.select.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    deliver_move_id = fields.Many2one(
        'stock.move',
        string='Delivery Move',
        required=True,
    )
    delivered_qty = fields.Float(
        string='Delivered Qty',
        digits='Product Unit of Measure',
        readonly=True,
    )
    return_qty = fields.Float(
        string='Return Qty',
        digits='Product Unit of Measure',
        default=0.0,
    )
    picking_scheduled_date = fields.Date(
        string='Delivery Date',
        related='deliver_move_id.picking_id.scheduled_date_only',
        readonly=True,
    )
