# -*- coding: utf-8 -*-
# v19.0.1.45.0
# ---------------------------------------------------------------------------
# Re-allocate existing delivery.return.history rows using the corrected,
# date-ordered FIFO in StockPicking._resync_collection_histories.
#
# Bug fixed in v45.0: when the SAME delivery move was returned across MULTIPLE
# collections, the previous per-picking resync allocated each shared delivery
# move's capacity in VALIDATION order rather than COLLECTION-DATE order. The
# collection that happened to sync LAST was starved of capacity, so an earlier
# collection's credit was silently understated (e.g. S00004 / invoice 25/06/101:
# BJ6038 147->114, CNLY 119->95, HJ6038 128->88, LG091 170->165 on WH/IN/00024).
#
# This migration re-runs the corrected resync for every done incoming
# (collection) picking, so the stored return_qty / delivered_qty on existing
# histories are realigned to the FIFO-by-date contract. It is idempotent: the
# resync is a pure function of the validated move quantities + collection dates,
# so re-running it converges to the same allocation.
#
# NOTE: this corrects the HISTORY records (the source the invoice wizard reads).
# Invoices that were ALREADY generated from the old (wrong) histories are NOT
# rewritten here -- those draft invoices must be regenerated via the delivery
# invoice wizard so the corrected credit lines flow through. See README / the
# v45.0 deploy notes.
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: no historical collections to realign.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    # Guard: model + field must exist (skip gracefully if schema differs).
    if 'delivery.return.history' not in env:
        _logger.info("hksf_rental v45: delivery.return.history model absent, "
                     "skipping FIFO re-allocation.")
        return

    Picking = env['stock.picking']
    collections = Picking.search([
        ('picking_type_code', '=', 'incoming'),
        ('state', '=', 'done'),
    ])
    if not collections:
        _logger.info("hksf_rental v45: no done collection pickings to realign.")
        return

    realigned = 0
    for picking in collections:
        try:
            picking._resync_collection_histories()
            realigned += 1
        except Exception:  # pragma: no cover - never block the upgrade
            _logger.exception(
                "hksf_rental v45: FIFO re-allocation failed for picking %s "
                "(id=%s); leaving its histories unchanged.",
                picking.name, picking.id,
            )

    _logger.info(
        "hksf_rental v45: re-ran date-ordered FIFO resync on %s of %s done "
        "collection pickings. NOTE: previously generated invoices must be "
        "regenerated to pick up corrected credit quantities.",
        realigned, len(collections),
    )
