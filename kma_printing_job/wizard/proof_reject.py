from odoo import _, api, fields, models

# What kind of change the customer asked for. Stored in the proof comment and in the
# job's chatter, so the shop can later see what actually causes re-work.
REASON_TYPES = [
    ('content', 'Text or content change'),
    ('design', 'Design or layout change'),
    ('colour', 'Colour change'),
    ('size', 'Size or dimensions'),
    ('spelling', 'Spelling mistake'),
    ('quality', 'Print or material quality'),
    ('other', 'Something else'),
]


class KmaProofRejectWizard(models.TransientModel):
    _name = 'kma.proof.reject.wizard'
    _description = 'Request Proof Changes'

    line_ids = fields.Many2many('kma.printing.job.line', string='Items', required=True)
    item_label = fields.Char(compute='_compute_preview', string='Item')
    proof_label = fields.Char(compute='_compute_preview', string='Current Proof')
    proof_image = fields.Binary(compute='_compute_preview', string='Proof')
    reason_type = fields.Selection(REASON_TYPES, string='What needs to change',
                                   required=True, default='content')
    reason = fields.Text(string='Details', required=True)
    attachment_ids = fields.Many2many('ir.attachment', string='Files From the Customer',
                                      help='Anything the customer sent with the change request. '
                                           'These are filed on the item as reference files.')

    @api.depends('line_ids')
    def _compute_preview(self):
        """Show the designer what is being rejected, so a wrong item is obvious."""
        for wizard in self:
            lines = wizard.line_ids
            wizard.item_label = ', '.join(lines.mapped('product_id.display_name'))
            if len(lines) == 1:
                proof = lines.current_proof_id
                wizard.proof_label = proof.name or _('No proof')
                wizard.proof_image = proof.preview_image
            else:
                wizard.proof_label = _('%s items together', len(lines))
                wizard.proof_image = False

    def action_confirm(self):
        self.ensure_one()
        label = dict(REASON_TYPES).get(self.reason_type) or ''
        detail = (self.reason or '').strip()
        comment = '%s: %s' % (label, detail) if label else detail
        self.line_ids._reject_proof(comment, _('%s (on behalf of customer)', self.env.user.name))
        if self.attachment_ids:
            # the customer's own files belong with the requirement, not buried in the wizard
            self.line_ids.write({'brief_attachment_ids': [(4, att.id) for att in self.attachment_ids]})
        return {'type': 'ir.actions.act_window_close'}
