# -*- coding: utf-8 -*-
# v19.0.1.76.0
# One-time backfill for the new active-rental tracking fields on sale.order:
#   - on_hire_qty      (delivered done-moves minus collected done-moves)
#   - is_active_rental (confirmed rental master with on_hire_qty > 0)
#
# Stored computed fields are normally recomputed by Odoo on module update, but
# for a large order history that implicit recompute can be slow/unbounded. We
# trigger the recompute explicitly on billing MASTERS only (children always
# resolve to False), which is exactly the scope _compute_active_rental cares
# about. Idempotent and safe to re-run.
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Guard: the new columns must exist (they are added by the ORM before
    # post-migration scripts run on update).
    cr.execute("""
        SELECT count(*) FROM information_schema.columns
        WHERE table_name = 'sale_order'
          AND column_name IN ('on_hire_qty', 'is_active_rental')
    """)
    if cr.fetchone()[0] < 2:
        _logger.warning(
            "19.0.1.76.0: on_hire_qty / is_active_rental columns missing; "
            "skipping active-rental backfill."
        )
        return

    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Billing masters only (billing_master_id is NULL). Compute in batches so a
    # very large catalogue does not build one huge recordset.
    masters = env['sale.order'].search([('billing_master_id', '=', False)])
    _logger.info(
        "19.0.1.76.0: backfilling active-rental flags on %d billing master(s).",
        len(masters),
    )
    BATCH = 500
    for i in range(0, len(masters), BATCH):
        masters[i:i + BATCH]._compute_active_rental()
    cr.commit()
    active = env['sale.order'].search_count([('is_active_rental', '=', True)])
    _logger.info("19.0.1.76.0: %d order(s) flagged as Active Rental.", active)
