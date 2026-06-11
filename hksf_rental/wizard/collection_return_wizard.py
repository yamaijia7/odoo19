# -*- coding: utf-8 -*-
"""Create Collection From Sale Order (ported from Odoo 11 collection_create_from_so).

Adds an order-level "Create Collection" action: from a confirmed sale order it
computes the outstanding products (delivered but not yet collected) and creates a
single incoming (collection) picking covering all of them.

Odoo 19 differences vs the original Odoo 11 module:
  - ``@api.multi`` removed.
  - ``stock.picking.move_lines`` is now ``move_ids``; moves are created directly.
  - ``product.outstanding`` has no ``scrapped_qty`` field; ``lost_products_qty`` is
    already ``max(delivered - collected, 0)``. The O11 wizard summed two filters
    (outstanding + negative/lost) which, under the v19 model, are the SAME records
    -- so we use a single ``delivered_qty > collected_qty`` filter to avoid
    double-counting.
  - Picking/move creation idioms mirror the existing v19 return collection wizard.
"""
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class CollectionReturnWizard(models.TransientModel):
    _name = 'collection.return.wizard'
    _description = 'Create Collection From Sale Order'

    collection_date = fields.Date(
        string='Collection Date',
        required=True,
        default=fields.Date.context_today,
    )

    def action_create_collection(self):
        """Create one incoming (collection) picking for the order's outstanding
        products (delivered qty still out in the field)."""
        self.ensure_one()
        order = self.env['sale.order'].browse(self._context.get('active_id'))
        if not order:
            raise UserError(_("No sale order in context."))

        # Refresh delivered-vs-collected figures, then take everything still out.
        order.action_compute_outstanding_products()
        outstanding_lines = order.product_outstanding_ids.filtered(
            lambda r: r.delivered_qty > r.collected_qty
        )
        if not outstanding_lines:
            raise UserError(_("There are no outstanding products to collect for this order."))

        warehouse = order.warehouse_id
        if not warehouse:
            raise UserError(_("The sale order has no warehouse set."))

        # The collection is the RETURN of the outgoing operation type.
        out_type = self.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        return_type = out_type.return_picking_type_id or self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        if not return_type:
            raise UserError(_("No incoming/return operation type found for warehouse %s.") % warehouse.name)

        location_id = return_type.default_location_src_id
        location_dest_id = return_type.default_location_dest_id

        site_contact_ids = [(4, c.id) for c in order.site_contact_ids] if 'site_contact_ids' in order._fields else False

        picking_vals = {
            'partner_id': order.partner_shipping_id.id or order.partner_id.id,
            'company_id': order.company_id.id,
            'location_id': location_id.id,
            'location_dest_id': location_dest_id.id,
            'scheduled_date_only': self.collection_date,
            'picking_type_id': return_type.id,
            'origin': order.name,
            'custom_sale_order_id': order.id,
            'partner_parent_id': order.partner_id.parent_id.id or order.partner_id.id,
        }
        if site_contact_ids:
            picking_vals['site_contact_ids'] = site_contact_ids
        picking = self.env['stock.picking'].create(picking_vals)

        History = self.env['delivery.return.history']
        for line in outstanding_lines:
            qty_left = line.delivered_qty - line.collected_qty
            collection_move = self.env['stock.move'].create({
                # v19 stock.move has no 'name' field; use description_picking.
                'description_picking': line.product_id.name,
                'company_id': order.company_id.id,
                'product_id': line.product_id.id,
                'product_uom': line.uom_id.id or line.product_id.uom_id.id,
                'product_uom_qty': qty_left,
                'partner_id': order.partner_shipping_id.id or order.partner_id.id,
                'location_id': location_id.id,
                'location_dest_id': location_dest_id.id,
                'origin': order.name,
                'picking_id': picking.id,
                'date': self.collection_date,
                'custom_sale_id': order.id,
                'state': 'draft',
            })

            # ----------------------------------------------------------------
            # Link this collection move back to the originating delivery
            # move(s) via delivery.return.history. Without these records the
            # delivery-invoice wizard cannot generate the return credit lines
            # (it iterates move.delivery_return_history_ids on the DELIVERY
            # move). Mirrors return_move_select_wizard.action_confirm.
            #
            # Distribute the collected qty across the product's done delivery
            # moves FIFO (earliest delivery first), capped by each move's
            # already-returned qty so we never over-credit a delivery.
            # ----------------------------------------------------------------
            remaining = qty_left
            delivery_moves = line.outgoing_move_ids.filtered(
                lambda m: m.state == 'done'
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
                    'return_move_id': collection_move.id,
                    'return_qty': alloc,
                    'delivered_qty': dmove.quantity,
                })
                dmove.delivery_return_history_ids = [(4, history.id)]
                collection_move.delivery_return_history_ids = [(4, history.id)]
                remaining -= alloc

        return {
            'type': 'ir.actions.act_window',
            'name': _('Collection'),
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'target': 'current',
        }
