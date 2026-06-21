# -*- coding: utf-8 -*-
from odoo import models, fields


class HksfLinkedOrderWizard(models.TransientModel):
    """Small wizard launched from a billing-master SO to create a new linked
    (child) order, either copying the master's lines or starting blank."""
    _name = 'hksf.linked.order.wizard'
    _description = 'HKSF New Linked Order Wizard'

    order_id = fields.Many2one(
        'sale.order',
        string='Master Order',
        required=True,
    )
    copy_lines = fields.Boolean(
        string='Copy Lines from Master',
        default=True,
        help="When ticked, the new linked order starts with a copy of the "
             "master's order lines. Untick to start with a blank order.",
    )

    def action_apply(self):
        self.ensure_one()
        # Footer buttons pass default_copy_lines via context to force the
        # choice (Copy Lines / Blank Order) regardless of the checkbox state;
        # fall back to the field value when not overridden.
        copy_lines = self.env.context.get('default_copy_lines', self.copy_lines)
        return self.order_id.with_context(
            copy_lines=copy_lines
        ).action_new_linked_order()
