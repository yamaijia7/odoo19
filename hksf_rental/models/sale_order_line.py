# -*- coding: utf-8 -*-
from datetime import date
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api


class SaleOrderLine(models.Model):
    """
    Extends sale.order.line with rental-specific fields and overrides the
    amount computation.

    Calculation methods
    -------------------

    1. month  (duration in months)
    --------------------------------
    Triggered by _onchange_start_end_date whenever the user sets start_date
    or end_date on a line.

        delta = relativedelta(end_date, start_date)
        fraction = delta.days / 30.0   # days remainder → fractional months
        month   = delta.months + fraction

    Example:  01-Jan → 20-Feb  →  delta.months = 1, delta.days = 20
              month = 1 + (20 / 30) = 1.667

    2. subtotal_weight
    ------------------
        subtotal_weight = product_uom_qty × weight

    3. subtotal_volume
    ------------------
        subtotal_volume = product_uom_qty × volume

    4. _compute_amount  (rental price subtotal)
    -------------------------------------------
    Overrides the standard Odoo _compute_amount when the parent order is in
    rental mode (custom_sale_type == 'rent') AND month > 0.

    Standard Odoo formula:
        price_subtotal = price_unit × product_uom_qty  (after discount, taxes)

    Rental override formula:
        effective_unit = price_unit × (1 − discount/100) × month
        taxes_result   = tax_ids.compute_all(
                            effective_unit,
                            currency       = order.currency_id,
                            quantity       = product_uom_qty,
                            product        = product_id,
                            partner        = partner_shipping_id,
                         )
        price_subtotal = taxes_result['total_excluded']
        price_tax      = Σ taxes_result['taxes'][*]['amount']
        price_total    = taxes_result['total_included']

    In plain terms:
        price_subtotal = price_unit × (1 − discount%) × months × qty   (excl. tax)
        price_total    = price_subtotal + taxes

    For sale lines (line_type == 'sale' or custom_sale_type == 'sale')
    the standard Odoo formula is used unchanged.
    """

    _inherit = 'sale.order.line'

    # ------------------------------------------------------------------
    # Rental period
    # ------------------------------------------------------------------
    start_date = fields.Date(string='Start Date')
    end_date = fields.Date(string='End Date')
    month = fields.Float(
        string='Months',
        default=1.0,
        digits=(16, 4),
        help=(
            "Rental duration in months. Automatically calculated from "
            "start/end date. Fractional months are expressed as days/30."
        ),
    )

    # ------------------------------------------------------------------
    # Manual invoice mode (ported from Odoo 11 manual_customer_invoice)
    # ------------------------------------------------------------------
    wizard_days = fields.Integer(
        string='Day from popup',
        copy=False,
        readonly=True,
        help="Days entered via the Select Days popup. Overrides the "
             "Start/End date calculation when set.",
    )
    custom_days = fields.Integer(
        string="Day's",
        compute='_compute_custom_days',
        store=True,
        help="Billable days = (End Date - Start Date) + 1, or the value "
             "entered through the Select Days popup.",
    )
    custom_month = fields.Float(
        string='Manual Invoice Month',
        compute='_compute_custom_month',
        store=True,
        help="custom_days / 30 — the fractional month used to price the line "
             "when the order is in Manual Invoice mode.",
    )

    # ------------------------------------------------------------------
    # Line classification
    # ------------------------------------------------------------------
    line_type = fields.Selection(
        selection=[('rental', 'Rental'), ('sale', 'Sale')],
        string='Line Type',
        default='rental',
        copy=False,
    )

    # ------------------------------------------------------------------
    # Repair / lost pricing (from hksf_delivery_invoice)
    # ------------------------------------------------------------------
    repair_price = fields.Float(
        string='Repair Price',
        digits='Product Price',
        default=0.0,
        copy=False,
    )
    lost_price = fields.Float(
        string='Lost Price',
        digits='Product Price',
        default=0.0,
        copy=False,
    )
    product_internal_reference = fields.Char(
        string='Internal Reference',
        related='product_id.default_code',
        store=True,
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Weight / volume
    # ------------------------------------------------------------------
    weight = fields.Float(string='Unit Weight (kg)')
    subtotal_weight = fields.Float(
        string='Subtotal Weight (kg)',
        compute='_compute_subtotal_weight',
        store=True,
    )
    volume = fields.Float(string='Unit Volume (m³)')
    subtotal_volume = fields.Float(
        string='Subtotal Volume (m³)',
        compute='_compute_subtotal_volume',
        store=True,
    )

    # ------------------------------------------------------------------
    # Weight / volume computations
    # ------------------------------------------------------------------
    @api.depends('start_date', 'end_date', 'wizard_days')
    def _compute_custom_days(self):
        """Day's = wizard_days (if set) else (end_date - start_date) + 1."""
        for rec in self:
            if rec.wizard_days:
                rec.custom_days = rec.wizard_days
            elif rec.start_date and rec.end_date:
                custom_days = (rec.end_date - rec.start_date).days + 1
                rec.custom_days = custom_days if custom_days > 0 else 0
            else:
                rec.custom_days = 0

    @api.depends('custom_days')
    def _compute_custom_month(self):
        """Manual Invoice Month = custom_days / 30."""
        for rec in self:
            rec.custom_month = (rec.custom_days / 30.0) if rec.custom_days else 0.0

    @api.depends('weight', 'product_uom_qty')
    def _compute_subtotal_weight(self):
        """subtotal_weight = product_uom_qty × weight"""
        for line in self:
            line.subtotal_weight = line.product_uom_qty * line.weight

    @api.depends('volume', 'product_uom_qty')
    def _compute_subtotal_volume(self):
        """subtotal_volume = product_uom_qty × volume"""
        for line in self:
            line.subtotal_volume = line.product_uom_qty * line.volume

    # ------------------------------------------------------------------
    # Duration onchange
    # ------------------------------------------------------------------
    @api.onchange('start_date', 'end_date')
    def _onchange_start_end_date(self):
        """
        Auto-compute rental months from date range.

        month = delta.months + (delta.days / 30.0)

        where delta = relativedelta(end_date, start_date).
        """
        for line in self:
            if line.start_date and line.end_date:
                if line.end_date < line.start_date:
                    line.month = 0.0
                    continue
                delta = relativedelta(line.end_date, line.start_date)
                fractional = delta.days / 30.0
                line.month = delta.months + fractional
            else:
                line.month = 0.0

    # ------------------------------------------------------------------
    # Tax computation helper
    # ------------------------------------------------------------------
    def _hksf_compute_taxes(self, price):
        """Single choke-point for tax/total computation on a rental line.

        Behaviour is IDENTICAL to calling ``tax_ids.compute_all(...)`` directly
        (and is a no-op pass-through when there are no taxes, e.g. Hong Kong
        where sales are untaxed). It exists purely as an upgrade-insulation
        layer: Odoo is gradually migrating tax math from ``compute_all`` to a
        new tax engine, so keeping the single call here means a future version
        bump is a ONE-METHOD change instead of editing every call site.

        :param price: the per-unit price already adjusted for discount and the
                      rental month multiplier.
        :return: the dict returned by ``account.tax.compute_all`` -- keys
                 ``total_excluded``, ``total_included`` and ``taxes``.
        """
        self.ensure_one()
        return self.tax_ids.compute_all(
            price,
            currency=self.order_id.currency_id,
            quantity=self.product_uom_qty,
            product=self.product_id,
            partner=self.order_id.partner_shipping_id,
        )

    # ------------------------------------------------------------------
    # Amount computation override
    # ------------------------------------------------------------------
    @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_ids', 'month',
                 'custom_month', 'order_id.is_manual_invoice')
    def _compute_amount(self):
        """
        Override standard amount computation to include the rental duration
        multiplier for lines belonging to a rental order.

        Rental formula (when order.custom_sale_type == 'rent' and month > 0):
            effective_unit_price = price_unit × (1 − discount/100) × month
            tax_result = taxes.compute_all(
                            effective_unit_price,
                            currency=order.currency_id,
                            quantity=product_uom_qty,
                            ...)
            price_subtotal = tax_result['total_excluded']
            price_total    = tax_result['total_included']
            price_tax      = price_total − price_subtotal

        For non-rental or month=0 lines the standard Odoo computation
        (price_unit × qty after discount and taxes) is used.
        """
        # Run the standard computation first for all lines
        super()._compute_amount()

        for line in self:
            # Manual Invoice mode takes precedence (Odoo 11 manual_customer_invoice)
            # price = price_unit × (1 - discount%) × custom_month
            if line.order_id.is_manual_invoice and line.custom_days > 0:
                price = line.price_unit * (1.0 - (line.discount or 0.0) / 100.0) * line.custom_month
                taxes = line._hksf_compute_taxes(price)
                line.update({
                    'price_tax': sum(t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                    'price_total': taxes['total_included'],
                    'price_subtotal': taxes['total_excluded'],
                })
                continue
            # Standard rental month override
            if (
                line.order_id.custom_sale_type == 'rent'
                and line.month > 0
            ):
                price = line.price_unit * (1.0 - (line.discount or 0.0) / 100.0) * line.month
                taxes = line._hksf_compute_taxes(price)
                line.update({
                    'price_tax': sum(t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                    'price_total': taxes['total_included'],
                    'price_subtotal': taxes['total_excluded'],
                })

    # ------------------------------------------------------------------
    # Product change
    # ------------------------------------------------------------------
    @api.onchange('product_id')
    def _onchange_product_id_hksf(self):
        """Copy weight/volume from the product and set the rental line_type.

        Note: in Odoo 19 sale.order.line no longer has a `product_id_change`
        onchange method to call via super(); product defaults are applied
        through computed fields. This is a standalone onchange that only adds
        the HKSF-specific behaviour on top.
        """
        if self.product_id:
            self.weight = self.product_id.weight
            self.volume = self.product_id.volume
            # Set line_type from product default only on rental orders
            if self.order_id.custom_sale_type != 'sale':
                self.line_type = self.product_id.line_type or 'rental'
            else:
                self.line_type = 'sale'
