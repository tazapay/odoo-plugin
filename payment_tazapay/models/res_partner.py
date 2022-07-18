from odoo import models, fields, api, _


class Partner(models.Model):
    _inherit = 'res.partner'

    tazapay_user_id = fields.Char(string="Tazapay User ID")
