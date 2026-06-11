# -*- coding: utf-8 -*-
"""Rename the rental income-account fields (v19.0.1.27.2).

  product_template.property_custom_sale_income_product_id
      -> property_rental_income_account_id
  product_category.property_custom_sale_income_categ_id
      -> property_rental_income_account_categ_id

Why a pre-migration
-------------------
In Odoo 19 a company_dependent Many2one is stored as a real JSONB column on the
model's own table (not ir_property like older versions). A plain Python field
rename therefore leaves the old JSONB column orphaned and creates a new empty
one, silently losing any per-company account a user had configured. Renaming
the column at SQL level preserves the stored {company_id: account_id} mapping.

Each rename is idempotent: it only runs when the old column exists and the new
one does not.
"""
import logging

_logger = logging.getLogger(__name__)

RENAMES = [
    ('product_template',
     'property_custom_sale_income_product_id',
     'property_rental_income_account_id'),
    ('product_category',
     'property_custom_sale_income_categ_id',
     'property_rental_income_account_categ_id'),
]


def _col_exists(cr, table, col):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, col))
    return bool(cr.fetchone())


def _patch_stale_views(cr, old, new):
    """Swap the old field name for the new one in any stored ir_ui_view arch.
    Odoo validates existing (DB) view archs against the freshly-loaded model
    BEFORE the module's XML overwrites them; with the field renamed, the old
    arch references a field that no longer exists and validation explodes.
    Idempotent."""
    cr.execute("""
        UPDATE ir_ui_view
        SET arch_db = replace(arch_db::text, %s, %s)::jsonb
        WHERE arch_db::text LIKE %s
    """, (old, new, '%' + old + '%'))
    if cr.rowcount:
        _logger.info(
            "hksf_rental 1.27.2: patched %s stored view(s) referencing %s.",
            cr.rowcount, old,
        )


def migrate(cr, version):
    for table, old, new in RENAMES:
        old_exists = _col_exists(cr, table, old)
        new_exists = _col_exists(cr, table, new)
        if old_exists and not new_exists:
            cr.execute(
                'ALTER TABLE "%s" RENAME COLUMN "%s" TO "%s"' % (table, old, new)
            )
            _logger.info(
                "hksf_rental 1.27.2: renamed %s.%s -> %s (per-company values "
                "preserved).", table, old, new,
            )
        elif old_exists and new_exists:
            # Both present (unexpected): keep non-null old values where the new
            # column is null, then drop the old column.
            cr.execute(
                'UPDATE "%s" SET "%s" = "%s" '
                'WHERE "%s" IS NULL AND "%s" IS NOT NULL'
                % (table, new, old, new, old)
            )
            cr.execute('ALTER TABLE "%s" DROP COLUMN "%s"' % (table, old))
            _logger.info(
                "hksf_rental 1.27.2: merged %s.%s into %s and dropped the old "
                "column.", table, old, new,
            )
        else:
            _logger.info(
                "hksf_rental 1.27.2: nothing to do for %s.%s -> %s "
                "(old_exists=%s, new_exists=%s).",
                table, old, new, old_exists, new_exists,
            )
        # Always patch stored view archs so view validation in this same
        # upgrade never sees a dangling reference to the renamed field.
        _patch_stale_views(cr, old, new)
