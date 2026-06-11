# -*- coding: utf-8 -*-
"""Re-sync over-allocated delivery.return.history for collections that were
validated with a PARTIAL quantity (created before 19.0.1.14.0).

Problem
-------
The Create-Collection wizard pre-allocates one delivery.return.history per
delivery move for the FULL outstanding quantity at wizard time. If the user
then edits the collection picking to collect only part of that quantity before
validating (e.g. 46 of 144), the histories still carry the full delivered
quantities (35 + 109 = 144). The delivery-invoice wizard credits/charges the
SUM of history.return_qty, so the invoice line shows 144 units instead of the
46 actually collected.

The 19.0.1.14.0 stock.picking.button_validate now re-syncs histories to the
real collected qty on every NEW validation, but collections validated BEFORE
this fix already have the wrong histories stored.

Fix
---
For every DONE incoming (collection) move, re-distribute history.return_qty
FIFO across its linked delivery moves so the total equals the move's actual
done quantity. Histories that fall to zero are removed. delivered_qty is
re-pinned to each delivery move's quantity (used by the minimum-charge cap).

Only touches moves where the histories DO NOT already sum to the move qty, so
re-running -u hksf_rental is idempotent. Skips moves whose collection has
already been invoiced (invoice_id set, not cancelled) so we never disturb
posted figures.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.invalidate_all()  # raw rows may have been touched before this point
    Move = env['stock.move']

    collection_moves = Move.search([
        ('custom_sale_id', '!=', False),
        ('picking_code', '=', 'incoming'),
        ('state', '=', 'done'),
        ('delivery_return_history_ids', '!=', False),
    ])

    fixed = 0
    for move in collection_moves:
        histories = move.delivery_return_history_ids
        total_hist = sum(histories.mapped('return_qty'))
        collected = move.quantity
        # Already correct -> idempotent skip.
        if abs(total_hist - collected) < 0.001:
            continue
        # Do not disturb collections already tied to a posted/active invoice.
        picking = move.picking_id
        if picking and picking.invoice_id and picking.invoice_id.state != 'cancel':
            _logger.info(
                "hksf_rental 1.14.0: skip move %s (collection already invoiced).",
                move.id,
            )
            continue

        ordered = histories.sorted(
            lambda h: (h.deliver_move_id.date or h.deliver_move_id.create_date,
                       h.deliver_move_id.id)
        )
        remaining = collected
        for hist in ordered:
            other_returned = sum(
                h.return_qty for h in hist.deliver_move_id.delivery_return_history_ids
                if h.return_move_id != move
            )
            capacity = max(hist.deliver_move_id.quantity - other_returned, 0.0)
            alloc = min(capacity, remaining) if remaining > 0.0 else 0.0
            hist.write({
                'return_qty': alloc,
                'delivered_qty': hist.deliver_move_id.quantity,
            })
            remaining -= alloc
        ordered.filtered(lambda h: h.return_qty <= 0.0).unlink()
        fixed += 1

    _logger.info(
        "hksf_rental 1.14.0: re-synced %d collection move(s) to actual qty.", fixed
    )
