# -*- coding: utf-8 -*-
# v19.0.1.40.0
# The short-lived v39.0 approach added a custom field
# account.move.line.custom_prorate_price to round prorated subtotals via a
# custom compute. v40.0 replaces that with the cleaner NATIVE path (full-
# precision price_unit + min_display_digits=5 display widening), so the field
# is no longer part of the model. Drop the orphaned column if it exists.
#
# Guarded with IF EXISTS so it is safe on databases that never saw v39.0
# (e.g. production, which upgrades straight from an earlier version). Idempotent.
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'account_move_line'
          AND column_name = 'custom_prorate_price'
    """)
    if cr.fetchone():
        cr.execute(
            "ALTER TABLE account_move_line DROP COLUMN IF EXISTS custom_prorate_price"
        )
        _logger.info("hksf_rental: dropped orphaned column "
                     "account_move_line.custom_prorate_price (v39 leftover).")
    else:
        _logger.info("hksf_rental: custom_prorate_price column absent, "
                     "nothing to drop.")
