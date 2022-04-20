# -*- coding: utf-8 -*-

import logging
import json
import pprint
import werkzeug

from odoo import http
from odoo.http import request
from odoo.addons.payment_hesabe.models.hesabecrypt import decrypt

_logger = logging.getLogger(__name__)


class HesabeController(http.Controller):
    @http.route(['/payment/hesabe/knet/return', '/payment/hesabe/knet/fail'],  auth='public', website='true', csrf=False)
    def hesabe_knet_return(self, **post):
        hesabe = http.request.env['payment.acquirer'].search([('provider', '=', 'hesabe_knet')], limit=1).sudo()
        data = decrypt(post['data'], hesabe.secret_key, hesabe.iv_key)
        response = json.loads(data)
        _logger.info('Hesabe Knet: entering _handle_feedback_data with post data %s', pprint.pformat(response))
        if response:
            http.request.env['payment.transaction'].sudo()._handle_feedback_data(response, 'hesabe_knet')
        return http.request.redirect('/payment/status')

    @http.route(['/payment/hesabe/mpgs/return', '/payment/hesabe/mpgs/fail'], auth='public', website='true', csrf=False)
    def hesabe_mpgs_return(self, **post):
        hesabe = http.request.env['payment.acquirer'].search([('provider', '=', 'hesabe_mpgs')], limit=1).sudo()
        data = decrypt(post['data'], hesabe.secret_key, hesabe.iv_key)
        response = json.loads(data)
        _logger.info('Hesabe MPGS: entering _handle_feedback_data with post data %s', pprint.pformat(response))
        if response:
            http.request.env['payment.transaction'].sudo()._handle_feedback_data(response, 'hesabe_mpgs')
        return http.request.redirect('/payment/status')

    @http.route('/payment/hesabe', auth="public", website='true', methods=['POST'], csrf=False)
    def hesabe_payment(self, **post):
        return werkzeug.utils.redirect(post.get('form_url'))
