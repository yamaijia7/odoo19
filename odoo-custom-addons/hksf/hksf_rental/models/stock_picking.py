# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import pytz
from datetime import datetime


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # ------------------------------------------------------------------
    # Fields from odoo_delivery_collection
    # ------------------------------------------------------------------

    scheduled_date_only = fields.Date(
        string='Scheduled Date (Date Only)',
        index=True,
        copy=False,
        help="Date-only version of the scheduled datetime (in user timezone). "
             "Required before validation. Used for invoice date range comparisons.",
    )
    license_plate = fields.Char(
        string='License Plate',
        copy=False,
    )
    truck_size_selection = fields.Selection(
        selection=[
            ('5_ton', '5 Ton'),
            ('10_ton', '10 Ton'),
            ('20_ton', '20 Ton'),
            ('flatbed', 'Flatbed'),
            ('other', 'Other'),
        ],
        string='Truck Size',
    )
    time_in = fields.Datetime(
        string='Time In',
        copy=False,
    )
    time_out = fields.Datetime(
        string='Time Out',
        copy=False,
    )
    custom_employee_id = fields.Many2one(
        'hr.employee',
        string='Driver / Employee',
        copy=False,
    )
    delivery_type = fields.Selection(
        selection=[
            ('standard', 'Standard'),
            ('urgent', 'Urgent'),
            ('partial', 'Partial'),
        ],
        string='Delivery Type',
        default='standard',
    )

    # collection.repair.damage lines on picking
    collection_repair_ids = fields.One2many(
        'collection.repair.damage',
        'collection_picking_id',
        string='Repair / Damage Records',
        copy=False,
    )

    # ------------------------------------------------------------------
    # Fields from odoo_delivery_invoice
    # ------------------------------------------------------------------

    invoice_id = fields.Many2one(
        'account.move',
        string='Invoice',
        copy=False,
        domain=[('move_type', '=', 'out_invoice')],
    )
    last_invoice_date = fields.Date(
        string='Last Invoice Date',
        copy=False,
    )
    transport_charge_ids = fields.One2many(
        'transport.charge',
        'picking_id',
        string='Transport Charges',
        copy=False,
    )
    transportation_method = fields.Selection(
        selection=[
            ('by_us', 'By Us'),
            ('by_customer', 'By Customer'),
        ],
        string='Transportation Method',
        default='by_customer',
    )
    site_contact_ids = fields.Many2many(
        'res.partner',
        'stock_picking_site_contact_rel',
        'picking_id',
        'partner_id',
        string='Site Contacts',
    )
    partner_parent_id = fields.Many2one(
        'res.partner',
        string='Parent Customer',
        compute='_compute_partner_parent_id',
        store=True,
    )
    is_from_previous_month = fields.Boolean(
        string='From Previous Month',
        default=False,
        copy=False,
    )
    original_return_picking_id = fields.Many2one(
        'stock.picking',
        string='Original Return Picking',
        copy=False,
    )
    new_return_picking_id = fields.Many2one(
        'stock.picking',
        string='New Return Picking',
        copy=False,
    )
    custom_sale_order_id = fields.Many2one(
        'sale.order',
        string='Custom Sale Order',
        copy=False,
        index=True,
        help="Explicitly linked sale order (used for collection pickings not "
             "created through normal stock routing).",
    )
    is_create_transport_invoice = fields.Boolean(
        string='Transport Invoice Created',
        default=False,
        copy=False,
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('partner_id', 'partner_id.parent_id')
    def _compute_partner_parent_id(self):
        for rec in self:
            rec.partner_parent_id = rec.partner_id.parent_id or rec.partner_id

    # ------------------------------------------------------------------
    # Onchange: auto-fill scheduled_date_only from scheduled_date
    # ------------------------------------------------------------------

    @api.onchange('scheduled_date')
    def _onchange_scheduled_date(self):
        """Convert scheduled datetime to a date-only value using user timezone."""
        for rec in self:
            if rec.scheduled_date:
                user_tz = self.env.user.tz or 'UTC'
                try:
                    tz = pytz.timezone(user_tz)
                    local_dt = rec.scheduled_date.astimezone(tz)
                    rec.scheduled_date_only = local_dt.date()
                except Exception:
                    rec.scheduled_date_only = rec.scheduled_date.date()

    # ------------------------------------------------------------------
    # Override button_validate — require scheduled_date_only
    # ------------------------------------------------------------------

    def button_validate(self):
        for rec in self:
            if not rec.scheduled_date_only:
                raise UserError(
                    _("Please set the Scheduled Date on picking '%s' before validating.")
                    % rec.name
                )
        res = super().button_validate()
        # After a collection (incoming) picking is validated, re-sync its
        # delivery.return.history rows to the ACTUAL collected quantity.
        for rec in self:
            if rec.picking_type_code == 'incoming' and rec.state == 'done':
                rec._resync_collection_histories()
                rec._generate_repair_damage_lines()
        return res

    def action_sync_collection(self):
        """ONE button on a validated (done) collection picking that re-syncs
        EVERYTHING derived from the collection, in the right order:

          1. ``_resync_collection_histories`` -> rebuilds the rental return
             credit (delivery.return.history) to match the corrected collected
             ``move.quantity``. Needed after you edit a Done qty (e.g. validated
             at 20, actually 19), because histories are otherwise only rebuilt
             inside ``button_validate``.
          2. ``_generate_repair_damage_lines`` -> (re)creates the
             collection.repair.damage lines from the inline Repair / Damage
             columns (repair -> repair_price, damage -> lost_price). Needed when
             the columns are filled in / edited after validation.

        NOTE on stock: the stock ledger itself is corrected by CORE Odoo the
        moment you edit the *Done* quantity on the move line of a done picking
        (stock.move.line.write re-posts the quant). Edit the qty FIRST in
        Detailed Operations, THEN press this button to realign credit + R&D.

        Both steps are idempotent -- safe to press repeatedly. If any move has
        Repair + Damage greater than the collected qty, a non-blocking warning
        is shown (the data is still saved, per design).
        """
        done_incoming = self.filtered(
            lambda p: p.picking_type_code == 'incoming' and p.state == 'done'
        )
        if not done_incoming:
            raise UserError(_(
                "A collection can only be synced once it is validated (done)."
            ))
        done_incoming._resync_collection_histories()
        done_incoming._generate_repair_damage_lines()
        warn = done_incoming._collect_overqty_warnings()
        if warn:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Collection synced (with warnings)"),
                    'message': warn,
                    'type': 'warning',
                    'sticky': True,
                },
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Collection synced"),
                'message': _("Rental credit and Repair/Damage lines are up to date."),
                'type': 'success',
                'sticky': False,
            },
        }

    # Backward-compat aliases: any old stored button reference keeps working
    # and now performs the full combined sync.
    def action_sync_repair_damage(self):
        return self.action_sync_collection()

    def action_sync_collection_qty(self):
        return self.action_sync_collection()

    def _collect_overqty_warnings(self):
        """Return a human-readable warning string (or empty) listing any move
        where Repair + Damage exceeds the collected (Done) quantity. Combined
        comparison per the user's decision; soft / non-blocking.
        """
        msgs = []
        for rec in self:
            for move in rec.move_ids:
                rd = (move.repair_qty or 0.0) + (move.damage_qty or 0.0)
                collected = move.quantity or 0.0
                if rd > collected + 0.0001:
                    msgs.append(_(
                        "%(prod)s: Repair (%(r)s) + Damage (%(d)s) = %(sum)s "
                        "exceeds collected %(c)s."
                    ) % {
                        'prod': move.product_id.display_name,
                        'r': move.repair_qty or 0.0,
                        'd': move.damage_qty or 0.0,
                        'sum': rd,
                        'c': collected,
                    })
        return "\n".join(msgs)

    def _generate_repair_damage_lines(self):
        """Turn inline repair_qty / damage_qty on collection moves into
        collection.repair.damage lines.

        Per the user's pricing model:
          - repair -> sale-line repair_price (own price)
          - damage -> sale-line lost_price   (damage = lost, beyond repair)
        Lines are kept INDEPENDENT of the rental return credit (no qty cap,
        no exclusion from rental) by explicit design decision.
        Idempotent: existing un-invoiced auto lines for a move+type are updated
        in place so re-validating / editing doesn't create duplicates.
        """
        RD = self.env['collection.repair.damage'].sudo()
        for rec in self:
            order = rec.custom_sale_order_id
            for move in rec.move_ids:
                if not move.product_id:
                    continue
                sale_line = move.sale_line_id or (
                    order.order_line.filtered(lambda l: l.product_id == move.product_id)[:1]
                    if order else False
                )
                specs = [
                    ('repair', move.repair_qty,
                     sale_line.repair_price if sale_line else move.product_id.lst_price),
                    ('damage', move.damage_qty,
                     sale_line.lost_price if sale_line else move.product_id.lst_price),
                ]
                for rd_type, qty, price in specs:
                    existing = RD.search([
                        ('move_id', '=', move.id),
                        ('type', '=', rd_type),
                        ('collection_picking_id', '=', rec.id),
                    ], limit=1)
                    if qty and qty > 0.0:
                        vals = {
                            'product_id': move.product_id.id,
                            'internal_ref': '%s %s' % (move.product_id.name, rec.name),
                            'qty': qty,
                            'uom_id': move.product_id.uom_id.id,
                            'price_unit': price or 0.0,
                            'type': rd_type,
                            'move_id': move.id,
                            'collection_picking_id': rec.id,
                            'order_id': order.id if order else False,
                        }
                        if existing and not existing.invoice_id:
                            existing.write(vals)
                        elif not existing:
                            RD.create(vals)
                    elif existing and not existing.invoice_id:
                        # qty cleared back to 0 -> remove the stale auto line
                        existing.with_context(force_unlink=True).unlink()
        return True

    def _resync_collection_histories(self):
        """Re-distribute return histories to match the quantity actually
        collected on this incoming picking.

        The Create-Collection wizard pre-allocates delivery.return.history rows
        for the FULL outstanding quantity at wizard time. If the user then edits
        the picking to collect only a partial quantity before validating, those
        histories still carry the full delivered_qty (e.g. 35 + 109 = 144),
        which makes the delivery invoice credit/charge 144 units instead of the
        46 actually collected. This re-syncs return_qty FIFO across the linked
        delivery moves so the histories always equal the validated qty -- the
        same contract Odoo 11 enforced via the return-move-select wizard.
        """
        History = self.env['delivery.return.history'].sudo()
        for move in self.move_ids.filtered(lambda m: m.state == 'done'):
            collected = move.quantity
            histories = move.delivery_return_history_ids.sorted(
                lambda h: (h.deliver_move_id.date or h.deliver_move_id.create_date,
                           h.deliver_move_id.id)
            )
            if not histories:
                continue
            remaining = collected
            for hist in histories:
                # Capacity left on this delivery move from OTHER collections.
                other_returned = sum(
                    h.return_qty for h in hist.deliver_move_id.delivery_return_history_ids
                    if h.return_move_id != move
                )
                capacity = max(hist.deliver_move_id.quantity - other_returned, 0.0)
                alloc = min(capacity, remaining) if remaining > 0.0 else 0.0
                if hist.return_qty != alloc or hist.delivered_qty != hist.deliver_move_id.quantity:
                    hist.write({
                        'return_qty': alloc,
                        'delivered_qty': hist.deliver_move_id.quantity,
                    })
                remaining -= alloc
            # Drop any zero-qty histories left over (fewer deliveries needed
            # than were pre-allocated) so they don't clutter the invoice.
            histories.filtered(lambda h: h.return_qty <= 0.0).unlink()
