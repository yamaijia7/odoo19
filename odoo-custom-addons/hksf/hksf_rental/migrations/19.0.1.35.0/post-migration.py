# -*- coding: utf-8 -*-
# v19.0.1.35.0
# Migrate the old hard-coded truck_size_selection (Selection) values on
# stock.picking to the new editable hksf.truck.size master-data model
# (field truck_size_id). Runs after the new model + seed records are loaded.
import logging

_logger = logging.getLogger(__name__)

# Old selection key -> display label (matches the seeded hksf.truck.size names)
OLD_MAP = {
    '5_ton': '5 Ton',
    '10_ton': '10 Ton',
    '20_ton': '20 Ton',
    'flatbed': 'Flatbed',
    'other': 'Other',
}


def migrate(cr, version):
    # The legacy column may already be gone (e.g. fresh install); guard for it.
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'stock_picking'
          AND column_name = 'truck_size_selection'
    """)
    if not cr.fetchone():
        _logger.info("hksf_rental: no legacy truck_size_selection column; skipping.")
        return

    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    TruckSize = env['hksf.truck.size']

    # Cache name -> id, creating any missing size on the fly.
    cache = {}
    for key, label in OLD_MAP.items():
        rec = TruckSize.search([('name', '=', label)], limit=1)
        if not rec:
            rec = TruckSize.create({'name': label})
        cache[key] = rec.id

    cr.execute("""
        SELECT id, truck_size_selection
        FROM stock_picking
        WHERE truck_size_selection IS NOT NULL
          AND truck_size_id IS NULL
    """)
    rows = cr.fetchall()
    migrated = 0
    for pid, sel in rows:
        size_id = cache.get(sel)
        if size_id:
            cr.execute(
                "UPDATE stock_picking SET truck_size_id = %s WHERE id = %s",
                (size_id, pid),
            )
            migrated += 1
    _logger.info("hksf_rental: migrated %s pickings to truck_size_id.", migrated)

    # Drop the now-orphaned legacy column so it doesn't linger.
    cr.execute("ALTER TABLE stock_picking DROP COLUMN IF EXISTS truck_size_selection")
    _logger.info("hksf_rental: dropped legacy truck_size_selection column.")
