# -*- coding: utf-8 -*-
"""
collection_repair_damage.py
============================
Base model for lost and damaged/repair material lines linked to a sale order
or a collection picking.

Ported from:
  odoo_delivery_collection/models/stock.py  (CollectionRepairDamage base)
  odoo_delivery_invoice/models/collection_damage.py  (extensions)

Odoo 19 changes:
  - product.uom → uom.uom
  - @api.multi removed — all methods are recordset-style
  - @api.one removed — use standard self loop
  - account.invoice → account.move
  - account.invoice.line → account.move.line
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CollectionRepairDamage(models.Model):
    _name = 'collection.repair.damage'
    _description = 'Collection / Repair / Damage Line'
    _rec_name = 'product_id'

    # -----------------------------------------------------------------------
    # Computed
    # -----------------------------------------------------------------------
    @api.depends('qty', 'price_unit', 'extra_charge')
    def _compute_price_subtotal(self):
        for rec in self:
            rec.price_subtotal = (rec.qty * rec.price_unit) + rec.extra_charge

    @api.depends('qty', 'collected_qty')
    def _compute_lost_products_qty(self):
        # NB: this computes ONLY lost_products_qty. It must NOT touch
        # price_subtotal -- that field is owned solely by
        # _compute_price_subtotal (qty*price_unit + extra_charge). The old
        # O11 port also wrote price_subtotal here with a different formula
        # ((qty-collected)*price_unit), which silently corrupted the subtotal
        # whenever collected_qty changed. Removed. (audit v19.0.1.26.0)
        for line in self:
            line.lost_products_qty = line.qty - line.collected_qty

    # -----------------------------------------------------------------------
    # Fields
    # -----------------------------------------------------------------------
    product_id = fields.Many2one('product.product', string='Product', required=True)
    # internal_ref is optional: when the product has no Internal Reference
    # (default_code) we fall back to the product name so manual entry is never
    # blocked by a required field that can legitimately be empty. (v19.0.1.27.0)
    internal_ref = fields.Char(string='Description', required=False)
    qty = fields.Float(string='Quantity', required=True)
    uom_id = fields.Many2one('uom.uom', string='Unit of Measure', required=True)
    price_unit = fields.Float(string='Price Unit', required=True)
    extra_charge = fields.Float(string='Extra Charge')
    price_subtotal = fields.Float(
        string='Price Subtotal',
        compute='_compute_price_subtotal',
        store=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    type = fields.Selection(
        selection=[
            ('repair', 'Repair'),      # repairable damage -> repair_price
            ('damage', 'Damage'),      # beyond repair -> lost_price (damage = lost)
            ('lost', 'Lost'),          # unrecovered -> lost_price
        ],
        string='Type',
        copy=False,
    )
    # Picking-level link (from collection picking)
    collection_picking_id = fields.Many2one('stock.picking', string='Stock Picking')
    # Order-level link
    order_id = fields.Many2one('sale.order', string='Sale Order')
    move_id = fields.Many2one('stock.move', string='Move')
    picking_lost_id = fields.Many2one('stock.picking', string='Lost Picking')

    # Invoice tracking
    invoice_id = fields.Many2one('account.move', string='Invoice', copy=False)
    invoiced_qty = fields.Float(string='Invoiced Quantity', copy=False)
    lost_product_invoiced_qty = fields.Float(string='Lost Material Invoiced Qty', copy=False)
    invoice_line_ids = fields.Many2many(
        'account.move.line',
        string='Invoice Lines',
        readonly=True,
    )

    # Collected qty (related from stock move return quantity)
    collected_qty = fields.Float(
        string='Collected Qty',
        related='move_id.new_return_quantity',
        store=True,
    )
    lost_products_qty = fields.Float(
        string='Lost Qty',
        compute='_compute_lost_products_qty',
    )

    # -----------------------------------------------------------------------
    # Onchange
    # -----------------------------------------------------------------------
    def _price_for_type(self, line_type, order):
        """Return the unit price for a manual line of the given type, sourced
        first from the matching sale-order line's stamped prices, then from the
        relevant pricelist, then the product list price as a last resort.

        Pricing by type (per user's R&D model):
          - repair        -> repair_price  (repair pricelist)
          - damage / lost  -> lost_price    (lost pricelist; damage = lost)

        extra_charge is NOT used to carry the lost price -- the unit price
        already reflects the correct per-unit charge for the type. extra_charge
        is left for genuine one-off surcharges only. (v19.0.1.27.0)
        """
        self.ensure_one()
        use_lost = line_type in ('damage', 'lost')
        # 1) Stamped price on the matching order line.
        if order:
            sol = order.order_line.filtered(
                lambda l: l.product_id == self.product_id
            )[:1]
            if sol:
                price = sol.lost_price if use_lost else sol.repair_price
                if price:
                    return price
        # 2) Pricelist on the order.
        pricelist = (order.lost_pricelist_id if use_lost else order.repair_pricelist_id) \
            if order else False
        if pricelist:
            return pricelist._get_product_price(self.product_id, self.qty or 1.0)
        # 3) Last resort: product list price.
        return self.product_id.lst_price

    @api.onchange('product_id', 'type')
    def onchange_product(self):
        for rec in self:
            if rec.product_id:
                # Description: Internal Reference, falling back to the product
                # name when default_code is empty (field is optional now).
                rec.internal_ref = rec.product_id.default_code or rec.product_id.name
                rec.uom_id = rec.product_id.uom_id
                if not rec.qty:
                    rec.qty = 1.0
                order_id = rec.order_id
                if not order_id and rec.collection_picking_id:
                    order_id = rec.collection_picking_id.custom_sale_order_id
                # Price strictly by type; no extra_charge surcharge injected.
                rec.price_unit = rec._price_for_type(rec.type, order_id)
                rec.extra_charge = 0.0

    @api.onchange('qty')
    def onchange_product_qty(self):
        for rec in self:
            if rec.product_id and rec.qty:
                order_id = rec.order_id
                if not order_id and rec.collection_picking_id:
                    order_id = rec.collection_picking_id.custom_sale_order_id
                rec.price_unit = rec._price_for_type(rec.type, order_id)

    # -----------------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------------
    def unlink(self):
        if not self._context.get('force_unlink', False):
            for line in self:
                if line.invoice_id:
                    raise UserError(_("You cannot delete an invoiced line!"))
        return super().unlink()
