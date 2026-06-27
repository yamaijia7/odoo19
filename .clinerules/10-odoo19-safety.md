# Odoo 19 Source Safety

## Protected files — do NOT edit without explicit confirmation
- odoo-custom-addons/hksf/hksf_rental/wizard/delivery_invoice_wizard.py
  (Normal/Charge First billing sync — CRITICAL)
- odoo-custom-addons/hksf/hksf_rental/models/sale_order.py

## Large files — load deliberately, not automatically
- delivery_invoice_wizard.py ~17K tokens
- sale_order.py ~15K tokens
- Ask before reading in full. Read by section if possible.

## After any edit
- Report exact file + lines changed.
- Run: python3 -m py_compile <file> and confirm no syntax error.
