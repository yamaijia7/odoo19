# -*- coding: utf-8 -*-
from odoo import models, fields


class HksfPrintReportWizard(models.TransientModel):
    """Print-document chooser launched from the Sale Order form header.

    Lets the user pick which branded SO report to print. Each report type
    shares the same custom HKSF header/footer; only the body design differs.
    New report types are added by extending the report_type selection and
    mapping the value to its ir.actions.report xml id in _REPORT_ACTIONS.
    """
    _name = 'hksf.print.report.wizard'
    _description = 'HKSF Print Document Wizard'

    # value -> ir.actions.report external id
    _REPORT_ACTIONS = {
        'rental': 'hksf_rental.action_report_hksf_rental',
        'ed': 'hksf_rental.action_report_hksf_sale_native',
    }

    order_id = fields.Many2one(
        'sale.order',
        string='Order',
        required=True,
    )
    report_type = fields.Selection(
        selection=[
            ('rental', 'Rental Quote'),
            ('ed', 'E&D Quote'),
        ],
        string='Document',
        required=True,
        default='rental',
    )

    def action_print(self):
        self.ensure_one()
        action_xmlid = self._REPORT_ACTIONS[self.report_type]
        report = self.env.ref(action_xmlid)
        return report.report_action(self.order_id)
