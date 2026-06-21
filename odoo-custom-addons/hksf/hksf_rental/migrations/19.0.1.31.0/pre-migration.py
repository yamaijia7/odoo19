# -*- coding: utf-8 -*-
"""Pre-migration: ROLLBACK to the pre-service-charge baseline (v19.0.1.28.5
code shipped under version 19.0.1.31.0 as the trigger).

The service-charge feature (introduced v19.0.1.29.0, extended through
v19.0.1.30.1) added a `service.charge` model plus several columns and views.
The rollback code no longer defines any of these, so Odoo would leave them
behind as ORPHANS (Odoo never auto-drops tables, columns, or stored views).
This script removes every artifact the feature added, in dependency-safe
order, so the database returns cleanly to its pre-service-charge shape and the
sale order form stops referencing the dropped `service_type` field.

Everything is guarded (IF EXISTS / existence checks) so the script is
idempotent and safe to re-run. It only touches artifacts the feature created;
the native `product.template.service_type` field is left untouched.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install (no prior version) -> nothing to clean up.
        return

    _logger.info("HKSF rollback: removing service-charge artifacts (was %s)", version)

    # ------------------------------------------------------------------
    # 1. Stored views that reference the removed service.charge fields.
    #    Blank their arch so Odoo regenerates the inheriting views from the
    #    rollback module XML on this same upgrade. We target ONLY views whose
    #    stored arch references service.charge usage (the Service Charges tab
    #    embedded in the sale.order form, e.g. id 1453 in production), NOT the
    #    native product.template.service_type field (legitimate, must remain).
    #
    #    A sale.order / service.charge view that still carries a bare
    #    <field name="service_type"/> belongs to the removed feature; the
    #    native product field only appears on product.template views, which we
    #    explicitly exclude.
    # ------------------------------------------------------------------
    #    We blank any sale.order / service.charge view whose stored arch still
    #    references the removed feature fields (service_type, service_charge_ids)
    #    OR the Service Charges tab. The native product.template.service_type
    #    field lives only on product.template views, which we never touch.
    cr.execute(
        """
        UPDATE ir_ui_view
           SET arch_db = '{"en_US": "<data/>"}'::jsonb
         WHERE model IN ('sale.order', 'service.charge')
           AND (
                arch_db::text LIKE '%%service_type%%'
             OR arch_db::text LIKE '%%service_charge_ids%%'
             OR arch_db::text LIKE '%%service.charge%%'
           )
        """
    )
    _logger.info("HKSF rollback: blanked %s stale service-charge view(s)", cr.rowcount)

    #    The custom QWeb quotation report template (report_sale_rental_document)
    #    carried a service-charge print block in v30.x. Its view has no model
    #    (QWeb reports do). Blank any QWeb view that references service.charge so
    #    Odoo rebuilds it from the rollback report XML in this same upgrade.
    cr.execute(
        """
        UPDATE ir_ui_view
           SET arch_db = '{"en_US": "<data/>"}'::jsonb
         WHERE type = 'qweb'
           AND (model IS NULL OR model = '')
           AND arch_db::text LIKE '%%service.charge%%'
        """
    )
    if cr.rowcount:
        _logger.info("HKSF rollback: blanked %s service-charge QWeb view(s)", cr.rowcount)

    # Also drop any ir.ui.view rows whose model IS service.charge (the tab
    # list/form views defined by the removed feature) so they don't linger.
    cr.execute("DELETE FROM ir_ui_view WHERE model = 'service.charge'")
    if cr.rowcount:
        _logger.info("HKSF rollback: deleted %s service.charge view(s)", cr.rowcount)

    # ------------------------------------------------------------------
    # 2. ir.model.data xmlids that point at the removed feature's records
    #    (views, the access rule, the model, fields). Removing these prevents
    #    "External ID not found" noise and orphaned metadata.
    # ------------------------------------------------------------------
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'hksf_rental'
           AND (
                name LIKE '%%service_charge%%'
             OR name LIKE '%%service.charge%%'
             OR name = 'access_service_charge_user'
           )
        """
    )
    if cr.rowcount:
        _logger.info("HKSF rollback: deleted %s ir.model.data row(s)", cr.rowcount)

    # ------------------------------------------------------------------
    # 3. ACL row for service.charge.
    # ------------------------------------------------------------------
    cr.execute(
        """
        DELETE FROM ir_model_access
         WHERE model_id IN (
                   SELECT id FROM ir_model WHERE model = 'service.charge'
               )
        """
    )

    # ------------------------------------------------------------------
    # 4. Orphaned COLUMNS added by the feature to other tables.
    #    - res_company.hksf_service_journal_id  (Service Invoice Journal)
    #    - account_move_line.is_service_product
    #    - account_move_line.service_charge_id  (FK to service_charge)
    #    Drop the FK column before the service_charge table to avoid a
    #    dependency error.
    # ------------------------------------------------------------------
    cr.execute("ALTER TABLE IF EXISTS res_company "
               "DROP COLUMN IF EXISTS hksf_service_journal_id")
    cr.execute("ALTER TABLE IF EXISTS account_move_line "
               "DROP COLUMN IF EXISTS service_charge_id")
    cr.execute("ALTER TABLE IF EXISTS account_move_line "
               "DROP COLUMN IF EXISTS is_service_product")

    # ------------------------------------------------------------------
    # 5. ir.model.fields rows for the removed model AND for the orphaned
    #    fields on other models, so the registry doesn't try to reload them.
    # ------------------------------------------------------------------
    cr.execute(
        """
        DELETE FROM ir_model_fields
         WHERE model = 'service.charge'
            OR (model = 'res.company'      AND name = 'hksf_service_journal_id')
            OR (model = 'account.move.line' AND name IN ('is_service_product',
                                                         'service_charge_id'))
            OR (model = 'sale.order'        AND name = 'service_charge_ids')
        """
    )
    if cr.rowcount:
        _logger.info("HKSF rollback: deleted %s ir.model.fields row(s)", cr.rowcount)

    # ------------------------------------------------------------------
    # 6. The service.charge model itself: ir.model row + backing table.
    #    Drop the table last (after its FK references are gone).
    # ------------------------------------------------------------------
    cr.execute("DELETE FROM ir_model WHERE model = 'service.charge'")
    cr.execute("DROP TABLE IF EXISTS service_charge CASCADE")

    _logger.info("HKSF rollback: service-charge artifacts removed; "
                 "database returned to pre-service-charge baseline.")
