# -*- coding: utf-8 -*-
"""Back-fill stock.move.custom_sale_id on existing records.

custom_sale_id became a computed+stored field in 19.0.1.8.0. Odoo only
recomputes a stored field on upgrade when its *schema* changes; an already-
stored column whose definition was merely augmented with a compute is NOT
auto-recomputed for existing rows. Deliveries created before this version
therefore keep a NULL custom_sale_id and stay invisible to the HKSF features
(Outstanding Products, Lost/Damaged, Create Collection).

This migration forces the recomputation so existing deliveries are healed
without any need to recreate them.
"""
import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Move = env['stock.move']

    # Only touch rows that are currently NULL and have something to derive
    # from (the standard sale link or the picking's sale order). This keeps
    # the migration idempotent and avoids disturbing collection/return moves
    # that already carry an explicit value.
    cr.execute("""
        SELECT sm.id
          FROM stock_move sm
          LEFT JOIN stock_picking sp ON sp.id = sm.picking_id
         WHERE sm.custom_sale_id IS NULL
           AND (sm.sale_line_id IS NOT NULL OR sp.custom_sale_order_id IS NOT NULL)
    """)
    ids = [r[0] for r in cr.fetchall()]
    if not ids:
        _logger.info("hksf_rental 1.8.0: no stock.move rows need custom_sale_id back-fill")
        return

    moves = Move.browse(ids)
    # Invalidate any cached value then force the stored compute to run and flush.
    moves.invalidate_recordset(['custom_sale_id'])
    moves._compute_custom_sale_id()
    moves.flush_recordset(['custom_sale_id'])
    _logger.info("hksf_rental 1.8.0: back-filled custom_sale_id on %s stock.move rows", len(ids))
