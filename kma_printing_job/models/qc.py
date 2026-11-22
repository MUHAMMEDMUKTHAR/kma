from odoo import api, fields, models


class KmaPrintingQcTemplate(models.Model):
    _name = 'kma.printing.qc.template'
    _description = 'QC Checklist Item'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class KmaPrintingJobQc(models.Model):
    _name = 'kma.printing.job.qc'
    _description = 'Printing Job Quality Check'
    _order = 'sequence, id'

    job_line_id = fields.Many2one('kma.printing.job.line', string='Item', required=True,
                                  ondelete='cascade', index=True)
    job_id = fields.Many2one(related='job_line_id.job_id', store=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Check', required=True)
    done = fields.Boolean(string='Passed')
    done_by = fields.Many2one('res.users', readonly=True)
    done_date = fields.Datetime(readonly=True)

    def write(self, vals):
        if 'done' in vals:
            vals.update({'done_by': self.env.uid if vals['done'] else False,
                         'done_date': fields.Datetime.now() if vals['done'] else False})
        return super().write(vals)
