# -*- coding: utf-8 -*-
# v19.0.1.38.0
# One-time backfill: tick "Apply Minimum Charge" (ia_apply_minimum_charge) on
# all EXISTING rental products (line_type = 'rental'). Going forward the field
# defaults to True for new/imported products; this aligns the existing rental
# catalogue. Pure-sale products (line_type = 'sale') are left untouched.
#
# Only flips records currently False -> True; never un-ticks a product the
# user has already set, and is safe to re-run (idempotent).
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Guard: both columns must exist on this DB.
    cr.execute("""
        SELECT count(*) FROM information_schema.columns
        WHERE table_name = 'product_template'
          AND column_name IN ('ia_apply_minimum_charge', 'line_type')
    """)
    if cr.fetchone()[0] < 2:
        _logger.info("hksf_rental: minimum-charge backfill skipped (columns missing).")
        return

    cr.execute("""
        UPDATE product_template
        SET ia_apply_minimum_charge = TRUE
        WHERE line_type = 'rental'
          AND COALESCE(ia_apply_minimum_charge, FALSE) = FALSE
    """)
    _logger.info(
        "hksf_rental: minimum-charge backfill set %s rental products to TRUE.",
        cr.rowcount,
    )
