# HKSF Rental — Upgrade Runbook (Odoo 19 → future major versions)

This module currently runs cleanly on **Odoo 19** (version `19.0.1.27.9`).
This document records the known forward-looking risks for the *next* major
version jump (19 → 20 → 21 …), so that whoever performs the upgrade has a
ready checklist. **None of these are bugs on Odoo 19** — they are items that
core Odoo is expected to change in a future release.

> Context: H.K. Scafframe Systems (Hong Kong scaffolding rental). **Hong Kong
> has no sales tax**, so this deployment never carries tax records. Several
> "tax" items below are therefore low-impact for this specific deployment, but
> are documented for completeness.

---

## 0. Always do first (every upgrade)

1. **Back up before anything**: full Postgres dump (`pg_dump -Fc`) **and** the
   filestore. Never run a destructive migration without both.
2. Upgrade on a **copy** of production, not production itself.
3. Read the target version's **OpenUpgrade** notes and the official "Changes"
   list for the modules this one inherits (`sale`, `stock`, `account`,
   `product`).
4. Run the bundled regression suite after upgrade (see section 6).

---

## 1. Tax computation — `compute_all()` (LOW impact for HK, but watch)

- **Where:** `models/sale_order_line.py`, method `_hksf_compute_taxes()`.
- **What:** As of v27.9 every tax/total computation goes through this **single
  helper** (it wraps `tax_ids.compute_all(...)`). Previously there were two
  separate call sites.
- **Risk:** Odoo is migrating tax math away from `account.tax.compute_all` to a
  new tax engine (`_prepare_base_line_for_taxes_computation` already exists in
  v19). `compute_all` may be removed or change signature in a future version.
- **Fix when it happens:** edit **only** `_hksf_compute_taxes()` — one method,
  one place. Keep the same return keys (`total_excluded`, `total_included`,
  `taxes`) so the two callers in `_compute_amount` keep working.
- **HK note:** with no taxes, `compute_all` is a pass-through (returns the bare
  amount). The math result will not change; only the *method name* is at risk.

---

## 2. UoM field rename — `stock.move.product_uom` (MEDIUM, near-certain)

- **Where:** `wizard/delivery_invoice_wizard.py` (≈4 references to
  `move.product_uom.id`).
- **State in v19 (verified):** `stock.move.product_uom` EXISTS;
  `stock.move.product_uom_id` does NOT. Meanwhile `sale.order.line` and
  `account.move.line` already use `product_uom_id`. Core is mid-rename.
- **Risk:** a future version will likely rename `stock.move.product_uom` →
  `product_uom_id`, matching the other models.
- **Fix when it happens:** find/replace `move.product_uom` → `move.product_uom_id`
  in the wizard, and re-verify the invoice-line creation. Confirm the new field
  name against the target version's `stock.move` model before editing.

---

## 3. QWeb `t-esc` → `t-out` (DONE in v27.9)

- All report `t-esc` were replaced with `t-out` in v27.9
  (`report/report_sale_rental.xml`, `report/report_delivery_invoice.xml`).
- Behaviour is identical (both HTML-escape). No further action needed unless a
  future version deprecates `t-out` too (no signal of that today).

---

## 4. Core method overrides — re-test after every upgrade (MEDIUM)

These methods call into core and are the ones most exposed to signature drift.
They are all written defensively (call `super()`), but **re-run the regression
suite** after upgrade and read the target version's changelog for each:

| Model | Override | Notes |
|---|---|---|
| `sale.order.line` | `_compute_amount` | rental month multiplier; depends on `_hksf_compute_taxes` (§1) |
| `stock.picking` | `button_validate` | requires `scheduled_date_only`; signature has changed historically |
| `account.move` / `account.move.line` | `_compute_*` | invoice-line restructures happen across versions |
| `sale.order` | `create` / `write` / `action_confirm` | |
| `stock.move` | `write` / `unlink` | |

---

## 5. Data-layer items (LOW, single-company deployment)

1. **`delivery.return.history` invoice-line links** (`deliver_invoice_line_id`,
   `return_invoice_line_id`) have **no `ondelete`** (default `set null`). Safe,
   but if a future OpenUpgrade re-keys `account.move` lines these could go null.
   *Mitigation:* after upgrade, re-validate history link integrity and re-run
   the FIFO back-fill logic (same as migrations `1.13.0`/`1.14.0`).
2. **No `company_id`** on `collection.repair.damage`, `product.outstanding`,
   `delivery.return.history`. Inert while single-company. If you ever go
   multi-company, add a stored `company_id` (derive from `order_id`/`picking_id`)
   in a pre-migration so the column exists before any core multi-company change.
3. **Selection keys stored as strings** (`line_type`, `truck_size_selection`,
   `body_font_size`). If you ever rename a selection *key* (not just its label),
   rename the stored DB value in a `pre-migration.py` — a plain Python change
   leaves stale values that fail validation.

---

## 6. Post-upgrade validation checklist

Run from the Odoo root (`PYTHONPATH=<odoo_root>`), DB user `odoo`:

- [ ] Module upgrades with **no errors**:
      `python3 odoo-bin -d <db> -u hksf_rental --stop-after-init --no-http
      --db_user=odoo --db_password=odoo`
- [ ] Regression suite passes (all 5 in the module root):
      `test_merged_sync_overqty.py`, `test_pricelist_stamp_lost_outstanding.py`,
      `test_rental_income_account.py`, `test_repair_damage_rd_invoice.py`,
      `test_shared_batch_two_collections.py`
- [ ] Order PDF renders, header readable (white-on-black), rows compact.
- [ ] Delivery/Invoice PDF renders.
- [ ] Create a rental order → confirm → delivery → collection (validate) →
      rental invoice → lost-material invoice routes to **Lost Material Income
      Account** → repair/damage invoice.
- [ ] Outstanding page: Unit Price shows 2 decimals.

---

## 7. What is intentionally NOT a risk

- **No JavaScript / OWL / `static/` assets** — the single biggest cross-version
  breakage source does not apply.
- **No deprecated decorators** (`@api.multi/one/cr`), no `osv.osv`, no
  `fields.function`, no `_columns`.
- **No raw `cr.execute`** in runtime code (only idempotent SQL in
  `pre-migration.py`, which is the correct place).
- **No `<tree>` / `attrs=` / `states=`** legacy view syntax.
- **No `mail.thread`/chatter** customization.
- **Migration discipline already in place** — column renames + stored-compute
  back-fills, all idempotent.
