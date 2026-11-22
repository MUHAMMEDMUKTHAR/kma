from odoo import models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        res = super()._action_done()
        self.sale_id.printing_job_ids.sudo()._check_delivered()
        return res
