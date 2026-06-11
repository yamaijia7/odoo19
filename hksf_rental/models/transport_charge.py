# -*- coding: utf-8 -*-
"""
transport_charge.py
====================
Transport billing lines attached to a stock.picking.
Created manually on a delivery or collection order when transportation
is handled by HKSF ("by_us"). The invoice wizard picks these up and
adds them as service lines on the rental invoice.

Ported from odoo_delivery_invoice/models/transport_charge.py

Odoo 19 changes:
  - product.uom → uom.uom
  - account.invoice → account.move
  - @api.multi removed
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class TransportCharge(models.Model):
    _name = 'transport.charge'
    _description = 'Transport Charge Line'

    @api.depends('product_uom_qty', 'price_unit')
    def _compute_amount(self):
        for line in self:
            line.price_subtotal = line.price_unit * line.product_uom_qty

    picking_id = fields.Many2one('stock.picking', string='Picking')
    name = fields.Text(string='Description', required=True)
    price_unit = fields.Float(
        string='Unit Price',
        required=True,
        digits='Product Price',
        default=0.0,
    )
    price_subtotal = fields.Float(
        string='Subtotal',
        compute='_compute_amount',
        store=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        domain=[('sale_ok', '=', True), ('type', '=', 'service')],
        required=True,
    )
    product_uom_qty = fields.Float(
        string='Quantity',
        digits='Product Unit of Measure',
        required=True,
        default=1.0,
    )
    product_uom = fields.Many2one('uom.uom', string='Unit of Measure', required=True)
    is_create_transport_invoice = fields.Boolean(string='Invoice Generated?')
    invoice_id = fields.Many2one('account.move', string='Invoice', copy=False, readonly=True)
    transportation_method = fields.Selection(
        related='picking_id.transportation_method',
        string='Transportation Method',
        store=True,
        readonly=True,
    )
    license_plate = fields.Char(
        related='picking_id.license_plate',
        string='License Plate',
        store=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        'res.company',
        related='picking_id.company_id',
        string='Company',
        store=True,
    )

    @api.onchange('product_id')
    def product_id_change(self):
        if not self.product_id:
            return
        self.product_uom = self.product_id.uom_id
        self.product_uom_qty = 1.0
        self.price_unit = self.product_id.lst_price
        name = self.product_id.display_name
        if self.product_id.description_sale:
            name += '\n' + self.product_id.description_sale
        self.name = name

    def unlink(self):
        for line in self:
            if line.invoice_id:
                raise UserError(_("You cannot delete a line that has already been invoiced!"))
        return super().unlink()
