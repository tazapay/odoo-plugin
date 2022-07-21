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
            if data.get('state') == 'Payment_Received':
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
                if transaction_id.payment_token_id:
                    transaction_id.payment_token_id.verified = True
        return True


class WebsiteSaleExtended(WebsiteSale):

    def buyer_seller_handshake(self, values):
        acquirer_id = request.env['payment.acquirer'].sudo().search([('provider', '=', 'tazapay')], limit=1)

        names = values.get('name').split(' ')
        buyer_vals = {
            'first_name': names[0],
            'last_name': names[1] if len(names) > 1 else values.get('company_name'),
            'email': values.get('email'),
            'country': request.env['res.country'].browse(values.get('country_id')).code,
            'contact_number': values.get('phone')
        }
        buyer_id = self._get_user(data=buyer_vals, user_type='Individual')

        company_id = request.website.company_id
        seller_val = {
            'business_name': company_id.name,
            'country': company_id.country_id.code,
            'email': acquirer_id.tazapay_email,
        }

        seller_id = self._get_user(data=seller_val, user_type='Business')

        return buyer_id, seller_id

    def _get_user(self, data, user_type):
        if not data.get('first_name'):
            data['first_name'] = data.get('last_name')
        user_request = self._tazapay_request(endpoint=f"/v1/user/{data.get('email')}", method='GET')
        response = json.loads(user_request.text)

        if response.get('status') == 'error':
            data.update({
                'ind_bus_type': user_type,
            })
            user_request = self._tazapay_request(data=json.dumps(data), endpoint='/v1/user', method='POST')
            response = json.loads(user_request.text)
            return response.get('data')['account_id']
        return response.get('data')['id']

    def _tazapay_request(self, data=None, method='POST', endpoint=None):
        acquirer_id = request.env['payment.acquirer'].sudo().search([('provider', '=', 'tazapay')])
        url = urls.url_join(acquirer_id._get_tazapay_urls(environment=acquirer_id.state)['tazapay_form_url'], endpoint)
        signature, salt, timestamp = acquirer_id._tazapay_signature(request_type=method, endpoint=endpoint)
        headers = {
            'accesskey': acquirer_id.tazapay_api_key,
            'salt': salt,
            'signature': signature,
            'timestamp': str(timestamp),
        }
        resp = requests.request(method, url, data=data, headers=headers)
        return resp

    def _checkout_form_save(self, mode, checkout, all_values):
        buyer_id, seller_id = self.buyer_seller_handshake(values=checkout)
        # update sellers uuid
        request.website.company_id.partner_id.sudo().write({
            'tazapay_user_id': seller_id
        })

        checkout['tazapay_user_id'] = buyer_id
        Partner = request.env['res.partner']
        if mode[0] == 'new':
            partner_id = Partner.sudo().with_context(tracking_disable=True).create(checkout).id
        elif mode[0] == 'edit':
            partner_id = int(all_values.get('partner_id', 0))
            if partner_id:
                # double check
                order = request.website.sale_get_order()
                shippings = Partner.sudo().search([("id", "child_of", order.partner_id.commercial_partner_id.ids)])
                if partner_id not in shippings.mapped('id') and partner_id != order.partner_id.id:
                    return Forbidden()
                Partner.browse(partner_id).sudo().write(checkout)
        return partner_id

    def _get_shop_payment_values(self, order, **kwargs):
        if not order.partner_id.tazapay_user_id or not order.company_id.partner_id.tazapay_user_id:
            data = {
                'name': order.partner_id.name,
                'email': order.partner_id.email,
                'country': order.partner_id.country_id.code,
                'contact_number': order.partner_id.phone
            }
            buyer_id, seller_id = self.buyer_seller_handshake(values=data)
            # update seller's uuid
            request.website.company_id.partner_id.sudo().write({'tazapay_user_id': seller_id})
            # update buyer's uuid
            order.partner_id.sudo().write({'tazapay_user_id': buyer_id})

        values = dict(
            website_sale_order=order,
            errors=[],
            partner=order.partner_id.id,
            order=order,
            payment_action_id=request.env.ref('payment.action_payment_acquirer').id,
            return_url= '/shop/payment/validate',
            bootstrap_formatting=True
        )

        domain = expression.AND([
            ['&', ('state', 'in', ['enabled', 'test']), ('company_id', '=', order.company_id.id)],
            ['|', ('website_id', '=', False), ('website_id', '=', request.website.id)],
            ['|', ('country_ids', '=', False), ('country_ids', 'in', [order.partner_id.country_id.id])]
        ])
        acquirers = request.env['payment.acquirer'].search(domain)

        values['access_token'] = order.access_token
        values['acquirers'] = [acq for acq in acquirers if (acq.payment_flow == 'form' and acq.view_template_id) or
                                    (acq.payment_flow == 's2s' and acq.registration_view_template_id)]
        values['tokens'] = request.env['payment.token'].search([
            ('acquirer_id', 'in', acquirers.ids),
            ('partner_id', 'child_of', order.partner_id.commercial_partner_id.id)])

        if order:
            values['acq_extra_fees'] = acquirers.get_acquirer_extra_fees(
                order.amount_total, order.currency_id, order.partner_id.country_id.id)
        return values
