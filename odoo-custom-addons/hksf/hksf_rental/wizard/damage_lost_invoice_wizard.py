# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class DamageLostInvoiceWizard(models.TransientModel):
    """Creates an invoice for damage/lost material lines on a sale order."""
    _name = 'damage.lost.invoice.wizard'
    _description = 'Damage / Lost Invoice Wizard'

    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        required=True,
    )
    invoice_type = fields.Selection(
        selection=[
            ('repair_damage', 'Repair + Damage'),
        ],
        string='Invoice Type',
        required=True,
        default='repair_damage',
    )
    journal_id = fields.Many2one(
        'account.journal',
        string='Journal',
        domain=[('type', '=', 'sale')],
    )
    # Optional billing period: repair/damage is generally billed monthly, so the
    # user can restrict the pulled lines to collections whose date falls within
    # [date_from, date_to]. Both blank = pull everything uninvoiced (original
    # behaviour). Filtering is on the collection picking's date.
    date_from = fields.Date(string='From Date')
    date_to = fields.Date(string='To Date')
    line_ids = fields.One2many(
        'damage.lost.invoice.wizard.line',
        'wizard_id',
        string='Lines',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self._context.get('active_id')
        if active_id:
            res['sale_order_id'] = active_id
            order = self.env['sale.order'].browse(active_id)
            company = order.company_id or self.env.company
            # Combined Repair + Damage invoice -> repair/damage journal
            # (falls back to standard Sales when not configured).
            journal = company._hksf_journal_for('repair')
            if journal:
                res['journal_id'] = journal.id
        return res

    @api.onchange('sale_order_id', 'invoice_type', 'date_from', 'date_to')
    def _onchange_order(self):
        self.line_ids = [(5, 0, 0)]
        if not self.sale_order_id:
            return
        invoice_type = self.invoice_type or 'repair_damage'
        # 'repair_damage' loads BOTH repair and damage lines together so they
        # appear on one combined R&D invoice; the other options load a single
        # type. Account routing is per-line (see action_create_invoice).
        if invoice_type == 'repair_damage':
            wanted = ('repair', 'damage')
        else:
            wanted = (invoice_type,)
        date_from = self.date_from
        date_to = self.date_to

        def _in_period(line):
            """True when the line's collection date falls in [from, to].
            Lines with no resolvable collection date are INCLUDED (so manual
            lines / legacy data are never silently dropped)."""
            if not date_from and not date_to:
                return True
            picking = line.collection_picking_id
            ldate = (picking.scheduled_date_only or
                     (picking.date_done.date() if picking.date_done else False))
            if not ldate:
                return True
            if date_from and ldate < date_from:
                return False
            if date_to and ldate > date_to:
                return False
            return True

        # Partial-lost tracking: a line stays available until its cumulative
        # invoiced_qty reaches qty. We pull lines with remaining > 0 and
        # pre-fill the wizard qty with the REMAINING amount (not the full qty),
        # so repeated runs bill the outstanding balance over several invoices.
        uninvoiced = self.sale_order_id.collection_lost_material_ids.filtered(
            lambda l: (l.qty - (l.invoiced_qty or 0.0)) > 0.0
            and l.type in wanted and _in_period(l)
        )
        self.line_ids = [
            (0, 0, {
                'damage_line_id': l.id,
                'product_id': l.product_id.id,
                'line_type': l.type,
                'qty': l.qty - (l.invoiced_qty or 0.0),
                'price_unit': l.price_unit,
                'name': l.internal_ref or l.product_id.name,
                'include': True,
            }) for l in uninvoiced
        ]

    def action_create_invoice(self):
        self.ensure_one()
        lines_to_invoice = self.line_ids.filtered(lambda l: l.include)
        if not lines_to_invoice:
            raise UserError(_("No lines selected to invoice."))

        # Overbill guard: never invoice more than the remaining (qty -
        # invoiced_qty) on any source line. Protects against partial-tracking
        # drift / accidental over-entry.
        for wline in lines_to_invoice:
            src = wline.damage_line_id
            if src:
                remaining = src.qty - (src.invoiced_qty or 0.0)
                if wline.qty > remaining + 1e-6:
                    raise UserError(_(
                        "Cannot invoice %(want)s of '%(name)s' - only "
                        "%(rem)s remaining (total %(total)s, already "
                        "invoiced %(done)s).",
                        want=wline.qty,
                        name=wline.name or (src.product_id.display_name),
                        rem=remaining,
                        total=src.qty,
                        done=src.invoiced_qty or 0.0,
                    ))

        order = self.sale_order_id
        # Combined invoices are stamped 'damage' (the R&D bucket) for reporting;
        # single-type invoices keep their own type.
        move_invoice_type = (
            'damage' if self.invoice_type == 'repair_damage' else self.invoice_type
        )
        invoice_vals = {
            'move_type': 'out_invoice',
            'partner_id': order.partner_id.id,
            'partner_shipping_id': order.partner_shipping_id.id,
            'invoice_date': fields.Date.today(),
            'rental_sale_id': order.id,
            'rental_invoice_type': move_invoice_type,
            'journal_id': self.journal_id.id if self.journal_id else (
                order.company_id._hksf_journal_for('repair').id
            ),
            'company_id': order.company_id.id,
            'invoice_origin': order.name,
            'ref': order.client_order_ref or order.name,
            'invoice_payment_term_id': order.payment_term_id.id,
        }
        invoice = self.env['account.move'].sudo().create(invoice_vals)

        for wline in lines_to_invoice:
            product = wline.product_id
            # Account routing is PER LINE (so a combined repair+damage invoice
            # still books each line to the correct income account):
            #   repair -> R&D income account (own repair revenue)
            #   damage -> lost income account (damage = lost, beyond repair)
            #   lost   -> lost income account
            line_type = wline.line_type or self.invoice_type
            account = None
            if line_type == 'repair':
                account = product.product_tmpl_id.property_r_and_d_account_income_id
            else:  # 'damage' or 'lost'
                account = product.product_tmpl_id.property_lost_account_income_id
            if not account:
                account = product.categ_id.property_account_income_categ_id

            # Prefix the line description with its type so a combined
            # Repair + Damage invoice clearly distinguishes each line.
            #   repair -> [Repair]  damage -> [Damage]  lost -> [Lost]
            type_label = {
                'repair': _('Repair'),
                'damage': _('Damage'),
                'lost': _('Lost'),
            }.get(line_type)
            base_name = wline.name or product.name or ''
            if type_label and not base_name.startswith('[%s]' % type_label):
                description = '[%s] %s' % (type_label, base_name)
            else:
                description = base_name

            line_vals = {
                'move_id': invoice.id,
                'product_id': product.id,
                'quantity': wline.qty,
                'price_unit': wline.price_unit,
                'name': description,
                'account_id': account.id if account else False,
            }
            self.env['account.move.line'].sudo().create(line_vals)

            # Partial-lost tracking: accumulate invoiced_qty on the source
            # line. Only stamp invoice_id (mark fully done) once the line is
            # fully consumed, so any remaining qty stays billable later.
            if wline.damage_line_id:
                src = wline.damage_line_id
                src.invoiced_qty = (src.invoiced_qty or 0.0) + wline.qty
                if src.invoiced_qty >= src.qty - 1e-6:
                    src.invoice_id = invoice.id

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': invoice.id,
            'target': 'current',
        }


class DamageLostInvoiceWizardLine(models.TransientModel):
    _name = 'damage.lost.invoice.wizard.line'
    _description = 'Damage Lost Invoice Wizard Line'

    wizard_id = fields.Many2one(
        'damage.lost.invoice.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    damage_line_id = fields.Many2one(
        'collection.repair.damage',
        string='Source Line',
    )
    line_type = fields.Selection(
        selection=[
            ('repair', 'Repair'),
            ('damage', 'Damage'),
            ('lost', 'Lost'),
        ],
        string='Type',
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
    )
    name = fields.Char(string='Description')
    qty = fields.Float(
        string='Quantity',
        digits='Product Unit of Measure',
        default=1.0,
    )
    price_unit = fields.Float(
        string='Unit Price',
        digits='Product Price',
    )
    include = fields.Boolean(
        string='Include',
        default=True,
    )
