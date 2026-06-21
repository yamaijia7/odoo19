# -*- coding: utf-8 -*-
"""Unified "New Order" dispatcher wizard.

A thin launcher that merges the two header actions:

  - Top-up this order  -> opens hksf.so.topup.wizard (the qty grid wizard)
  - New linked order    -> sale.order.action_new_linked_order()

It holds no business logic of its own. The top-up branch RETURNS the existing
top-up wizard action (so the user gets its interactive quantity grid as the
next step); the linked branch delegates straight to the sale order method.
This keeps the top-up line model / computed qty / overbill guards in exactly
one place.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class HksfNewOrderWizard(models.TransientModel):
    _name = 'hksf.new.order.wizard'
    _description = 'New Order (Top-up / Linked dispatcher)'

    order_id = fields.Many2one(
        'sale.order',
        string='Order',
        required=True,
        default=lambda self: self.env.context.get('active_id'),
    )
    custom_sale_type = fields.Selection(
        related='order_id.custom_sale_type',
        string='Sale Type',
        readonly=True,
    )

    mode = fields.Selection(
        selection=[
            ('topup', 'Top-up this order (add delivery quantity)'),
            ('linked', 'New linked order (separate order, same billing)'),
        ],
        string='What do you want to do?',
        required=True,
        default='topup',
    )

    # linked-only option
    copy_lines = fields.Boolean(
        string='Copy Lines from this Order',
        default=True,
        help="When ticked, the new linked order starts with a copy of this "
             "order's lines. Untick to start blank.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if active_id and 'mode' in fields_list:
            order = self.env['sale.order'].browse(active_id)
            # Top-up only applies to rental orders; default Sales orders to the
            # linked branch.
            res['mode'] = 'topup' if order.custom_sale_type == 'rent' else 'linked'
        return res

    def action_apply(self):
        self.ensure_one()
        if not self.order_id:
            raise UserError(_("No order found."))

        if self.mode == 'topup':
            return self._dispatch_topup()
        if self.mode == 'linked':
            return self._dispatch_linked()
        raise UserError(_("Please choose an option."))

    # ------------------------------------------------------------------
    def _dispatch_topup(self):
        """Open the existing Top-up Delivery grid wizard as the next step."""
        if self.order_id.custom_sale_type != 'rent':
            raise UserError(_(
                "Top-up Delivery is only available for rental orders. "
                "Use 'New linked order' for a Sales order."
            ))
        if self.order_id.state not in ('sale', 'done'):
            raise UserError(_("The order must be confirmed before topping up."))
        action = self.env['ir.actions.act_window']._for_xml_id(
            'hksf_rental.action_hksf_so_topup_wizard'
        )
        action['context'] = {'default_order_id': self.order_id.id}
        return action

    def _dispatch_linked(self):
        """Delegate to the sale order's linked-order creation."""
        if self.order_id.state != 'sale':
            raise UserError(_(
                "The order must be confirmed (state 'Sales Order') before "
                "creating a linked order."
            ))
        return self.order_id.with_context(
            copy_lines=self.copy_lines
        ).action_new_linked_order()
