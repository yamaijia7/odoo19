# -*- coding: utf-8 -*-
# v19.0.1.70.0
# ---------------------------------------------------------------------------
# Service billing moved from the round-2 "Service Lines" page + "Pull
# Invoiceable Service Lines" button to a per-order-line "Bill on Next Invoice"
# checkbox.
#
# What changed in the code
# ------------------------
# sale.order.line.service_invoiced_qty / service_to_invoice_qty are still
# stored computes, but their SOURCE changed. Previously they summed the linked
# service.charge rows (service_charge_ids). They now sum the service invoice
# lines stamped with custom_sale_line_id (service_invoice_line_ids), i.e. the
# mirror reads account.move.line.quantity directly. A new boolean
# bill_on_next_invoice was added (default False, no back-fill needed).
#
# Why a migration is needed
# -------------------------
# The @api.depends graph for the two stored mirror fields was rewired, so any
# historical order line must be recomputed once on upgrade so the stored values
# match the new source (linked service invoice lines) immediately rather than
# only on the next write. Idempotent: a pure recompute over current data. The
# new bill_on_next_invoice column is created by the ORM with its False default;
# we intentionally do NOT tick it for historical lines (opt-in going forward).
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: nothing historical to recompute.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    # Recompute the order-line mirror from its NEW source (service invoice lines
    # linked via custom_sale_line_id). Limit to lines that actually have linked
    # service invoice lines -- those are the only ones whose mirror can be
    # non-zero. invalidate first so the recompute reads fresh One2many data.
    SOL = env['sale.order.line']
    lines = SOL.search([('service_invoice_line_ids', '!=', False)])
    if lines:
        lines.invalidate_recordset(
            ['service_invoiced_qty', 'service_to_invoice_qty'])
        lines._compute_service_invoiced_qty()
        lines.flush_recordset(
            ['service_invoiced_qty', 'service_to_invoice_qty'])
        _logger.info(
            "hksf_rental v70: recomputed service_invoiced_qty / "
            "service_to_invoice_qty mirror for %s order line(s) from the new "
            "custom_sale_line_id-linked invoice lines.", len(lines),
        )
    else:
        _logger.info(
            "hksf_rental v70: no order lines with linked service invoice "
            "lines; nothing to recompute.")
