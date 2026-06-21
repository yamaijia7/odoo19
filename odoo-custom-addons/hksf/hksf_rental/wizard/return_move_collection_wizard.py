# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ReturnMoveCollectionWizard(models.TransientModel):
    """Creates a reverse (collection) stock picking from selected delivery move
    lines on a done outgoing picking."""
    _name = 'return.move.collection.wizard'
    _description = 'Return Move Collection Wizard'

    picking_id = fields.Many2one(
        'stock.picking',
        string='Source Picking',
        required=True,
    )
    line_ids = fields.One2many(
        'return.move.collection.wizard.line',
        'wizard_id',
        string='Lines',
    )
    scheduled_date_only = fields.Date(
        string='Collection Date',
        required=True,
    )
    transportation_method = fields.Selection(
        selection=[
            ('by_us', 'By Us'),
            ('by_customer', 'By Customer'),
        ],
        string='Transportation Method',
        default='by_us',
    )

    # ------------------------------------------------------------------
    # default_get — load lines from delivery picking
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self._context
        picking_id = ctx.get('active_id') or ctx.get('default_picking_id')
        if picking_id:
            res['picking_id'] = picking_id
            picking = self.env['stock.picking'].browse(picking_id)
            lines = []
            for move in picking.move_ids.filtered(lambda m: m.state == 'done'):
                lines.append((0, 0, {
                    'move_id': move.id,
                    'product_id': move.product_id.id,
                    'qty_done': move.quantity,
                    'return_qty': move.qty_to_return,
                }))
            res['line_ids'] = lines
        return res

    # ------------------------------------------------------------------
    # Confirm — create collection picking
    # ------------------------------------------------------------------

    def action_create_return_picking(self):
        self.ensure_one()
        lines = self.line_ids.filtered(lambda l: l.return_qty > 0.0)
        if not lines:
            raise UserError(_("Please enter quantities to return."))

        picking = self.picking_id
        sale_order = picking.custom_sale_order_id or picking.sale_id

        # Determine the incoming operation type for the same warehouse
        warehouse = picking.picking_type_id.warehouse_id
        incoming_type = self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        if not incoming_type:
            raise UserError(_("No incoming operation type found for warehouse %s.") % warehouse.name)

        new_picking_vals = {
            'picking_type_id': incoming_type.id,
            'location_id': picking.location_dest_id.id,
            'location_dest_id': picking.location_id.id,
            'partner_id': picking.partner_id.id,
            'origin': picking.name,
            'custom_sale_order_id': sale_order.id if sale_order else False,
            'scheduled_date_only': self.scheduled_date_only,
            'transportation_method': self.transportation_method,
            'original_return_picking_id': picking.id,
        }
        new_picking = self.env['stock.picking'].create(new_picking_vals)

        for line in lines:
            move_vals = {
                # v19 stock.move has no 'name' field; use description_picking.
                'description_picking': line.product_id.name,
                'product_id': line.product_id.id,
                'product_uom': line.move_id.product_uom.id,
                'product_uom_qty': line.return_qty,
                'picking_id': new_picking.id,
                'location_id': new_picking.location_id.id,
                'location_dest_id': new_picking.location_dest_id.id,
                'custom_sale_id': sale_order.id if sale_order else False,
                'original_return_move_id': line.move_id.id,
                'state': 'draft',
            }
            new_move = self.env['stock.move'].create(move_vals)
            # Link back on original delivery move
            line.move_id.new_return_move_id = new_move.id

        # Link new picking back to original
        picking.new_return_picking_id = new_picking.id

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': new_picking.id,
            'view_mode': 'form',
            'target': 'current',
        }


class ReturnMoveCollectionWizardLine(models.TransientModel):
    _name = 'return.move.collection.wizard.line'
    _description = 'Return Move Collection Wizard Line'

    wizard_id = fields.Many2one(
        'return.move.collection.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    move_id = fields.Many2one(
        'stock.move',
        string='Delivery Move',
        required=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        related='move_id.product_id',
        readonly=True,
    )
    qty_done = fields.Float(
        string='Delivered Qty',
        digits='Product Unit of Measure',
        readonly=True,
    )
    return_qty = fields.Float(
        string='Return Qty',
        digits='Product Unit of Measure',
        default=0.0,
    )
