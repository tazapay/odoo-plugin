# -*- coding: utf-8 -*-

{
    'name': 'Tazapay Payment Acquirer',
    'category': 'Accounting/Payment Acquirers',
    'sequence': 380,
    'summary': 'Payment Acquirer: Tazapay Implementation',
    'version': '1.0',
    'author': 'Tazapay',
    'description': """Tazapay Payment Acquirer""",
    'website': 'https://tazapay.com/',
    'depends': ['payment', 'sale'],
    'data': [
        'views/assets.xml',
        'views/payment_views.xml',
        'views/payment_tazapay_templates.xml',
        'data/payment_acquirer_data.xml',
    ],
    'images': ['static/description/icon.png', 'static/description/banner.png'],
    'installable': True,
    'application': True,
    'post_init_hook': 'create_missing_journal_for_acquirers',
    'uninstall_hook': 'uninstall_hook',
    'license': 'LGPL-3',
}
