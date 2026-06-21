# -*- coding: utf-8 -*-
# v19.0.1.69.0
# ---------------------------------------------------------------------------
# Order-line "Invoiced" mirror reactivity hardening.
#
# What changed in the code
# ------------------------
# sale.order.line.service_invoiced_qty / service_to_invoice_qty are stored
# computes that mirror the linked Service Lines' billing progress. Their
# @api.depends previously stopped at service_charge_ids.invoiced_quantity
# (itself a stored compute). On a DIRECT invoice-line quantity edit, or an
# invoice create / cancel, that left a retrigger gap so the order-line mirror
# could go stale. The depends now traverses all the way to
# service_charge_ids.invoice_line_ids.quantity and .move_id.state.
#
# Why a migration is needed
# -------------------------
# No new column is added, but the recompute trigger graph changed, so any
# historical row whose mirror had drifted (the exact bug being fixed) must be
# recomputed once on upgrade so stored values are guaranteed correct
# immediately. We also refresh service.charge.invoiced_quantity / remaining_qty
# first so the mirror reads correct source values. Idempotent: a pure recompute
# over current data.
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: nothing historical to recompute.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    # Refresh the service-charge source fields first.
    charges = env['service.charge'].search([])
    if charges:
        charges.modified(['invoice_line_ids', 'quantity'])
        charges._compute_invoiced_quantity()
        charges._compute_remaining_qty()
        _logger.info(
            "hksf_rental v69: refreshed invoiced_quantity / remaining_qty for "
            "%s service line(s).", len(charges),
        )

    # Recompute the order-line mirror so it reflects the linked Service Lines.
    SOL = env['sale.order.line']
    lines = SOL.search([('service_charge_ids', '!=', False)])
    if lines:
        lines._compute_service_invoiced_qty()
        _logger.info(
            "hksf_rental v69: recomputed service_invoiced_qty / "
            "service_to_invoice_qty mirror for %s order line(s).", len(lines),
        )
