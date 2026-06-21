# -*- coding: utf-8 -*-
{
    'name': 'HKSSL OCA Admin Accountant',
    'version': '19.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Grants Odoo Administrator users full Accountant accounting rights when account_usability (OCA) is installed.',
    'description': """
        OCA account_usability renames group_account_manager to "Accountant"
        and removes its implication of group_account_invoice, leaving the
        base Administrator user (base.user_admin / base.user_root) with no
        accounting group. This module ensures both admin users are always
        members of account.group_account_manager (Accountant), giving them
        full accounting visibility while retaining Settings access.
    """,
    'author': 'H.K. Scafframe Systems Limited',
    'website': 'https://github.com/yamaijia7/odoo19',
    'depends': [
        'account',
        'account_usability',
    ],
    'data': [
        'security/res_groups.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}
