from odoo import _, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    printing_job_ids = fields.One2many('kma.printing.job', 'sale_order_id', string='Printing Jobs')
    printing_job_count = fields.Integer(compute='_compute_printing_job_count')

    def _compute_printing_job_count(self):
        for order in self:
            order.printing_job_count = len(order.printing_job_ids)

    def action_confirm(self):
        res = super().action_confirm()
        self._create_printing_jobs()
        return res

    def _create_printing_jobs(self):
        Job = self.env['kma.printing.job'].sudo()
        for order in self:
            if order.printing_job_ids:
                continue
            lines = order.order_line.filtered(
                lambda l: not l.display_type and l.product_id.kma_is_printable)
            if not lines:
                continue
            deadline = order.commitment_date or order.expected_date
            job = Job.create({
                'sale_order_id': order.id,
                'date_deadline': deadline and deadline.date(),
                'line_ids': [(0, 0, {
                    'sale_line_id': line.id,
                    'sequence': line.sequence,
                    'product_id': line.product_id.id,
                    'name': line.name,
                    'product_uom_qty': line.product_uom_qty,
                    'product_uom': line.product_uom.id,
                    'stage': 'artwork_pending' if line.product_id.kma_requires_artwork else 'approved',
                }) for line in lines],
            })
            job.message_subscribe(partner_ids=order.user_id.partner_id.ids)
            order.message_post(body=_('Printing job %s created.', job._get_html_link()))

    def _action_cancel(self):
        res = super()._action_cancel()
        self.printing_job_ids.sudo().action_cancel()
        return res

    def action_view_printing_jobs(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('kma_printing_job.action_printing_job')
        if len(self.printing_job_ids) == 1:
            action.update({'view_mode': 'form', 'views': [(False, 'form')], 'res_id': self.printing_job_ids.id})
        else:
            action['domain'] = [('sale_order_id', '=', self.id)]
        return action
