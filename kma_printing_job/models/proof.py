from odoo import _, api, fields, models
from odoo.exceptions import UserError


class KmaPrintingProof(models.Model):
    _name = 'kma.printing.proof'
    _description = 'Artwork Proof'
    _order = 'version desc, id desc'

    job_line_id = fields.Many2one('kma.printing.job.line', string='Item', required=True,
                                  ondelete='cascade', index=True)
    job_id = fields.Many2one(related='job_line_id.job_id', store=True, index=True, string='Printing Job')
    product_id = fields.Many2one(related='job_line_id.product_id', store=True, string='Product')
    version = fields.Integer(readonly=True, copy=False)
    name = fields.Char(compute='_compute_name')
    file = fields.Binary(string='Proof File', attachment=True, required=True)
    filename = fields.Char()
    mimetype = fields.Char(compute='_compute_mimetype')
    preview_image = fields.Binary(compute='_compute_preview_image', string='Preview')
    partner_id = fields.Many2one(related='job_id.partner_id')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('approved', 'Approved'),
        ('rejected', 'Changes Requested'),
    ], default='draft', required=True, readonly=True)
    date_sent = fields.Datetime(readonly=True)
    date_decided = fields.Datetime(readonly=True)
    decided_by = fields.Char(readonly=True)
    customer_comment = fields.Text(string='Customer Comment', readonly=True)

    @api.depends('version')
    def _compute_name(self):
        for proof in self:
            proof.name = _('Proof v%s', proof.version or 1)

    def _compute_mimetype(self):
        Attachment = self.env['ir.attachment'].sudo()
        for proof in self:
            att = Attachment.search([('res_model', '=', self._name), ('res_id', '=', proof.id),
                                     ('res_field', '=', 'file')], limit=1) if proof.id else False
            proof.mimetype = att.mimetype if att else False

    def _compute_preview_image(self):
        for proof in self:
            proof.preview_image = proof.with_context(bin_size=False).file \
                if (proof.mimetype or '').startswith('image/') else False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            line = self.env['kma.printing.job.line'].browse(vals.get('job_line_id'))
            vals['version'] = max(line.proof_ids.mapped('version') or [0]) + 1
        return super().create(vals_list)

    def unlink(self):
        if any(p.state != 'draft' for p in self):
            raise UserError(_('Only proofs that were not sent yet can be deleted. Sent proofs are kept as history.'))
        return super().unlink()
