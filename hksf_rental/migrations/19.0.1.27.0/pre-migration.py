# -*- coding: utf-8 -*-
"""Rename sale_order.r_and_d_pricelist_id -> repair_pricelist_id.

Problem
-------
Earlier versions declared a Many2one ``r_and_d_pricelist_id`` ("R&D Pricelist")
on ``sale.order`` that was shown in the form but never actually used to drive
any pricing. In v19.0.1.27.0 the field is repurposed and renamed to
``repair_pricelist_id`` and now actually stamps each line's Repair Price.

Root cause / why a pre-migration
--------------------------------
A plain Python field rename does NOT rename the underlying Postgres column.
Without this script Odoo would (a) leave the old ``r_and_d_pricelist_id``
column orphaned and (b) create a fresh, empty ``repair_pricelist_id`` column,
silently losing whatever pricelist an order already referenced. Renaming the
column at the SQL layer in pre-migration preserves existing data.

Idempotent: only renames when the old column exists and the new one does not.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Does the old column exist?
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sale_order'
          AND column_name = 'r_and_d_pricelist_id'
    """)
    old_exists = bool(cr.fetchone())

    # Does the new column already exist?
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sale_order'
          AND column_name = 'repair_pricelist_id'
    """)
    new_exists = bool(cr.fetchone())

    if old_exists and not new_exists:
        cr.execute(
            "ALTER TABLE sale_order "
            "RENAME COLUMN r_and_d_pricelist_id TO repair_pricelist_id"
        )
        _logger.info(
            "hksf_rental 1.27.0: renamed sale_order.r_and_d_pricelist_id "
            "-> repair_pricelist_id (data preserved)."
        )
    elif old_exists and new_exists:
        # Both present (unexpected): copy non-null old values into the new
        # column where the new one is empty, then drop the old column.
        cr.execute(
            "UPDATE sale_order SET repair_pricelist_id = r_and_d_pricelist_id "
            "WHERE repair_pricelist_id IS NULL "
            "AND r_and_d_pricelist_id IS NOT NULL"
        )
        cr.execute("ALTER TABLE sale_order DROP COLUMN r_and_d_pricelist_id")
        _logger.info(
            "hksf_rental 1.27.0: both columns present -- copied old values "
            "into repair_pricelist_id and dropped r_and_d_pricelist_id."
        )
    else:
        _logger.info(
            "hksf_rental 1.27.0: nothing to do for repair_pricelist_id rename "
            "(old_exists=%s, new_exists=%s).",
            old_exists, new_exists,
        )
    # Always patch any stale stored view arch so view validation during this
    # same upgrade never sees a dangling reference to the renamed field.
    _patch_stale_views(cr)


def _patch_stale_views(cr):
    """Rewrite any stored ir_ui_view arch that still references the old field
    name. The module's XML data will overwrite these views with the new arch
    later in the same load, but Odoo validates *existing* (DB) view archs
    against the freshly-loaded model before that overwrite -- and the old field
    no longer exists on the model, so validation would explode. Swapping the
    name in the stored arch keeps every intermediate state consistent.
    Idempotent: a no-op once the views already use the new name.
    """
    cr.execute("""
        UPDATE ir_ui_view
        SET arch_db = replace(arch_db::text,
                              'r_and_d_pricelist_id',
                              'repair_pricelist_id')::jsonb
        WHERE arch_db::text LIKE '%r_and_d_pricelist_id%'
    """)
    if cr.rowcount:
        _logger.info(
            "hksf_rental 1.27.0: patched %s stored view(s) referencing the old "
            "r_and_d_pricelist_id field name.", cr.rowcount,
        )
