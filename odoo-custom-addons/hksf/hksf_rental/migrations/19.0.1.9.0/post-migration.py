# -*- coding: utf-8 -*-
"""Back-fill stock.move.custom_sale_line_id on existing records.

custom_sale_line_id became computed+stored in 19.0.1.9.0 so that HKSF features
keying off the originating sale line work for standard deliveries -- most
importantly Lost/Damaged "Sync from Pickings" (action_compute_lost_product),
which filters delivery moves on custom_sale_line_id.line_type == 'rental'.
On standard deliveries this field was previously NULL, so the filter dropped
every move and the Lost/Damaged tab stayed blank.

Like the 19.0.1.8.0 custom_sale_id migration, Odoo does not auto-recompute an
already-stored field on -u, so this forces a one-time back-fill of NULL rows
that have a sale_line_id. Idempotent; never touches rows with an explicit
value.
"""
import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Move = env['stock.move']

    cr.execute("""
        SELECT id
          FROM stock_move
         WHERE custom_sale_line_id IS NULL
           AND sale_line_id IS NOT NULL
    """)
    ids = [r[0] for r in cr.fetchall()]
    if not ids:
        _logger.info("hksf_rental 1.9.0: no stock.move rows need custom_sale_line_id back-fill")
        return

    moves = Move.browse(ids)
    moves.invalidate_recordset(['custom_sale_line_id'])
    moves._compute_custom_sale_line_id()
    moves.flush_recordset(['custom_sale_line_id'])
    _logger.info("hksf_rental 1.9.0: back-filled custom_sale_line_id on %s stock.move rows", len(ids))
