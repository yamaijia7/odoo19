# -*- coding: utf-8 -*-
from . import models
from . import wizard


def _set_product_price_precision(env):
    """Ensure 'Product Price' decimal precision is 5.

    HKSF rental invoicing stores a pro-rated UNIT price on each invoice line
    (price_unit = rate / 30 * rent_days). At the Odoo default precision of 2
    the unit price is truncated (e.g. 4.83 instead of 4.83333) BEFORE being
    multiplied by quantity, which shaves cents off every pro-rated line and no
    longer reconciles against the original Odoo 11 invoices. Setting precision
    to 5 keeps the full unit price so the line subtotal rounds only once.

    Done in a hook (not XML data) because updating another module's
    decimal.precision record via xmlid is unreliable across upgrades.
    """
    prec = env['decimal.precision'].search([('name', '=', 'Product Price')], limit=1)
    if prec and prec.digits < 5:
        prec.digits = 5


def _ensure_report_url_loopback(env):
    """Guarantee wkhtmltopdf can fetch the report CSS asset bundles.

    ROOT CAUSE of the blank invoice header: the custom header is rendered by
    wkhtmltopdf as a SEPARATE header sub-document. Odoo's minimal_layout wraps
    it in <html style="height:0"> and relies on the report asset bundles
    (web.report_assets_pdf / web.report_assets_common -> 'html,body{height:100%}')
    to give the header band a real height. wkhtmltopdf fetches those CSS files
    over HTTP from <base href="{report.url or web.base.url}">.

    If that URL is not reachable from the Odoo server process itself (common in
    production behind a reverse proxy / firewall, or when web.base.url points at
    an external hostname the box can't loop back to), the CSS fails to load,
    the header collapses to 0 height and the band prints BLANK -- even though
    the HTML render and the asset are perfectly correct.

    Fix: point 'report.url' at the loopback interface + the configured HTTP
    port, which the server can always reach. We only set it when it is empty so
    we never override an admin's explicit value. Idempotent across upgrades.
    """
    icp = env['ir.config_parameter'].sudo()
    if icp.get_param('report.url'):
        return
    from odoo.tools import config as odoo_config
    port = odoo_config.get('http_port') or odoo_config.get('xmlrpc_port') or 8069
    icp.set_param('report.url', 'http://127.0.0.1:%s' % port)


def post_init_hook(env):
    _set_product_price_precision(env)
    _ensure_report_url_loopback(env)
