# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class SaleLineQtyUpdateWizard(models.TransientModel):
    """Batch-update ordered quantity on sale order lines from delivered qty."""
    _name = 'sale.line.qty.update.wizard'
    _description = 'Sale Line Qty Update Wizard'

    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        required=True,
    )
    line_ids = fields.One2many(
        'sale.line.qty.update.wizard.line',
        'wizard_id',
        string='Lines',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self._context.get('active_id')
        if active_id:
            res['sale_order_id'] = active_id
            order = self.env['sale.order'].browse(active_id)
            lines = []
            for line in order.order_line.filtered(
                lambda l: l.product_id.type in ('product', 'consu')
            ):
                lines.append((0, 0, {
                    'sale_line_id': line.id,
                    'product_id': line.product_id.id,
                    'current_qty': line.product_uom_qty,
                    'delivered_qty': line.qty_delivered,
                    'new_qty': line.qty_delivered,
                }))
            res['line_ids'] = lines
        return res

    def action_update_quantities(self):
        self.ensure_one()
        for wline in self.line_ids:
            if wline.new_qty != wline.sale_line_id.product_uom_qty:
                wline.sale_line_id.product_uom_qty = wline.new_qty
        return {'type': 'ir.actions.act_window_close'}


class SaleLineQtyUpdateWizardLine(models.TransientModel):
    _name = 'sale.line.qty.update.wizard.line'
    _description = 'Sale Line Qty Update Wizard Line'

    wizard_id = fields.Many2one(
        'sale.line.qty.update.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Sale Line',
        required=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        related='sale_line_id.product_id',
        readonly=True,
    )
    current_qty = fields.Float(
        string='Current Ordered Qty',
        digits='Product Unit of Measure',
        readonly=True,
    )
    delivered_qty = fields.Float(
        string='Delivered Qty',
        digits='Product Unit of Measure',
        readonly=True,
    )
    new_qty = fields.Float(
        string='New Ordered Qty',
        digits='Product Unit of Measure',
        default=0.0,
    )
