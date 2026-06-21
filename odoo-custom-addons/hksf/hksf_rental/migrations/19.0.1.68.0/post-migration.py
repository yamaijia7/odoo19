# -*- coding: utf-8 -*-
# v19.0.1.68.0
# ---------------------------------------------------------------------------
# Service Lines (formerly "Service Charges") partial-quantity billing.
#
# What changed in the code
# ------------------------
# service.charge now tracks billing progress by QUANTITY instead of an
# all-or-nothing invoice_id stamp:
#   * invoiced_quantity  = sum of qty on linked, non-cancelled invoice lines
#                          (account.move.line.service_charge_id == charge)
#   * remaining_qty      = quantity - invoiced_quantity
#   * is_billed          = now means FULLY billed (invoiced_quantity >= quantity)
# and sale.order.line mirrors the linked Service Lines' progress via
# service_invoiced_qty / service_to_invoice_qty.
#
# Why a migration is needed
# -------------------------
# These are new STORED computed fields and is_billed's meaning changed, so the
# existing rows must be recomputed. Odoo recomputes new stored computes on
# upgrade, but we force it here so the values are guaranteed correct
# immediately (and so is_billed flips from the old "has an invoice" semantics
# to the new "fully billed" semantics for every historical row).
#
# Back-fill correctness
# ---------------------
# The OLD model always billed the FULL charge quantity in one shot and stamped
# invoice_id, so each historical service charge already has invoice line(s)
# carrying its full qty. The new compute reads exactly those lines, so
# invoiced_quantity back-fills to the full qty -> remaining_qty 0 ->
# is_billed True, matching the prior behaviour. Charges whose invoice was
# cancelled have their lines excluded, so they correctly re-open as billable.
# Idempotent: a pure recompute over current data.
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: nothing historical to recompute.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    charges = env['service.charge'].search([])
    if charges:
        # Recompute the new quantity-based progress fields + redefined is_billed.
        charges.modified(['invoice_line_ids', 'quantity'])
        charges._compute_invoiced_quantity()
        charges._compute_remaining_qty()
        charges._compute_is_billed()
        _logger.info(
            "hksf_rental v68: recomputed invoiced_quantity / remaining_qty / "
            "is_billed for %s service line(s).", len(charges),
        )

    # Mirror onto linked order lines.
    SOL = env['sale.order.line']
    lines = SOL.search([('service_charge_ids', '!=', False)])
    if lines:
        lines._compute_service_invoiced_qty()
        _logger.info(
            "hksf_rental v68: recomputed service_invoiced_qty mirror for %s "
            "order line(s) with linked Service Lines.", len(lines),
        )
