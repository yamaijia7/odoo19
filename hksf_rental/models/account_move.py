# -*- coding: utf-8 -*-
from decimal import Decimal
from odoo import models, fields, api


class AccountMove(models.Model):
    """Extend account.move with rental invoicing header fields.

    Header fields relocated from hksf_rental_invoice, plus the charge_type /
    minimum_charge_method fields written by the delivery invoice wizard.
    Line-level billing fields live on account.move.line below.
    """
    _inherit = 'account.move'

    rental_subject_line = fields.Text(
        string='Rental Subject',
        copy=False,
        readonly=False,
    )
    rental_sale_id = fields.Many2one(
        'sale.order',
        string='Source Sale Order',
        copy=False,
        readonly=True,
    )
    rental_salesperson_id = fields.Many2one(
        'res.users',
        string='Salesperson (Rental)',
        copy=False,
    )
    rental_invoice_type = fields.Selection(
        selection=[
            ('rent', 'Rental'),
            ('sale', 'Sale'),
            ('repair', 'Repair'),
            ('damage', 'Damage'),
            ('lost', 'Lost Material'),
        ],
        string='Invoice Type',
        copy=False,
    )
    rental_charge_type = fields.Selection(
        selection=[('monthly', 'Monthly'), ('weekly', 'Weekly')],
        string='Charge Type',
        copy=False,
    )
    # Written by hksf.delivery.invoice.wizard._prepare_invoice_vals
    charge_type = fields.Selection(
        selection=[('monthly', 'Monthly'), ('weekly', 'Weekly')],
        string='Rental Charge Type',
        copy=False,
    )
    minimum_charge_method = fields.Selection(
        selection=[
            ('normal', 'Normal'),
            ('first_charge', 'Charge First'),
        ],
        string='Minimum Charge Method',
        copy=False,
    )
    # Manual invoice mode (ported from Odoo 11 manual_customer_invoice)
    is_manual_invoice = fields.Boolean(
        string='Manual Invoice',
        copy=False,
    )

    # ------------------------------------------------------------------
    # Print helpers (ported from Odoo 11 odoo_delivery_invoice_report)
    # ------------------------------------------------------------------
    def change_quantity_format(self, quantity):
        """Render a quantity without trailing zeros (e.g. 12.00 -> 12)."""
        return "%g" % (Decimal(round(quantity or 0.0, 2)))

    def print_cutom_invoice(self):
        """Group invoice lines into the buckets used by the material-rent
        reports. Mirrors the Odoo 11 helper, adapted to account.move(.line).

          1 = Balance Brought From Last Month/Week
          2 = Rental (outgoing deliveries)
          3 = Deduction (incoming collections)
          4 = Transportation Charges
          5 = Lost Products (no picking)
        """
        new_val_dict = {1: {}, 2: {}, 3: {}, 4: {}, 5: {}}

        report_print_type = self._context.get('report_print_type', 'detail_rent')
        AML = self.env['account.move.line'].sudo()

        for rec in self:
            inv_lines = rec.invoice_line_ids.filtered(
                lambda l: l.display_type in (
                    'product', 'cogs',
                    'non_deductible_product', 'non_deductible_product_total',
                )
            )
            if report_print_type == 'material_rent':
                inv_lines = inv_lines.filtered(lambda l: l.price_total != 0.0)

            for line in inv_lines:
                picking = line.picking_id
                code = picking.picking_type_code if picking else False
                if line.is_from_previous_month:                          # Balance brought forward
                    new_val_dict[1].setdefault('previous_month', AML)
                    new_val_dict[1]['previous_month'] += line
                elif line.is_transport_product:                          # Transport
                    new_val_dict[4].setdefault(picking, AML)
                    new_val_dict[4][picking] += line
                elif picking and code == 'outgoing' and line.price_subtotal >= 0.0:  # Rental
                    new_val_dict[2].setdefault(picking, AML)
                    new_val_dict[2][picking] += line
                elif picking and code == 'incoming':                     # Deduction
                    new_val_dict[3].setdefault(picking, AML)
                    new_val_dict[3][picking] += line
                elif not picking:                                        # Lost products
                    new_val_dict[5].setdefault(line.days, AML)
                    new_val_dict[5][line.days] += line

        # Sort pickings by scheduled date, lines by product name
        for seq in new_val_dict:
            new_val_dict[seq] = dict(sorted(
                new_val_dict[seq].items(),
                key=lambda x: (
                    getattr(x[0], 'scheduled_date_only', None) or ''
                    if not isinstance(x[0], (str, int)) else ''
                ),
            ))
            for k in new_val_dict[seq]:
                new_val_dict[seq][k] = new_val_dict[seq][k].sorted(
                    key=lambda r: r.product_id.name or ''
                )
        return {'new_val_dict': new_val_dict}


class AccountMoveLine(models.Model):
    """Extend invoice lines with delivery-specific billing fields."""
    _inherit = 'account.move.line'

    # ------------------------------------------------------------------
    # Billing period and rental rate fields
    # ------------------------------------------------------------------

    start_date = fields.Date(
        string='Start Date',
        copy=False,
    )
    end_date = fields.Date(
        string='End Date',
        copy=False,
    )
    days = fields.Integer(
        string='Days',
        default=0,
        copy=False,
    )
    custom_price_unit = fields.Float(
        string='Monthly Rate',
        digits='Product Price',
        readonly=True,
        copy=False,
        help="Full monthly/weekly rate before pro-rata calculation.",
    )
    monthly_amount = fields.Float(
        string='Period Amount',
        digits='Product Price',
        compute='_compute_monthly_amount',
        store=True,
        help="quantity × monthly rate",
    )
    minimum_charge_days = fields.Float(
        string='Minimum Charge Days',
        digits='Product Unit of Measure',
        default=0.0,
        copy=False,
    )
    # Manual invoice mode: Manual Invoice Month = days / 30
    custom_month = fields.Float(
        string='Manual Invoice Month',
        compute='_compute_custom_month',
        store=True,
        copy=False,
    )

    # ------------------------------------------------------------------
    # Classification / linking fields
    # ------------------------------------------------------------------

    is_transport_product = fields.Boolean(
        string='Is Transport Line',
        default=False,
        copy=False,
    )
    is_from_previous_month = fields.Boolean(
        string='From Previous Month',
        default=False,
        copy=False,
    )
    picking_id = fields.Many2one(
        'stock.picking',
        string='Delivery / Collection',
        copy=False,
        index=True,
    )
    custom_sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Sale Order Line',
        copy=False,
        index=True,
    )
    custom_move_id = fields.Many2one(
        'stock.move',
        string='Stock Move',
        copy=False,
        index=True,
    )
    delivery_history_ids = fields.Many2many(
        'delivery.return.history',
        'account_move_line_delivery_history_rel',
        'line_id',
        'history_id',
        string='Return History',
        copy=False,
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('quantity', 'custom_price_unit')
    def _compute_monthly_amount(self):
        for line in self:
            line.monthly_amount = line.quantity * line.custom_price_unit

    @api.depends('days')
    def _compute_custom_month(self):
        """Manual Invoice Month = days / 30 (Odoo 11 manual_customer_invoice)."""
        for line in self:
            line.custom_month = (line.days / 30.0) if line.days else 0.0

    # ------------------------------------------------------------------
    # Manual invoice price override (ported from Odoo 11
    # account.invoice.line._compute_price)
    # ------------------------------------------------------------------
    @api.depends('quantity', 'discount', 'price_unit', 'tax_ids', 'currency_id',
                 'custom_month', 'days', 'move_id.is_manual_invoice')
    def _compute_totals(self):
        """When the invoice is in Manual Invoice mode, price the line as
        price_unit × (1 - discount%) × custom_month (days / 30) instead of
        the standard quantity × price_unit. All other lines fall back to the
        standard Odoo 19 computation.
        """
        AccountTax = self.env['account.tax']
        manual_lines = self.filtered(
            lambda l: l.move_id.is_manual_invoice and l.days > 0
            and l.display_type in ('product', 'cogs',
                                   'non_deductible_product',
                                   'non_deductible_product_total')
            and l.move_id
        )
        # Standard computation for everything else
        super(AccountMoveLine, self - manual_lines)._compute_totals()

        for line in manual_lines:
            company = line.company_id or self.env.company
            price = line.price_unit * (1.0 - (line.discount or 0.0) / 100.0) * line.custom_month
            base_line = line.move_id._prepare_product_base_line_for_taxes_computation(line)
            # Override the unit price fed into the tax engine with the
            # manual-invoice price; keep quantity = 1 so the engine does not
            # multiply custom_month by quantity twice.
            base_line['price_unit'] = price
            base_line['quantity'] = line.quantity
            base_line['discount'] = 0.0
            AccountTax._add_tax_details_in_base_line(base_line, company)
            AccountTax._round_base_lines_tax_details([base_line], company)
            line.price_subtotal = base_line['tax_details']['total_excluded_currency']
            line.price_total = base_line['tax_details']['total_included_currency']

    @api.onchange('start_date', 'end_date')
    def _onchange_days(self):
        """Recompute Days from Start/End dates in Manual Invoice mode."""
        for rec in self:
            if rec.move_id.is_manual_invoice and rec.start_date and rec.end_date:
                custom_days = (rec.end_date - rec.start_date).days + 1
                if custom_days > 0:
                    rec.days = custom_days

    # ------------------------------------------------------------------
    # Override create — write back invoice line reference on history records
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            if not line.delivery_history_ids:
                continue
            picking = line.picking_id
            if picking and picking.picking_type_code == 'incoming':
                # collection line → mark as return invoice line
                line.delivery_history_ids.write({
                    'return_invoice_line_id': line.id,
                })
            elif picking and picking.picking_type_code == 'outgoing':
                # delivery line → mark as delivery invoice line
                line.delivery_history_ids.write({
                    'deliver_invoice_line_id': line.id,
                })
        return lines
