# -*- coding: utf-8 -*-
"""Back-fill the service-line invoiced/to-invoice mirror for ALL service lines.

Why
---
The wizard's service-line billing is now authoritative (it recomputes the
remaining qty live), so this migration is not required for billing to work.
But the stored mirror fields ``service_invoiced_qty`` / ``service_to_invoice_qty``
also drive the "Invoiced" / "To Invoice" COLUMNS on the Order Lines list, and
the 19.0.1.70.0 recompute only touched lines that already had linked service
invoice lines (``service_invoice_line_ids != False``). A never-billed service
line (e.g. S00006's checked but unbilled "Erection") was therefore left with a
stale 0 in those columns. Recompute the mirror for every service-type order
line so the UI reflects the true outstanding qty.

Idempotent: a pure recompute over current data.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    # All non-section service-type order lines (the only ones the mirror is
    # meaningful for). product_id.type == 'service' is the eligibility test used
    # everywhere in the module.
    lines = env['sale.order.line'].search([
        ('display_type', '=', False),
        ('product_id.type', '=', 'service'),
    ])
    if lines:
        lines.invalidate_recordset(
            ['service_invoiced_qty', 'service_to_invoice_qty'])
        lines._compute_service_invoiced_qty()
        lines.flush_recordset(
            ['service_invoiced_qty', 'service_to_invoice_qty'])
        _logger.info(
            "hksf_rental v72: back-filled service_invoiced_qty / "
            "service_to_invoice_qty for %s service order line(s).", len(lines),
        )
    else:
        _logger.info(
            "hksf_rental v72: no service order lines to back-fill.")
