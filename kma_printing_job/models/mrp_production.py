from odoo import fields, models


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    printing_job_line_id = fields.Many2one('kma.printing.job.line', string='Printing Item', index=True,
                                           copy=False, readonly=True)
    printing_job_id = fields.Many2one(related='printing_job_line_id.job_id', store=True, index=True,
                                      string='Printing Job')
    printing_proof_id = fields.Many2one(related='printing_job_line_id.current_proof_id', string='Approved Artwork')
    printing_proof_image = fields.Binary(related='printing_job_line_id.current_proof_image', string='Artwork')

    def button_mark_done(self):
        res = super().button_mark_done()
        self.printing_job_line_id.sudo()._check_production_done()
        return res

    def action_cancel(self):
        res = super().action_cancel()
        self.printing_job_line_id.sudo()._kma_on_mo_cancelled()
        return res
