# -*- coding: utf-8 -*-
"""
service_charge.py
=================
Erection / dismantling (and other) SERVICE billing lines attached to a
sale.order.

H.K. Scafframe rents scaffolding materials AND performs the erection /
dismantling labour for it. After a crew finishes an erection or dismantling
job, the charge for that service is entered here on the sale order. The rental
invoice wizard then picks up every unbilled service charge and adds it to the
rental invoice as a NATIVE account.move.line (income posts through the service
product's own income account), so the journal is always built by core Odoo
from a single set of real lines -- there is no second / hidden line and no
manual journal-item injection.

Design notes (vs. the Odoo 11 `sale.service.invoice.line` approach):
  - The Odoo 11 method materialised each service line TWICE into the ledger
    (a hidden real account.invoice.line in odoo_account_extend PLUS a manual
    `invoice_line_move_line_get` `type:'src'` move line in odoo_delivery_invoice),
    which double-counted service revenue / unbalanced the move. This model
    deliberately produces exactly ONE native move line per charge, the same
    way `transport.charge` already works in this module.
  - `invoice_id` is stamped once billed and the wizard skips already-billed
    charges, so re-running the wizard never re-bills the same service. If the
    invoice is cancelled, the stamp is cleared (see account_move.py) so the
    charge becomes billable again.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ServiceCharge(models.Model):
    _name = 'service.charge'
    _description = 'Service Charge Line (Erection / Dismantling)'
    _order = 'sequence, id'

    sequence = fields.Integer(string='Sequence', default=10)
    # Mirrors native sale.order.line: lets the Service Charges tab offer
    # "Add a section" / "Add a note" controls. Section/Note rows are pure
    # display rows -- they carry no product/qty/price, are never billed, and
    # are rendered bold (section) / italic (note) on the quotation, exactly
    # like native order lines.
    display_type = fields.Selection(
        selection=[
            ('line_section', "Section"),
            ('line_note', "Note"),
        ],
        default=False,
        help="Technical field for UI structure: section header or note row.",
    )
    order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Related Sale Line',
        domain="[('order_id', '=', order_id), ('display_type', '=', False), "
               "('product_id.type', '=', 'service')]",
        copy=False,
        help="Optional: link this billing row to a SERVICE line already quoted "
             "on the Order Lines tab (e.g. an erection or dismantling charge). "
             "Pulls its description, price and quantity for billing.",
    )
    product_id = fields.Many2one(
        'product.product',
        string='Service Product',
        domain=[('sale_ok', '=', True), ('type', '=', 'service')],
    )
    name = fields.Text(string='Description', required=True)
    quantity = fields.Float(
        string='Quantity',
        digits='Product Unit of Measure',
        default=1.0,
    )
    product_uom = fields.Many2one('uom.uom', string='Unit of Measure')
    price_unit = fields.Float(
        string='Unit Price',
        digits='Product Price',
        default=0.0,
        help="Manual price per service job.",
    )
    price_subtotal = fields.Monetary(
        string='Subtotal',
        compute='_compute_amount',
        store=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='order_id.currency_id',
        string='Currency',
        store=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        'res.company',
        related='order_id.company_id',
        string='Company',
        store=True,
    )
    is_create_service_invoice = fields.Boolean(
        string='Create Service Invoice',
        default=True,
        copy=False,
        help="Tick to include this service charge the next time you run "
             "Create Delivery Invoice. Untick to keep it on the order without "
             "billing it yet. Mirrors the Transport Charges flag.",
    )
    invoice_id = fields.Many2one(
        'account.move',
        string='Invoice',
        copy=False,
        readonly=True,
        index=True,
        help="Most recent invoice this service line was billed on. Kept for "
             "the journal-routing / back-reference; partial-billing progress "
             "is tracked by Invoiced Qty (see invoice_line_ids).",
    )
    invoice_line_ids = fields.One2many(
        'account.move.line',
        'service_charge_id',
        string='Invoice Lines',
        copy=False,
        readonly=True,
        help="Every invoice line billed from this service line (across one or "
             "more rental invoices). Their summed quantity is the Invoiced Qty.",
    )
    invoiced_quantity = fields.Float(
        string='Invoiced Qty',
        digits='Product Unit of Measure',
        compute='_compute_invoiced_quantity',
        store=True,
        help="How much of this service line has already been billed (sum of "
             "the linked, non-cancelled invoice-line quantities). If you edit "
             "the qty on a generated invoice line, this follows it, so the "
             "next rental invoice bills only the remaining quantity.",
    )
    remaining_qty = fields.Float(
        string='To Invoice Qty',
        digits='Product Unit of Measure',
        compute='_compute_remaining_qty',
        store=True,
        help="Quantity still to bill = Quantity - Invoiced Qty. The rental "
             "invoice wizard bills this remainder on its next run.",
    )
    is_billed = fields.Boolean(
        string='Billed?',
        compute='_compute_is_billed',
        store=True,
        help="Automatically ticked once this charge is FULLY billed "
             "(remaining qty <= 0). Partially-billed charges stay un-ticked so "
             "the wizard keeps billing the remainder.",
    )

    @api.depends('invoice_line_ids', 'invoice_line_ids.quantity',
                 'invoice_line_ids.move_id.state', 'display_type')
    def _compute_invoiced_quantity(self):
        for line in self:
            if line.display_type:
                line.invoiced_quantity = 0.0
                continue
            billed = line.invoice_line_ids.filtered(
                lambda l: l.move_id.state != 'cancel'
                and l.move_id.move_type == 'out_invoice'
            )
            line.invoiced_quantity = sum(billed.mapped('quantity'))

    @api.depends('quantity', 'invoiced_quantity', 'display_type')
    def _compute_remaining_qty(self):
        for line in self:
            if line.display_type:
                line.remaining_qty = 0.0
                continue
            rem = line.quantity - line.invoiced_quantity
            line.remaining_qty = rem if rem > 0.0 else 0.0

    @api.depends('quantity', 'invoiced_quantity', 'display_type')
    def _compute_is_billed(self):
        for line in self:
            # Section / note rows are never billed.
            if line.display_type:
                line.is_billed = False
                continue
            # Fully billed once the invoiced qty has caught up to the quantity.
            line.is_billed = bool(
                line.quantity and line.invoiced_quantity >= line.quantity
            )

    # Hong Kong: no sales tax. Subtotal = total. No tax field by design,
    # consistent with the rest of the module.
    @api.depends('quantity', 'price_unit', 'display_type')
    def _compute_amount(self):
        for line in self:
            # Section / note rows have no monetary amount.
            if line.display_type:
                line.price_subtotal = 0.0
                continue
            line.price_subtotal = line.price_unit * line.quantity

    @api.constrains('display_type', 'product_id')
    def _check_product_required(self):
        """Real service rows must have a product; section/note rows must not."""
        for line in self:
            if not line.display_type and not line.product_id:
                raise UserError(_(
                    "Each service charge line must have a Service Product "
                    "(unless it is a Section or Note row)."
                ))

    @api.onchange('product_id')
    def product_id_change(self):
        if self.display_type or not self.product_id:
            return
        self.product_uom = self.product_id.uom_id
        if not self.quantity:
            self.quantity = 1.0
        if not self.price_unit:
            self.price_unit = self.product_id.lst_price
        name = self.product_id.display_name
        if self.product_id.description_sale:
            name += '\n' + self.product_id.description_sale
        if not self.name:
            self.name = name

    @api.onchange('sale_line_id')
    def _onchange_sale_line_id(self):
        """Derive a service charge from a quotation (Order) line.

        Workflow: Order Lines = the quotation; Service Charges = the service
        (erection / dismantling) billing layer. Picking the Related Sale Line
        pulls description + price (+ product when the quoted product is itself
        a service product) so the user does not re-type them.

        Odoo-logic safety: billing posts through the service product's income
        account, and `_check_product_required` enforces product_id is a
        service product. So we only copy product_id when the quoted line's
        product is `type == 'service'`; for rental / non-service lines we copy
        description + price only and leave product_id for the user to pick,
        keeping the service income account correct. Existing values are never
        overwritten (only blanks are filled).
        """
        line = self.sale_line_id
        if self.display_type or not line:
            return
        # Description: prefer the quotation line's text.
        if line.name:
            self.name = line.name
        # Price: copy the quoted unit price as a starting point.
        if not self.price_unit:
            self.price_unit = line.price_unit
        # Quantity / UoM: take the quoted quantity as the starting point.
        # `quantity` defaults to 1.0 on new rows, so treat 0 or the untouched
        # default 1.0 as "not yet set by the user" and copy the quoted qty.
        if line.product_uom_qty and self.quantity in (0.0, 1.0):
            self.quantity = line.product_uom_qty
        if not self.product_uom and line.product_uom_id:
            self.product_uom = line.product_uom_id
        # Product: only adopt it when it is a real service product, otherwise
        # adopting it would break billing (wrong income account) and trip the
        # service-product constraint. Warn instead of silently breaking.
        prod = line.product_id
        if prod and prod.type == 'service':
            if not self.product_id:
                self.product_id = prod
        elif prod and not self.product_id:
            return {
                'warning': {
                    'title': _("Pick a service product"),
                    'message': _(
                        "Description and price were copied from the quotation "
                        "line, but '%s' is not a service product, so it was not "
                        "set as the Service Product. Choose a service product "
                        "so the charge bills to the correct income account."
                    ) % prod.display_name,
                }
            }

    def unlink(self):
        for line in self:
            if line.invoice_id and line.invoice_id.state != 'cancel':
                raise UserError(_(
                    "You cannot delete a service charge that has already been "
                    "invoiced (%s). Cancel/reverse the invoice first."
                ) % line.invoice_id.name)
        return super().unlink()
