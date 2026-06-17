# -*- coding: utf-8 -*-
"""Fix the BLANK invoice/report header in the wkhtmltopdf PDF (production).

Problem
-------
The custom rental invoice header (logo banner + INVOICE title + Invoice No +
client block) renders correctly in the HTML report and in the header HTML that
Odoo hands to wkhtmltopdf -- yet the printed PDF shows a tall BLANK band where
the header should be.

Root cause
----------
wkhtmltopdf renders the report header as a SEPARATE header sub-document. Odoo's
web.minimal_layout wraps that sub-document in `<html style="height: 0;">` and
relies on the report asset bundles (web.report_assets_pdf /
web.report_assets_common, which contain `html, body { height: 100% }`) to give
the header band a real height. Those CSS files are fetched over HTTP by
wkhtmltopdf from `<base href="{report.url or web.base.url}">`.

In production the box runs behind a reverse proxy / firewall and web.base.url
points at an external hostname that the Odoo server process itself cannot loop
back to. wkhtmltopdf's request for the CSS fails (ConnectionRefused / timeout),
`html,body{height:100%}` never applies, the `height:0` wins, and the header
band collapses to zero height -> prints blank. The body table still prints
because its own `<table>` carries intrinsic height.

Reproduced locally: with the asset URL unreachable the header is blank; with it
reachable (report.url -> loopback) the full header renders pixel-correct.

Fix
---
Point `report.url` at the loopback interface + the configured HTTP port, which
the server can always reach regardless of proxy/DNS. Only set it when empty so
an explicit admin value is never overridden. Idempotent across upgrades.

(post_init_hook does the same on fresh installs; this migration covers the
already-installed production database on -u hksf_rental.)
"""
import logging

from odoo import api, SUPERUSER_ID
from odoo.tools import config as odoo_config

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    icp = env['ir.config_parameter'].sudo()
    current = icp.get_param('report.url')
    if current:
        _logger.info(
            "hksf_rental 1.18.0: report.url already set (%s) -- left untouched.",
            current,
        )
        return
    port = odoo_config.get('http_port') or odoo_config.get('xmlrpc_port') or 8069
    url = 'http://127.0.0.1:%s' % port
    icp.set_param('report.url', url)
    _logger.info(
        "hksf_rental 1.18.0: set report.url=%s so wkhtmltopdf can fetch the "
        "report CSS bundles and the invoice header band renders (was blank).",
        url,
    )
