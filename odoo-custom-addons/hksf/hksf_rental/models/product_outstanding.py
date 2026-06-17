# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ProductOutstanding(models.Model):
    """Per-order outstanding quantity summary (delivered vs collected)."""
    _name = 'product.outstanding'
    _description = 'Product Outstanding'
    _rec_name = 'product_id'

    order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        required=True,
        ondelete='cascade',
        index=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
    )
    uom_id = fields.Many2one(
        'uom.uom',
        string='Unit of Measure',
    )
    outgoing_move_ids = fields.Many2many(
        'stock.move',
        'product_outstanding_delivery_move_rel',
        'outstanding_id',
        'move_id',
        string='Delivery Moves',
        domain=[('picking_code', '=', 'outgoing')],
    )
    incoming_move_ids = fields.Many2many(
        'stock.move',
        'product_outstanding_incoming_move_rel',
        'outstanding_id',
        'move_id',
        string='Collection Moves',
        domain=[('picking_code', '=', 'incoming')],
    )
    delivered_qty = fields.Float(
        string='Delivered Qty',
        digits='Product Unit of Measure',
        default=0.0,
    )
    collected_qty = fields.Float(
        string='Collected Qty',
        digits='Product Unit of Measure',
        default=0.0,
    )
    lost_products_qty = fields.Float(
        string='Balance',
        digits='Product Unit of Measure',
        compute='_compute_lost_products_qty',
        store=True,
    )
    price_unit = fields.Float(
        string='Unit Price',
        digits='Product Price',
        compute='_compute_price_unit',
        store=True,
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('delivered_qty', 'collected_qty')
    def _compute_lost_products_qty(self):
        for rec in self:
            diff = rec.delivered_qty - rec.collected_qty
            rec.lost_products_qty = diff if diff > 0.0 else 0.0

    @api.depends('product_id', 'order_id', 'order_id.order_line.lost_price')
    def _compute_price_unit(self):
        """Unit Price = the LOST price this outstanding/lost unit will be billed
        at. The page's purpose is to calculate lost products, so we surface the
        stamped lost_price from the matching sale order line (set on confirm /
        Reload Price Lists). Falls back to the order's Lost pricelist, then 0.
        """
        for rec in self:
            price = 0.0
            order = rec.order_id
            product = rec.product_id
            if order and product:
                # 1. stamped lost_price on the matching sale order line
                sale_line = order.order_line.filtered(
                    lambda l: l.product_id == product and l.lost_price
                )[:1]
                if sale_line:
                    price = sale_line.lost_price
                # 2. fall back to the order's Lost / Damage pricelist
                elif order.lost_pricelist_id:
                    price = order.lost_pricelist_id._get_product_price(
                        product, rec.lost_products_qty or 1.0)
            rec.price_unit = price
