# -*- coding: utf-8 -*-
"""
delivery_return_history.py
===========================
Central link table between a delivery stock.move and its collection/return move.

Each record is created by the return_move_select wizard when a customer returns
(collects) scaffolding. The invoice wizard reads these records to compute
credit lines for collected material.

Odoo 19 changes applied during consolidation:
  - delivery/collection move field unified on the deliver_move_id name
  - display name now computed via _compute_display_name populating display_name
"""
from odoo import api, fields, models


class DeliveryReturnHistory(models.Model):
    _name = 'delivery.return.history'
    _description = 'Delivery / Collection Return History'

    deliver_move_id = fields.Many2one(
        'stock.move',
        string='Delivered Move',
        ondelete='cascade',
    )
    return_move_id = fields.Many2one(
        'stock.move',
        string='Return (Collection) Move',
        ondelete='set null',
    )
    return_qty = fields.Float(string='Return Qty')
    delivered_qty = fields.Float(string='Delivered Qty')

    # Invoice linkage
    invoice_id = fields.Many2one(
        'account.move',
        string='Invoice',
        copy=False,
    )
    deliver_invoice_line_id = fields.Many2one(
        'account.move.line',
        string='Delivery Invoice Line',
        copy=False,
    )
    return_invoice_line_id = fields.Many2one(
        'account.move.line',
        string='Return Invoice Line',
        copy=False,
    )

    @api.depends('return_move_id', 'return_move_id.picking_id.name', 'return_qty')
    def _compute_display_name(self):
        for rec in self:
            name = rec.return_move_id.picking_id.name if rec.return_move_id else 'New'
            rec.display_name = '%s - Return: %s' % (name, rec.return_qty)
