import logging
import pprint
import werkzeug
import json
import requests
from odoo import http
from odoo.http import request
from odoo.osv import expression

from werkzeug.exceptions import Forbidden, NotFound
from werkzeug import urls
from odoo.addons.payment.controllers.portal import PaymentProcessing

from odoo.addons.website_sale.controllers.main import WebsiteSale

_logger = logging.getLogger(__name__)
from urllib.parse import urljoin


class TazaPayController(http.Controller):
    _complete_url = "/payment/tazapay/complete"
    _error_url = "/payment/tazapay/error"
    _callback_url = "/payment/tazapay/return"

    @http.route([
        "/payment/tazapay/complete",
        "/payment/tazapay/error",
    ], type="http", auth="public", csrf=False, cors="*")
    def process_tazapay_payment(self, **post):
        _logger.info('Tazapay redirects peacefully')
        last_tx_id = request.env['payment.transaction'].browse(request.session.get('__website_sale_last_tx_id'))
        last_tx_id.sudo()._escrow_payment_verification(data=last_tx_id)
        request.env['payment.transaction'].sudo().form_feedback(post, 'tazapay')
        return werkzeug.utils.redirect('/payment/process')

    @http.route([
        "/payment/tazapay/return",
    ], type="json", auth="public", csrf=False, cors="*", methods=['POST'])
    def tazapay_webhook(self, **post):
        data = json.loads(request.httprequest.data)
        _logger.info('Tazapay sends back data: %s', pprint.pformat(data))
        if data:
            txn_no = data.get('txn_no')
            transaction_id = request.env['payment.transaction'].sudo().search([('acquirer_reference', '=', txn_no)],
                                                                              limit=1)
            if data.get('state') in ['Payment_Received', 'Payout_Completed']:
                payment = data.get('payment')
                currency_id = request.env['res.currency'].search([('name', '=', payment.get('collection_currency'))])
                transaction_id.sudo().write({
                    'collection_method': payment.get('collection_method'),
                    'paid_amount': payment.get('paid_amount'),
                    'collection_currency': currency_id.id if currency_id else False,
                    'payable_amount': payment.get('payable_amount'),
                    'txt_state': data.get('state')
                })
                transaction_id._set_transaction_done()
                transaction_id.execute_callback()
                transaction_id._post_process_after_done()
                if transaction_id.payment_token_id:
                    transaction_id.payment_token_id.verified = True
        return True
