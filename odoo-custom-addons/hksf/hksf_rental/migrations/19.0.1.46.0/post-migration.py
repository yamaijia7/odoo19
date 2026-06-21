# -*- coding: utf-8 -*-
# v19.0.1.46.0
# ---------------------------------------------------------------------------
# Re-allocate existing delivery.return.history rows using the corrected,
# TWO-DIMENSIONAL FIFO in StockPicking._resync_collection_histories.
#
# Bug fixed in v46.0
# ------------------
# v45.0 fixed the allocation of return_qty ACROSS COLLECTIONS (collection-date
# FIFO), but it kept each history pinned to whatever delivery move it was
# originally created against. When a product was delivered in MULTIPLE tranches
# (e.g. 189 on 23/05 then 130 more on 12/06) and collected in multiple batches,
# the later collection (30/06) was still attributed to the OLD May tranche
# instead of spilling into the recent 12/06 tranche. Because the recent tranche
# is inside its minimum rental period, O11 bills an 11-day MINIMUM CHARGE on it;
# O19 lost that charge entirely.
#
# Example (S00004 / invoice 25/06/101, the 30/06 WH/IN/00025 "Deducted" block):
#   HJ6038 101 collected on 30/06 -> O11 = 61 (old, $0) + 40 (new, 11-day min
#   = $117.33); O19 had all 101 on the old tranche -> $0. Same pattern on
#   BJ6038 (33), CNLY (24), LG091 (5), CB43 (12), CB22 (4), LG067 (17),
#   FSSC (11). Net understatement = $307.81 of minimum charges (full 30/06
#   min-charge subtotal $332.19 vs O19 $24.38).
#
# v46.0 rebuilds the histories with FIFO on BOTH axes:
#   * collections processed earliest-first (by scheduled_date_only)
#   * delivery tranches consumed oldest-first
# so each collected unit is attributed to the correct tranche and minimum
# charges land exactly as in Odoo 11.
#
# This migration re-runs the corrected resync for every done incoming
# (collection) picking. It is idempotent: the resync is a pure function of the
# validated move quantities + delivery/collection dates, so re-running converges
# to the same allocation. Posted-invoice histories are left untouched by the
# resync's deletion guard; DRAFT invoices that were generated from the old
# (wrong) histories must be regenerated via the delivery invoice wizard to pick
# up the corrected credit + minimum-charge lines. See the v46.0 deploy notes.
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: no historical collections to realign.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    if 'delivery.return.history' not in env:
        _logger.info("hksf_rental v46: delivery.return.history model absent, "
                     "skipping FIFO re-allocation.")
        return

    Picking = env['stock.picking']
    collections = Picking.search([
        ('picking_type_code', '=', 'incoming'),
        ('state', '=', 'done'),
    ])
    if not collections:
        _logger.info("hksf_rental v46: no done collection pickings to realign.")
        return

    # The resync widens to all sibling collections of the same order itself, so
    # processing each picking once is sufficient (and idempotent). We still loop
    # defensively so one bad picking never aborts the whole upgrade.
    processed = 0
    for picking in collections:
        try:
            picking._resync_collection_histories()
            processed += 1
        except Exception:  # pragma: no cover - never block the upgrade
            _logger.exception(
                "hksf_rental v46: two-dimensional FIFO re-allocation failed for "
                "picking %s (id=%s); leaving its histories unchanged.",
                picking.name, picking.id,
            )

    _logger.info(
        "hksf_rental v46: re-ran two-dimensional (tranche + collection) FIFO "
        "resync over %s done collection pickings. NOTE: previously generated "
        "DRAFT invoices must be regenerated to pick up corrected credit and "
        "minimum-charge lines.",
        processed,
    )
