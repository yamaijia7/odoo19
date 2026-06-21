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

## 6b. Lost = Return behaviour (v19.0.1.28.2)

Creating a **lost-material invoice** now stops the lost qty from being
rent-charged, treating it exactly like stock returned to us on the
lost-invoice date (per user spec: "lost is similar to them buying it off as to
cut off rental").

- `sale_order._create_lost_return_picking()` auto-creates ONE validated
  **incoming** picking (`is_lost_return=True`) for the invoiced lost lines,
  dated on `invoice.invoice_date`, and FIFO-links it to the originating done
  delivery moves via `delivery.return.history` (per-move `return_qty` capped by
  remaining delivered-minus-already-returned capacity, so a delivery move can
  never be over-returned).
- The existing rental-billing credit machinery then nets the lost qty out of
  rent **pro-rata to the lost-invoice date** (separate negative collection
  credit line — not a reduced positive qty).
- **Partial lost** is fully supported: invoice e.g. 50 of 100 as lost; rent
  continues on the remaining 50, the 50 lost units stop accruing from the lost
  date. Q1 (continue rental after a lost invoice) and Q2 (partial lost lowers
  upkeep) both verified.
- Runtime-verified (`/home/user/workspace/verify_2820.py`,
  `debug_lostcredit.py`, `debug_C.py`): partial-lost mid-period → net rent
  reduced; full-lost at period start → net rent 0; two 50-lost runs → exactly
  100 returned (no over-return). All 5 regression tests still pass.

---

## 6c. Reverse Lost / resume rental (v19.0.1.28.3)

Use when a client requested a lost "cut-off" that was **never paid** and rental
must continue. Reverses the lost invoice from 6b and **resumes rent from the
lost-invoice date forward** — one click, no manual stock surgery.

**Why a button is needed:** the lost invoice (`account.move`) and the
lost-return picking are *not* linked. Deleting or cancelling the invoice on its
own does **nothing** to the return picking, so rent stays stopped. You also
cannot `action_cancel()` a done stock move (Odoo raises *"You cannot cancel a
stock move that has been set to 'Done'. Create a return…"*). Hence a dedicated,
Odoo-safe reverse.

- Button **"Reverse Lost & Resume Rental"** (`btn-warning`, with confirm dialog)
  on the lost invoice form header; visible only when
  `rental_invoice_type == 'lost'` and `state != 'cancel'`.
- `account_move.action_reverse_lost_resume_rental(reverse_stock=True)`:
  1. **Severs the custom `delivery.return.history` links** for the done
     lost-return picking (`force_unlink=True` context) — this alone resumes rent
     because `stock_move._compute_new_return_quantity` only counts histories
     whose `return_move_id.state == 'done'`. **Core stock is untouched** by this
     step (odoo logic preserved).
  2. If `reverse_stock=True` (button default), reverses the physical stock via
     the **standard `stock.return.picking` wizard** (`action_create_returns()` →
     validate), creating an *outgoing* picking that ships the units back out to
     the client. The reverse picking is flagged `is_lost_return=False` so it is
     not itself treated as a rental return. The original done incoming move is
     **kept**, never force-cancelled — quants/valuation stay consistent.
  3. Releases the lost lines (`invoice_id=False`) and recomputes outstanding.
  4. Cancels the lost invoice via the **normal flow** (`button_draft` →
     `button_cancel`) — invoice is *cancelled, not deleted*, so the audit trail
     and sequence remain intact.
- Runtime-verified (`/home/user/workspace/verify_reverse2.py`) for both
  `reverse_stock=True` and `False`: lost invoice ends cancelled (not deleted),
  original done return move kept, reverse outgoing picking created+done when
  requested, and **June rent resumes at the full 100 units**. All 5 regression
  tests still pass.
- **Re-lost after a reverse (v19.0.1.28.4):** the reverse now also untags the
  old lost-return picking (`is_lost_return=False`, `custom_sale_order_id`/move
  `custom_sale_id` cleared) and *deletes* the released `collection.repair.damage`
  lost line, so a NEW lost invoice can be raised later with no stale pickings or
  duplicate lost lines. Verified (`/home/user/workspace/verify_relost.py`):
  lost → reverse → rent resumes full → second future lost invoice builds from
  exactly one clean line and stops rent again. (A future lost invoice is created
  the normal way — the **Outstanding Products** page, which rebuilds the lost
  lines from the live delivered−collected balance.)

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

---

## 8. Core-safety audit (v19.0.1.28.5)

Full pass over every model/wizard for code that could desync Odoo core state:

- **Stock state writes:** the only raw `state='done'` force-writes were a
  defensive fallback in `sale_order._create_lost_return_picking`. Although the
  normal path never hit them (`button_validate()` returns `True` and quants move
  correctly — runtime-verified, on-hand +100), a forced `state='done'` would
  mark a move done *without moving stock* if ever reached. **Fixed:** the
  fallback now completes via the core `move_ids._action_done()` primitive (the
  same routine `button_validate` calls), so quants + valuation are always handled
  by Odoo. No raw stock `state` write remains in runtime code. `stock.move._action_cancel`
  is correctly overridden to clean the module's own histories then `super()`.
- **No quant / valuation tampering:** zero references to `stock.quant` or
  `stock.valuation.layer`; the module never writes them directly.
- **Core overrides all call `super()`** and only post-process the module's OWN
  custom relations (`tag_ids`, `delivery.return.history`,
  `collection.repair.damage`). `stock.move.write` guards re-entrancy with a
  context flag.
- **`account.move` writes are safe:** the two `invoice.unlink()` calls delete a
  *freshly-created zero-line DRAFT* invoice (the "no lines generated" discard) —
  never a posted move. The lost-reverse cancels via the normal
  `button_draft -> button_cancel` flow (cancelled, not deleted).
- **All `.unlink()` calls target custom models only** (histories / R&D lines /
  transport charges) — no core record is force-deleted.
- **Raw SQL only in `migrations/`**, operating on the module's own columns/views.

Verdict: after the one fallback fix, the module contains **no code path that
forces core stock/accounting state out of sync.** verify_reverse2, verify_relost
and all 5 regression tests pass; lost-return still moves quants correctly.

---

## ROLLBACK — remove service charges (v19.0.1.31.0)

This build **rolls the module back to the pre-service-charge baseline**
(the v19.0.1.28.5 codebase) so the feature can be rebuilt from scratch. The
erection/dismantling **service charge** feature (added v19.0.1.29.0, extended
through v19.0.1.30.1) is fully removed from the code, AND a pre-migration
script cleans every DB artifact it left behind.

- **Version number is a trigger, not the code line.** The manifest says
  `19.0.1.31.0` only so Odoo runs the cleanup migration when upgrading from a
  deployed 19.0.1.30.x. The actual code is the v28.5 baseline (no service
  charges). Odoo runs a migration when
  `installed_version < migration_folder <= manifest_version`; deployed 30.1
  upgrading to 31.0 satisfies this, so `migrations/19.0.1.31.0/pre-migration.py`
  fires.
- **What the cleanup removes** (all guarded / idempotent):
  - Blanks any stored `sale.order` / `service.charge` view referencing the
    removed fields (`service_type`, `service_charge_ids`) so the sale order
    form stops throwing the OWL "field is undefined" error, then Odoo rebuilds
    those views from the rollback XML in the same upgrade.
  - Blanks the custom QWeb quotation report view that carried the service
    print block (rebuilt from rollback XML).
  - Deletes `ir.model.data`, `ir.model.access`, `ir.model.fields`, and the
    `ir.model` row for `service.charge`.
  - Drops orphaned columns: `res_company.hksf_service_journal_id`,
    `account_move_line.is_service_product`, `account_move_line.service_charge_id`.
  - Drops the `service_charge` table.
  - **Leaves the native `product.template.service_type` field untouched** (it
    is a core Odoo product field, NOT part of the removed feature).
- **Why a migration (not just old code):** Odoo never auto-drops tables,
  columns, or stored views when a model/field disappears from code. Without the
  migration the DB would keep orphaned `service_charge` artifacts and the stale
  sale-order view would still break the form (the OWL error seen in prod).
- **Verified on Odoo 19:** upgraded a DB carrying the full v30.1 service-charge
  state to this build — migration ran clean, `service.charge` model/table/
  columns/views all gone, sale order form renders and records read OK, native
  product service_type preserved, and all 5 regression tests pass.
