# -*- coding: utf-8 -*-
from odoo import models, fields


class ResCompany(models.Model):
    """
    Adds company-level images used in the PDF report:
      - custom_header_logo  : printed full-width at the top of every page
      - company_stamp       : printed in the bottom-right footer area
      - custom_chop_and_sign: printed in the bottom-centre footer area
                              (only when sale_order.custom_chop_and_sign is True)

    Also holds the per-invoice-type default sale journals. The user books the
    same product four different ways (rental / sale / repair&damage / lost) and
    wants each non-standard type to post to its own journal so the sequence /
    numbering distinguishes them. SALE keeps Odoo's standard default (no field).
    """
    _inherit = 'res.company'

    company_stamp = fields.Binary(string='Company Stamp')
    custom_header_logo = fields.Binary(string='Report Header Logo')
    custom_chop_and_sign = fields.Binary(string='Chop & Sign Image')

    # --- Per-invoice-type default journals (sale-type journals only) ---------
    hksf_rental_journal_id = fields.Many2one(
        'account.journal',
        string='Rental Invoice Journal',
        domain="[('type', '=', 'sale'), ('company_id', '=', id)]",
        help="Default journal for RENTAL invoices. If empty, the company's "
             "standard Sales journal is used.",
    )
    hksf_repair_journal_id = fields.Many2one(
        'account.journal',
        string='Repair / Damage Invoice Journal',
        domain="[('type', '=', 'sale'), ('company_id', '=', id)]",
        help="Default journal for REPAIR / DAMAGE invoices. If empty, the "
             "company's standard Sales journal is used.",
    )
    hksf_lost_journal_id = fields.Many2one(
        'account.journal',
        string='Lost Material Invoice Journal',
        domain="[('type', '=', 'sale'), ('company_id', '=', id)]",
        help="Default journal for LOST MATERIAL invoices. If empty, the "
             "company's standard Sales journal is used.",
    )
    hksf_service_journal_id = fields.Many2one(
        'account.journal',
        string='Service Invoice Journal',
        domain="[('type', '=', 'sale'), ('company_id', '=', id)]",
        help="Default journal for invoices that contain erection / dismantling "
             "SERVICE lines. Any rental invoice carrying at least one service "
             "line posts entirely to this journal. If empty, the company's "
             "standard Sales journal is used.",
    )

    # Map a rental_invoice_type -> the company field holding its journal.
    # 'damage' shares the repair journal (combined Repair+Damage invoice).
    # 'sale' is intentionally absent (standard Odoo default journal).
    _HKSF_JOURNAL_FIELD_BY_TYPE = {
        'rent': 'hksf_rental_journal_id',
        'repair': 'hksf_repair_journal_id',
        'damage': 'hksf_repair_journal_id',
        'lost': 'hksf_lost_journal_id',
        'service': 'hksf_service_journal_id',
    }

    def _hksf_default_sale_journal(self):
        """The company's standard Sales journal (fallback for every type)."""
        self.ensure_one()
        return self.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', self.id),
        ], limit=1)

    def _hksf_journal_for(self, invoice_type):
        """Resolve the journal for a given rental invoice type.

        Returns the configured per-type journal if set, otherwise falls back to
        the company's standard Sales journal (never blocks invoice creation).
        """
        self.ensure_one()
        field = self._HKSF_JOURNAL_FIELD_BY_TYPE.get(invoice_type)
        journal = self[field] if field else False
        return journal or self._hksf_default_sale_journal()
