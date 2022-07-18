odoo.define('payment_tazapay.payment_form', function (require) {
    "use strict";

    var core = require('web.core');
    var Dialog = require('web.Dialog');
    var publicWidget = require('web.public.widget');
    var payment_form = require('payment.payment_form')

    var _t = core._t;

    payment_form.include({
        _get_redirect_form_method: function () {
            var providers = $(this.target).find("input[name='pm_id']")
            var tazapay_provider = providers.filter(value => $(providers[value]).data('provider') === 'tazapay')
            if (tazapay_provider.length > 0){
                return "get"
            }
            return "post";
        },
    })
})