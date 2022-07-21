# coding: utf-8

import json
import logging

import dateutil.parser
import pytz
import requests
from werkzeug import urls

import hashlib
import base64
import json
import requests
from datetime import datetime
import calendar
from random import choices
import hmac
import string
from odoo.http import request
import pprint

from odoo import api, fields, models, _
from odoo.addons.payment.models.payment_acquirer import ValidationError
from odoo.addons.payment_tazapay.controllers.main import TazaPayController
from odoo.tools.float_utils import float_compare


_logger = logging.getLogger(__name__)

escrow_txn_no = ''


class AcquirerTazapay(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[
        ('tazapay', 'Tazapay')
    ], ondelete={'tazapay': 'set default'})
    tazapay_api_key = fields.Char(required_if_provider='tazapay', groups='base.group_user')
    tazapay_api_secret = fields.Char(required_if_provider='tazapay', groups='base.group_user')

    @api.model
    def _get_tazapay_urls(self, environment):
        if environment == 'enabled':
            return {
                'tazapay_form_url': 'https://api.tazapay.com',
            }
        else:
            return {
                'tazapay_form_url': 'https://api-sandbox.tazapay.com',
            }

    def _tazapay_request(self, data=None, method='POST', endpoint=None):
        url = urls.url_join(self._get_tazapay_urls(environment=self.state)['tazapay_form_url'], endpoint)
        signature, salt, timestamp = self._tazapay_signature(request_type=method, endpoint=endpoint)
        headers = {
            'accesskey': self.tazapay_api_key,
            'salt': salt,
            'signature': signature,
            'timestamp': str(timestamp),
        }
        resp = requests.request(method, url, data=data, headers=headers)
        return resp

    def _tazapay_signature(self, request_type, endpoint):
        salt = ''.join(choices(string.ascii_letters + string.digits, k=10))
        date_time = datetime.utcnow()
        timestamp = calendar.timegm(date_time.utctimetuple())
        # generate signature
        to_sign = request_type + endpoint + salt + str(timestamp) + self.tazapay_api_key + self.tazapay_api_secret
        h = hmac.new(bytes(self.tazapay_api_secret, 'utf-8'), bytes(to_sign, 'utf-8'), hashlib.sha256)
        signature = base64.urlsafe_b64encode(str.encode(h.hexdigest()))
        return signature, salt, timestamp

    def buyer_seller_handshake(self, values):
        buyer_id = values.get('partner').tazapay_user_id
        seller_id = values.get('partner')['company_id'].partner_id.tazapay_user_id

        txn_no = self._create_escrow(
            seller_id=seller_id,
            buyer_id=buyer_id, amount=values.get('amount'),
            currency_code=values.get('currency').name
        )
        return txn_no

    def tazapay_form_generate_values(self, values):
        global escrow_txn_no

        escrow_txn_no = self.buyer_seller_handshake(values=values)

        tazapay_tx_values = dict(values)
        return tazapay_tx_values

    def _create_escrow(self, seller_id, buyer_id, currency_code, amount):
        data = {
            'initiated_by': seller_id,
            'buyer_id': buyer_id,
            'seller_id': seller_id,
            'txn_description': 'Odoo',
            'invoice_currency': currency_code,
            'invoice_amount': amount,
            'transaction_source': 'Odoo'
        }
        escrow_request = self._tazapay_request(endpoint='/v1/escrow', method='POST', data=json.dumps(data))
        response = json.loads(escrow_request.text)
        if response.get('status') == 'success':
            return response.get('data')['txn_no']
        else:
            return False

    def tazapay_get_form_action_url(self):
        self.ensure_one()
        last_tx_id = request.session.get('__website_sale_last_tx_id')
        self.env['payment.transaction'].browse(last_tx_id).write({
            'acquirer_reference': escrow_txn_no
        })
        payment_data = {
            'txn_no': escrow_txn_no,
            'complete_url': urls.url_join(self.get_base_url(), TazaPayController._complete_url),
            'error_url': urls.url_join(self.get_base_url(), TazaPayController._error_url),
            'callback_url': urls.url_join(self.get_base_url(), TazaPayController._callback_url)
        }
        payment_request = self._tazapay_request(
            data=json.dumps(payment_data), endpoint='/v1/session/payment', method='POST')
        response = json.loads(payment_request.text)

        redirect_url = response.get('data')['redirect_url']
        return redirect_url


class PaymentTransactionRave(models.Model):
    _inherit = 'payment.transaction'

    payable_amount = fields.Monetary(string="Payable Amount")
    paid_amount = fields.Monetary(string="Paid Amount")
    collection_method = fields.Char(string="Collection Method")
    collection_currency = fields.Many2one('res.currency', string="Collection Currency")
    txt_state = fields.Char(string="Escrow State")

    def _escrow_payment_verification(self, data):
        txn_no = data.sudo().acquirer_reference
        payment_status = self.acquirer_id.sudo()._tazapay_request(data=None, method='GET', endpoint=f"/v1/escrow/{txn_no}")
        return self._tazapay_validate_tree(payment_status.json(), data)

    def _tazapay_validate_tree(self, tree, data):
        self.ensure_one()
        if self.state == 'pending':
            self._process_pending_transaction(tree, data)
        if self.state != 'draft':
            _logger.info('Tazapay: trying to validate an already validated tx (ref %s)', self.reference)
            return True

        status = tree.get('status')
        amount = tree["data"]["invoice_amount"]
        currency = tree["data"]["invoice_currency"]

        tree_data = tree.get("data")

        if status == 'success' and amount == data.amount and currency == data.currency_id.name:
            order_val = {
                'date': fields.datetime.now(),
                'acquirer_reference': tree["data"]["txn_no"],
                'txt_state': tree["data"]["state"],
            }
            if tree_data.get("collection_method"):
                currency_id = self.env['res.currency'].search([
                        ('name', '=', tree["data"]["payment"]["collection_currency"])])
                order_val.update({
                    'collection_method': tree["data"]["payment"]["collection_method"],
                    'collection_currency': currency_id.id if currency_id else False,
                    'payable_amount': tree["data"]["payment"]["payable_amount"],
                    'paid_amount': tree["data"]["payment"]["paid_amount"],
                })

            self.write(order_val)
            if tree["data"]["state"] == 'Awaiting_Payment':
                self._set_transaction_pending()
            else:
                self._set_transaction_done()
            self.execute_callback()
            if self.payment_token_id:
                self.payment_token_id.verified = True
            return True
        else:
            error = tree['message']
            _logger.warning(error)
            self.sudo().write({
                'state_message': error,
                'acquirer_reference': tree["data"]["txn_no"],
                'date': fields.datetime.now(),
            })
            self._set_transaction_cancel()
            return False

    def _process_pending_transaction(self, tree, data):
        status = tree.get('status')
        amount = tree["data"]["invoice_amount"]
        currency = tree["data"]["invoice_currency"]

        tree_data = tree.get("data")

        if status == 'success' and amount == data.amount and currency == data.currency_id.name:
            order_val = {
                'date': fields.datetime.now(),
                'acquirer_reference': tree["data"]["txn_no"],
                'txt_state': tree["data"]["state"],
            }
            if tree_data.get("collection_method"):
                currency_id = self.env['res.currency'].search([
                        ('name', '=', tree["data"]["payment"]["collection_currency"])])
                order_val.update({
                    'collection_method': tree["data"]["payment"]["collection_method"],
                    'collection_currency': currency_id.id if currency_id else False,
                    'payable_amount': tree["data"]["payment"]["payable_amount"],
                    'paid_amount': tree["data"]["payment"]["paid_amount"],
                })

            self.write(order_val)
            if tree["data"]["state"] == 'Awaiting_Payment':
                self._set_transaction_pending()
            else:
                self._set_transaction_done()
            self.execute_callback()
            if self.payment_token_id:
                self.payment_token_id.verified = True
            return True
