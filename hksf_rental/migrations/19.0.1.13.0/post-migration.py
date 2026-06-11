# -*- coding: utf-8 -*-
"""Back-fill delivery.return.history for collections created BEFORE 19.0.1.10.0.

Problem
-------
The 19.0.1.10.0 fix made the SO-level "Create Collection" wizard write
``delivery.return.history`` records linking each collection (incoming) move to
its originating delivery (outgoing) move(s). The delivery-invoice wizard builds
the return-credit / minimum-charge lines EXCLUSIVELY from
``move.delivery_return_history_ids`` on the delivery move.

Collections created before that fix have NO history records, so they remain
invisible to invoicing (no credit, no "minimum charge applied" line) — even
though the collection move itself exists and is tagged with ``custom_sale_id``.

Fix
---
For every incoming (collection) stock.move that:
  * is linked to an order via ``custom_sale_id``,
  * has NO ``delivery_return_history_ids`` yet,
reconstruct the history by allocating the move's quantity FIFO across that
product's done delivery (outgoing) moves on the same order, capped by each
delivery move's remaining (delivered - already-returned) capacity.

This mirrors wizard/collection_return_wizard.action_create_collection exactly,
so back-filled collections behave identically to newly created ones.

Idempotent: only collection moves with EMPTY history are touched, so re-running
``-u hksf_rental`` will not double-create records.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Move = env['stock.move']
    History = env['delivery.return.history']

    # All collection (incoming) moves tagged to an order but missing history.
    collection_moves = Move.search([
        ('custom_sale_id', '!=', False),
        ('picking_code', '=', 'incoming'),
        ('delivery_return_history_ids', '=', False),
    ])
    if not collection_moves:
        _logger.info("hksf_rental 1.13.0: no collection moves need back-fill.")
        return

    created = 0
    healed_moves = 0

    # Group by (order, product) so allocation across a product's deliveries is
    # consistent even when a product was collected over several moves.
    by_order = {}
    for cm in collection_moves:
        by_order.setdefault(cm.custom_sale_id.id, env['stock.move'])
        by_order[cm.custom_sale_id.id] |= cm

    for order_id, cmoves in by_order.items():
        # Delivery (done, outgoing) moves for this order, indexed by product.
        delivery_moves = Move.search([
            ('custom_sale_id', '=', order_id),
            ('picking_code', '=', 'outgoing'),
            ('state', '=', 'done'),
        ])

        # Process collection moves oldest-first so earlier collections claim
        # delivery capacity before later ones (matches chronological reality).
        for cm in cmoves.sorted(lambda m: (m.date or m.create_date, m.id)):
            # Quantity actually collected: prefer done qty, fall back to demand.
            remaining = cm.quantity or cm.product_uom_qty
            if remaining <= 0.0:
                continue

            prod_deliveries = delivery_moves.filtered(
                lambda m: m.product_id == cm.product_id
            ).sorted(lambda m: (m.date or m.create_date, m.id))

            move_made_history = False
            for dmove in prod_deliveries:
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
                    'return_move_id': cm.id,
                    'return_qty': alloc,
                    'delivered_qty': dmove.quantity,
                })
                dmove.delivery_return_history_ids = [(4, history.id)]
                cm.delivery_return_history_ids = [(4, history.id)]
                remaining -= alloc
                created += 1
                move_made_history = True

            if move_made_history:
                healed_moves += 1
            elif not prod_deliveries:
                _logger.warning(
                    "hksf_rental 1.13.0: collection move %s (product %s, order %s) "
                    "has no matching done delivery move; left without history.",
                    cm.id, cm.product_id.display_name, order_id,
                )

    _logger.info(
        "hksf_rental 1.13.0: back-filled %s delivery.return.history record(s) "
        "across %s collection move(s).", created, healed_moves,
    )
