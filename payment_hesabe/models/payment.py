# -*- coding: utf-8 -*-

from werkzeug import urls

from odoo import api, fields, models, _
from odoo.addons.payment_hesabe.models.hesabecrypt import encrypt, decrypt
from odoo.addons.payment_hesabe.models.hesabeutil import checkout
from odoo.addons.payment.models.payment_acquirer import ValidationError
from odoo.tools.float_utils import float_compare
import ast
import json
import logging

_logger = logging.getLogger(__name__)


class AccountPaymentMethod(models.Model):
    _inherit = 'account.payment.method'

    @api.model
    def _get_payment_method_information(self):
        res = super()._get_payment_method_information()
        if 'hesabe_mpgs' not in res:
            res['hesabe_mpgs'] = {'mode': 'unique', 'domain': [('type', '=', 'bank')]}
        if 'hesabe_knet' not in res:
            res['hesabe_knet'] = {'mode': 'unique', 'domain': [('type', '=', 'bank')]}
        return res


class PaymentAcquirerHesabe(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[('hesabe_knet', 'Hesabe KNET'), ('hesabe_mpgs', 'Hesabe MPGS')], ondelete={'hesabe_knet': 'set default', 'hesabe_mpgs': 'set default'})
    secret_key = fields.Char(groups='base.group_user')
    merchant_code = fields.Char(groups='base.group_user')
    access_code = fields.Char(groups='base.group_user')
    iv_key = fields.Char(groups='base.group_user')
    api_version = fields.Char(groups='base.group_user', default='2.0')
    production_url = fields.Char(groups='base.group_user')
    sandbox_url = fields.Char(groups='base.group_user')

    def _get_hesabe_urls(self, environment):
        self.ensure_one()
        if environment == 'test':
            return {'hesabe_form_url': self.sandbox_url}
        elif environment == 'enabled':
            return {'hesabe_form_url': self.production_url}
        else:
            return {'hesabe_form_url': ''}


    def _get_hesabe_action_url(self):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return base_url + '/payment/hesabe'


class PaymentTransactionHesabe(models.Model):
    _inherit = 'payment.transaction'

    hesabe_data = fields.Text(string="Hesabe Data")

    def _get_default_payment_method_id(self):
        self.ensure_one()
        if self.provider not in ('hesabe_knet', 'hesabe_mpgs'):
            return super()._get_default_payment_method_id()
        if self.provider == 'hesabe_knet':
            return self.env.ref('payment_hesabe.payment_method_hesabe_knet').id
        elif self.provider == 'hesabe_knet':
            return self.env.ref('payment_hesabe.payment_method_hesabe_mpgs').id
        else:
            return super()._get_default_payment_method_id()

    def _get_specific_rendering_values(self, processing_values):
        """ Override of payment to return paytabs-specific rendering values.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of acquirer-specific processing values
        :rtype: dict
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.acquirer_id.provider not in ('hesabe_knet', 'hesabe_mpgs'):
            return res

        base_url = self.env['ir.config_parameter'].get_param('web.base.url')

        if processing_values.get('currency_id'):
            currency_id = self.env['res.currency'].browse(processing_values['currency_id'])
        else:
            currency_id = self.env.company.currency_id

        payload = {
            "merchantCode": self.acquirer_id.merchant_code,
            "currency": currency_id and currency_id.name  or '',
            "amount": processing_values['amount'],
            "responseUrl": urls.url_join(base_url, '/payment/hesabe/%s/return' % ('knet' if self.acquirer_id.provider == 'hesabe_knet' else 'mpgs')),
            "paymentType": 1 if self.acquirer_id.provider == 'hesabe_knet' else 2,
            "version": self.acquirer_id.api_version,
            "orderReferenceNumber": processing_values['reference'],
            "failureUrl": urls.url_join(base_url, '/payment/hesabe/%s/fail' % ('knet' if self.acquirer_id.provider == 'hesabe_knet' else 'mpgs')),
            "variable2": processing_values['amount'],
        }

        url = self.acquirer_id._get_hesabe_urls(self.acquirer_id.state)['hesabe_form_url']

        encryptedText = encrypt(str(json.dumps(payload)), self.acquirer_id.secret_key, self.acquirer_id.iv_key)
        checkoutToken = checkout(encryptedText, url, self.acquirer_id.access_code, 'production' if self.acquirer_id.state == 'enabled' else 'test')

        if '"status":false' in checkoutToken:
            raise ValidationError(_("This Merchant doesn't support this payment method!"))
        else:
            result = decrypt(checkoutToken, self.acquirer_id.secret_key, self.acquirer_id.iv_key)
            if '"status":false' in result:
                raise ValidationError(
                    _("Service Unavailable: We are sorry the service is not available for this account. Please contact the business team for further information."))
            response = json.loads(result)
            decryptToken = response['response']['data']
            if decryptToken != '':
                url = urls.url_join(url, "/payment")
            else:
                url = "/shop"

        rendering_values = {
            'api_url': url,
            'decryptToken' : decryptToken
        }
        return rendering_values


    @api.model
    def _get_tx_from_feedback_data(self, data, provider):
        tx = super()._get_tx_from_feedback_data(provider, data)
        if provider not in ('hesabe_knet', 'hesabe_mpgs'):
            return tx

        reference = data.get('response').get('orderReferenceNumber')
        tx = self.search([('reference', '=', reference)],limit=1)

        if not tx:
            error_msg = (_(
                'Hesabe %s: received data for reference %s; no order found') % (provider, reference))
            raise ValidationError(error_msg)
        elif len(tx) > 1:
            error_msg = (_(
                'Hesabe %s: received data for reference %s; multiple orders found') % (provider, reference))
            raise ValidationError(error_msg)
        tx.sudo().write({'hesabe_data' : data})
        return tx


    def _process_feedback_data(self, data):
        """ Override of payment to process the transaction based on Alipay data.

        Note: self.ensure_one()

        :param dict data: The feedback data sent by the provider
        :return: None
        :raise: ValidationError if inconsistent data were received
        """
        super()._process_feedback_data(data)
        if self.acquirer_id.provider not in ('hesabe_knet', 'hesabe_mpgs'):
            return

        if isinstance(data, str):
            data = ast.literal_eval(self.hesabe_data)

        status = data.get('status')

        result = self.write({
            'acquirer_reference': data.get('response').get('paymentId'),
        })
        if status:
            self._set_done()
        else:
            self._set_canceled("Hesabe: " + _("Canceled payment with status: %s", data.get('message')))