import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

# Stages of one item (one product line). Each item moves on its own.
ITEM_STAGES = [
    ('artwork_pending', 'Artwork Pending'),
    ('awaiting_approval', 'Awaiting Approval'),
    ('approved', 'Approved'),
    ('in_production', 'In Production'),
    ('qc', 'QC'),
    ('ready', 'Ready'),
    ('delivered', 'Delivered'),
    ('cancel', 'Cancelled'),
]
FLOW = [key for key, _label in ITEM_STAGES if key != 'cancel']

STAGE_DATE_FIELDS = {
    'awaiting_approval': 'date_proof_sent',
    'approved': 'date_approved',
    'in_production': 'date_production',
    'qc': 'date_qc',
    'ready': 'date_ready',
    'delivered': 'date_delivered',
}

STAGE_ICONS = {
    'artwork_pending': 'fa-paint-brush',
    'awaiting_approval': 'fa-hourglass-half',
    'approved': 'fa-thumbs-up',
    'in_production': 'fa-cogs',
    'qc': 'fa-check-square-o',
    'ready': 'fa-cube',
    'delivered': 'fa-truck',
}


def _fmt_dt(record, value):
    return fields.Datetime.context_timestamp(record, value).strftime('%d %b, %H:%M') if value else ''


def _banner(kind, icon, message):
    return Markup('<div class="alert alert-%s o_kma_banner" role="alert"><i class="fa %s" title=""></i><div>%s</div></div>') % (
        kind, icon, message)


class KmaPrintingJob(models.Model):
    _name = 'kma.printing.job'
    _description = 'Printing Job'
    _inherit = ['portal.mixin', 'mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, date_deadline, id desc'

    name = fields.Char(string='Job Reference', required=True, copy=False, readonly=True,
                       index=True, default=lambda self: _('New'))
    sale_order_id = fields.Many2one('sale.order', string='Sales Order', required=True,
                                    readonly=True, copy=False, index=True, ondelete='restrict')
    partner_id = fields.Many2one(related='sale_order_id.partner_id', store=True, string='Customer')
    company_id = fields.Many2one(related='sale_order_id.company_id', store=True)
    user_id = fields.Many2one(related='sale_order_id.user_id', store=True, string='Salesperson')
    designer_id = fields.Many2one('res.users', string='Designer', tracking=True,
                                  domain=[('share', '=', False)])
    date_deadline = fields.Date(string='Deadline', tracking=True)
    priority = fields.Selection([('0', 'Normal'), ('1', 'Urgent')], default='0', string='Priority')
    color = fields.Integer()
    note = fields.Html(string='Production Notes')

    is_cancelled = fields.Boolean(readonly=True, copy=False)
    stage = fields.Selection(
        ITEM_STAGES, string='Stage', compute='_compute_stage', store=True, index=True, tracking=True,
        group_expand='_expand_stages',
        help='The job is as far as its slowest item. Each item moves through the stages on its own.')

    line_ids = fields.One2many('kma.printing.job.line', 'job_id', string='Items')
    line_summary = fields.Char(compute='_compute_line_summary')
    process_labels = fields.Char(compute='_compute_process_labels', store=True, string='Processes')

    # item counters
    item_count = fields.Integer(compute='_compute_stored_counts', store=True, string='Item Count')
    to_send_count = fields.Integer(compute='_compute_item_counts', string='Proofs Ready to Send')
    approved_count = fields.Integer(compute='_compute_stored_counts', store=True, string='Artwork Approved')
    to_produce_count = fields.Integer(compute='_compute_item_counts', string='Ready for MO')
    produced_count = fields.Integer(compute='_compute_stored_counts', store=True, string='Produced')
    ready_count = fields.Integer(compute='_compute_stored_counts', store=True, string='Ready Items')
    artwork_percent = fields.Integer(compute='_compute_item_counts', string='Artwork')
    production_percent = fields.Integer(compute='_compute_item_counts', string='Production')
    stage_bar_html = fields.Html(compute='_compute_item_counts', sanitize=False)

    # stage dates: when the LAST item reached the stage
    date_proof_sent = fields.Datetime(compute='_compute_stage_dates', store=True, string='All Proofs Sent')
    date_approved = fields.Datetime(compute='_compute_stage_dates', store=True, string='All Approved')
    date_production = fields.Datetime(compute='_compute_stage_dates', store=True, string='All In Production')
    date_qc = fields.Datetime(compute='_compute_stage_dates', store=True, string='All in QC')
    date_ready = fields.Datetime(compute='_compute_stage_dates', store=True, string='All Ready')
    date_delivered = fields.Datetime(string='Delivered On', readonly=True, copy=False)

    # The requirement the customer gave, before any artwork exists. Order-wide here;
    # anything that applies to one product belongs on that item.
    brief_note = fields.Html(string='Customer Brief', sanitize=True,
                             help='What the customer asked for, for the whole order. A requirement that '
                                  'applies to one product belongs on that item instead.')
    brief_attachment_ids = fields.Many2many(
        'ir.attachment', 'kma_job_brief_attachment_rel', 'job_id', 'attachment_id',
        string='Reference Files', help='Files the customer sent: logo, text content, photos, a sample.')
    brief_file_count = fields.Integer(compute='_compute_brief', string='Reference Files')
    has_brief = fields.Boolean(compute='_compute_brief', string='Brief Recorded')
    brief_missing_count = fields.Integer(compute='_compute_brief', string='Items Without a Requirement')

    production_ids = fields.One2many('mrp.production', 'printing_job_id', string='Manufacturing Orders')
    production_count = fields.Integer(compute='_compute_counts')
    delivery_count = fields.Integer(compute='_compute_counts')
    proof_count = fields.Integer(compute='_compute_counts', string='Proofs')

    currency_id = fields.Many2one(related='sale_order_id.currency_id', store=True)
    amount_total = fields.Monetary(related='sale_order_id.amount_total', store=True, string='Order Value')
    invoice_status = fields.Selection(related='sale_order_id.invoice_status', string='Invoice Status')
    invoice_count = fields.Integer(related='sale_order_id.invoice_count', string='Invoices')

    deadline_state = fields.Selection([
        ('on_track', 'On Track'),
        ('due_soon', 'Due Soon'),
        ('overdue', 'Overdue'),
        ('done', 'Closed'),
    ], compute='_compute_deadline_state', store=True, string='Deadline Status')
    timeline_html = fields.Html(compute='_compute_timeline_html', sanitize=False)
    status_banner = fields.Html(compute='_compute_status_banner', sanitize=False)

    _sql_constraints = [
        ('sale_order_uniq', 'unique(sale_order_id)', 'Only one printing job is allowed per sales order.'),
    ]

    @api.model
    def _expand_stages(self, states, domain, order):
        return FLOW

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('kma.printing.job') or _('New')
        return super().create(vals_list)

    def _compute_access_url(self):
        super()._compute_access_url()
        for job in self:
            job.access_url = '/my/printing_jobs/%s' % job.id

    def _get_report_base_filename(self):
        self.ensure_one()
        return 'Printing Job %s' % self.name

    def _active_lines(self):
        return self.line_ids.filtered(lambda l: l.stage != 'cancel')

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('line_ids.stage', 'is_cancelled', 'date_delivered')
    def _compute_stage(self):
        for job in self:
            active = job._active_lines()
            if job.is_cancelled or (job.line_ids and not active):
                job.stage = 'cancel'
            elif job.date_delivered:
                job.stage = 'delivered'
            elif not active:
                job.stage = 'artwork_pending'
            else:
                job.stage = FLOW[min(FLOW.index(s) for s in active.mapped('stage'))]

    @api.depends('line_ids.product_id', 'line_ids.product_uom_qty')
    def _compute_line_summary(self):
        for job in self:
            job.line_summary = ', '.join(
                '%s × %g' % (line.product_id.name, line.product_uom_qty) for line in job.line_ids)

    @api.depends('line_ids.product_id.kma_print_process')
    def _compute_process_labels(self):
        labels = dict(self.env['product.template']._fields['kma_print_process']._description_selection(self.env))
        for job in self:
            procs = []
            for proc in job.line_ids.product_id.mapped('kma_print_process'):
                if proc and labels[proc] not in procs:
                    procs.append(labels[proc])
            job.process_labels = ', '.join(procs)

    @api.depends('line_ids.stage')
    def _compute_stored_counts(self):
        for job in self:
            active = job._active_lines()
            idx = [FLOW.index(l.stage) for l in active]
            job.item_count = len(active)
            job.approved_count = len([i for i in idx if i >= FLOW.index('approved')])
            job.produced_count = len([i for i in idx if i >= FLOW.index('qc')])
            job.ready_count = len([i for i in idx if i >= FLOW.index('ready')])

    @api.depends('line_ids.stage', 'line_ids.proof_state', 'item_count', 'approved_count', 'produced_count')
    def _compute_item_counts(self):
        labels = dict(ITEM_STAGES)
        for job in self:
            active = job._active_lines()
            total = len(active)
            job.to_send_count = len(active.filtered(lambda l: l.stage == 'artwork_pending' and l.proof_state == 'draft'))
            job.to_produce_count = len(active.filtered(lambda l: l.stage == 'approved'))
            job.artwork_percent = int(100 * job.approved_count / total) if total else 0
            job.production_percent = int(100 * job.produced_count / total) if total else 0
            parts = []
            for key in FLOW:
                n = len(active.filtered(lambda l, k=key: l.stage == k))
                if n:
                    parts.append(Markup('<span class="o_kma_seg o_kma_seg_%s" style="flex:%s" title="%s: %s"></span>') % (
                        key, n, labels[key], n))
            job.stage_bar_html = Markup('<div class="o_kma_segbar">%s</div>') % Markup('').join(parts) if parts else False

    @api.depends('line_ids.stage', 'line_ids.date_proof_sent', 'line_ids.date_approved', 'line_ids.date_production',
                 'line_ids.date_qc', 'line_ids.date_ready')
    def _compute_stage_dates(self):
        for job in self:
            active = job._active_lines()
            for key in ('awaiting_approval', 'approved', 'in_production', 'qc', 'ready'):
                fname = STAGE_DATE_FIELDS[key]
                reached = active and all(FLOW.index(l.stage) >= FLOW.index(key) for l in active)
                values = [d for d in active.mapped(fname) if d]
                job[fname] = max(values) if reached and values else False

    @api.depends('brief_note', 'brief_attachment_ids', 'line_ids.has_brief', 'line_ids.stage')
    def _compute_brief(self):
        """An order-wide brief covers every item; otherwise each item needs its own."""
        for job in self:
            job.brief_file_count = len(job.brief_attachment_ids)
            job.has_brief = bool(job.brief_attachment_ids) or bool(html2plaintext(job.brief_note or '').strip())
            job.brief_missing_count = 0 if job.has_brief else len(
                job._active_lines().filtered(lambda l: l.stage == 'artwork_pending' and not l.has_brief))

    def _compute_counts(self):
        for job in self:
            job.production_count = len(job.production_ids)
            job.delivery_count = len(job.sale_order_id.picking_ids)
            job.proof_count = len(job.line_ids.proof_ids)

    @api.depends('date_deadline', 'stage')
    def _compute_deadline_state(self):
        today = fields.Date.context_today(self)
        for job in self:
            if job.stage in ('delivered', 'cancel'):
                job.deadline_state = 'done'
            elif not job.date_deadline:
                job.deadline_state = 'on_track'
            elif job.date_deadline < today:
                job.deadline_state = 'overdue'
            elif (job.date_deadline - today).days <= 1:
                job.deadline_state = 'due_soon'
            else:
                job.deadline_state = 'on_track'

    @api.model
    def _cron_refresh_deadline_state(self):
        jobs = self.search([('stage', 'not in', ('delivered', 'cancel'))])
        self.env.add_to_compute(self._fields['deadline_state'], jobs)
        jobs._recompute_recordset(['deadline_state'])

    @api.depends('stage', 'create_date', 'date_proof_sent', 'date_approved', 'date_production',
                 'date_qc', 'date_ready', 'date_delivered', 'line_ids.stage')
    def _compute_timeline_html(self):
        labels = dict(ITEM_STAGES)
        for job in self:
            active = job._active_lines()
            total = len(active)
            cur = FLOW.index(job.stage) if job.stage in FLOW else -1
            steps = []
            for i, key in enumerate(FLOW):
                if i < cur or (key == 'delivered' and job.stage == 'delivered'):
                    state = 'done'
                elif i == cur:
                    state = 'current'
                else:
                    state = 'todo'
                # how many items have reached this stage
                reached = len([l for l in active if FLOW.index(l.stage) >= i])
                value = job.create_date if key == 'artwork_pending' else job[STAGE_DATE_FIELDS[key]]
                if state == 'done' and value:
                    sub = _fmt_dt(job, value)
                elif total and 0 < reached < total:
                    sub = _('%(n)s of %(t)s items', n=reached, t=total)
                else:
                    sub = ''
                icon = 'fa-check' if state == 'done' else STAGE_ICONS[key]
                cls = state + (' o_kma_step_partial' if state == 'todo' and reached else '')
                steps.append(Markup(
                    '<div class="o_kma_step o_kma_step_%s"><span class="o_kma_dot"><i class="fa %s"></i></span>'
                    '<span class="o_kma_label">%s</span><span class="o_kma_date">%s</span></div>'
                ) % (cls, icon, labels[key], sub))
            job.timeline_html = Markup('<div class="o_kma_timeline">%s</div>') % Markup('').join(steps)

    @api.depends('stage', 'deadline_state', 'line_ids.stage', 'line_ids.proof_state', 'line_ids.qc_percent',
                 'brief_missing_count')
    def _compute_status_banner(self):
        today = fields.Date.context_today(self)
        for job in self:
            out = []
            active = job._active_lines()

            def names(lines):
                return ', '.join(lines.mapped('product_id.name'))

            if job.deadline_state == 'overdue':
                out.append(_banner('danger', 'fa-exclamation-triangle',
                                   _('Overdue by %(d)s day(s). Deadline was %(date)s.',
                                     d=(today - job.date_deadline).days, date=job.date_deadline.strftime('%d %b %Y'))))
            if job.stage == 'delivered':
                msg = _('Delivered on %s.', _fmt_dt(job, job.date_delivered))
                if job.invoice_status == 'to invoice':
                    msg += ' ' + _('The sales order is not fully invoiced yet.')
                out.append(_banner('success', 'fa-truck', msg))
            elif job.stage != 'cancel':
                rejected = active.filtered(lambda l: l.stage == 'artwork_pending' and l.last_rejected_proof_id
                                           and l.proof_state != 'draft')
                for line in rejected:
                    p = line.last_rejected_proof_id
                    out.append(_banner('warning', 'fa-comment', Markup(_(
                        '<b>%(item)s</b>: changes requested by %(who)s: “%(text)s”. Upload a revised proof.')) % {
                        'item': line.product_id.name, 'who': p.decided_by or '', 'text': p.customer_comment or ''}))
                if job.brief_missing_count:
                    no_brief = active.filtered(lambda l: l.stage == 'artwork_pending' and not l.has_brief)
                    out.append(_banner('warning', 'fa-inbox', _(
                        'No customer requirement recorded for: %s. Add it on the item before the designer '
                        'starts, so nothing is designed from memory.', names(no_brief))))
                no_proof = active.filtered(lambda l: l.stage == 'artwork_pending' and not l.proof_state
                                           and not l.last_rejected_proof_id)
                if no_proof:
                    out.append(_banner('info', 'fa-upload', _('Upload a proof for: %s. Click an item to add its proof.',
                                                              names(no_proof))))
                to_send = active.filtered(lambda l: l.stage == 'artwork_pending' and l.proof_state == 'draft')
                if to_send:
                    out.append(_banner('info', 'fa-paper-plane', _(
                        'Proof ready to send: %s. Click Send Proofs to email the customer.', names(to_send))))
                waiting = active.filtered(lambda l: l.stage == 'awaiting_approval')
                if waiting:
                    out.append(_banner('warning', 'fa-hourglass-half', _(
                        'Waiting for %(partner)s to approve: %(items)s. Approve or request changes on the item '
                        'if the customer replied by phone or email.', partner=job.partner_id.name, items=names(waiting))))
                approved = active.filtered(lambda l: l.stage == 'approved')
                if approved:
                    out.append(_banner('warning', 'fa-thumbs-up', _(
                        'Artwork approved but without a manufacturing order: %s. Open the item and click '
                        'Create MO.', names(approved))))
                qc = active.filtered(lambda l: l.stage == 'qc')
                if qc:
                    out.append(_banner('info', 'fa-check-square-o', _(
                        'Quality check pending for: %s. Open the item, tick the checks and click QC Passed.', names(qc))))
                if active and all(l.stage == 'ready' for l in active):
                    out.append(_banner('success', 'fa-cube', _(
                        'All items are ready. Validate the delivery order to close the job.')))
            job.status_banner = Markup('').join(out) if out else False

    # ------------------------------------------------------------------
    # Job-level actions (they act on the items that are ready for them)
    # ------------------------------------------------------------------
    def action_send_proofs(self):
        template = self.env.ref('kma_printing_job.mail_template_proof_sent', raise_if_not_found=False)
        for job in self:
            lines = job.line_ids.filtered(lambda l: l.stage == 'artwork_pending' and l.proof_state == 'draft')
            if not lines:
                raise UserError(_('No item has a new proof to send. Open an item and upload its proof first.'))
            lines._send_proof()
            job._notify_proofs_sent(lines, template)
        return True

    def _notify_proofs_sent(self, lines, template):
        self.ensure_one()
        self._portal_ensure_token()
        if template:
            self.with_context(kma_sent_line_ids=lines.ids).message_post_with_source(
                template, message_type='comment', subtype_xmlid='mail.mt_comment', partner_ids=self.partner_id.ids)

    def action_create_mos(self):
        """Bulk fallback: manufacturing orders are created automatically on approval.

        No longer on the job form. Kept for the Items list action and for items
        left in Approved because the automatic creation failed.
        """
        for job in self:
            lines = job.line_ids.filtered(lambda l: l.stage == 'approved')
            if not lines:
                raise UserError(_('No item has approved artwork waiting for production.'))
            lines._create_production()
        return self.action_view_productions() if len(self) == 1 else True

    def action_customer_preview(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'target': 'self', 'url': self.get_portal_url()}

    def _check_delivered(self):
        """Validating the delivery delivers the items that passed quality check.

        An item that is still waiting for artwork, production or QC is never
        delivered by this hook: a validated picking must not carry an item that
        was never printed. The job itself is only Delivered once every active
        item is.
        """
        for job in self.filtered(lambda j: j.stage not in ('delivered', 'cancel')):
            pickings = job.sale_order_id.picking_ids.filtered(
                lambda p: p.picking_type_code == 'outgoing' and p.state != 'cancel')
            if not pickings or any(p.state != 'done' for p in pickings):
                continue
            active = job._active_lines()
            if not active:
                continue
            just_delivered = active.filtered(lambda l: l.stage == 'ready')
            if just_delivered:
                just_delivered.write({'stage': 'delivered'})
            pending = active.filtered(lambda l: l.stage != 'delivered')
            if pending:
                job.message_post(body=_(
                    'Delivery validated. %(done)s item(s) set to Delivered. The job stays open because '
                    '%(late)s item(s) have not passed quality check yet: %(names)s',
                    done=len(just_delivered), late=len(pending),
                    names=', '.join(pending.mapped('product_id.name'))))
            else:
                job.date_delivered = fields.Datetime.now()
                job.message_post(body=_('All items delivered. Job set to Delivered automatically.'))

    def action_cancel(self):
        for job in self.filtered(lambda j: j.stage != 'delivered'):
            job.line_ids.filtered(lambda l: l.stage != 'delivered').write({'stage': 'cancel'})
            job.is_cancelled = True
        return True

    def action_reset(self):
        for job in self.filtered(lambda j: j.stage == 'cancel'):
            job.is_cancelled = False
            for line in job.line_ids.filtered(lambda l: l.stage == 'cancel'):
                line.stage = 'artwork_pending' if line.requires_artwork else 'approved'
        return True

    # ------------------------------------------------------------------
    # Smart buttons
    # ------------------------------------------------------------------
    def action_view_sale_order(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'sale.order', 'view_mode': 'form',
                'res_id': self.sale_order_id.id}

    def action_view_productions(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('mrp.mrp_production_action')
        action['domain'] = [('printing_job_id', '=', self.id)]
        action['context'] = {}
        if len(self.production_ids) == 1:
            action.update({'view_mode': 'form', 'views': [(False, 'form')], 'res_id': self.production_ids.id})
        return action

    def action_view_deliveries(self):
        self.ensure_one()
        return self.sale_order_id.action_view_delivery()

    def action_view_invoices(self):
        self.ensure_one()
        return self.sale_order_id.action_view_invoice()

    def action_view_proofs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Proofs'),
            'res_model': 'kma.printing.proof',
            'view_mode': 'kanban,tree,form',
            'views': [(self.env.ref('kma_printing_job.view_printing_proof_kanban').id, 'kanban'),
                      (self.env.ref('kma_printing_job.view_printing_proof_tree').id, 'tree'),
                      (False, 'form')],
            'domain': [('job_id', '=', self.id)],
            'context': {'search_default_group_line': 1},
        }


class KmaPrintingJobLine(models.Model):
    """One printing item: its own artwork, approval, manufacturing order and QC."""
    _name = 'kma.printing.job.line'
    _description = 'Printing Job Item'
    _order = 'job_id, sequence, id'
    _rec_names_search = ['product_id', 'job_id']

    job_id = fields.Many2one('kma.printing.job', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    sale_line_id = fields.Many2one('sale.order.line', string='Order Line', ondelete='set null')
    product_id = fields.Many2one('product.product', required=True, string='Item')
    name = fields.Text(string='Specification')
    product_uom_qty = fields.Float(string='Quantity', digits='Product Unit of Measure')
    product_uom = fields.Many2one('uom.uom', string='Unit')
    print_process = fields.Selection(related='product_id.kma_print_process')
    requires_artwork = fields.Boolean(related='product_id.kma_requires_artwork')

    stage = fields.Selection(ITEM_STAGES, string='Stage', default='artwork_pending', required=True,
                             index=True, copy=False, group_expand='_expand_stages')
    designer_id = fields.Many2one('res.users', string='Designer', domain=[('share', '=', False)],
                                  compute='_compute_designer', store=True, readonly=False,
                                  help='Defaults to the job designer. Set it to give this item to another designer.')

    # related job information (item board, filters)
    partner_id = fields.Many2one(related='job_id.partner_id', store=True, string='Customer')
    sale_order_id = fields.Many2one(related='job_id.sale_order_id', store=True)
    company_id = fields.Many2one(related='job_id.company_id', store=True)
    date_deadline = fields.Date(related='job_id.date_deadline', store=True)
    deadline_state = fields.Selection(related='job_id.deadline_state', store=True)
    priority = fields.Selection(related='job_id.priority', store=True)

    # requirement: what the customer asked for, and the files they sent
    brief_note = fields.Html(string='What the Customer Asked For', sanitize=True,
                             help='Text content, sizes, colours, what to change from the last job. '
                                  'Fill this in before the designer starts.')
    brief_attachment_ids = fields.Many2many(
        'ir.attachment', 'kma_job_line_brief_attachment_rel', 'line_id', 'attachment_id',
        string='Reference Files', help='Files the customer sent: logo, text content, photos, a sample.')
    brief_file_count = fields.Integer(compute='_compute_brief', string='Reference Files')
    has_brief = fields.Boolean(compute='_compute_brief', string='Requirement Recorded', store=True)

    # artwork
    proof_ids = fields.One2many('kma.printing.proof', 'job_line_id', string='Proofs')
    proof_count = fields.Integer(compute='_compute_proof')
    current_proof_id = fields.Many2one('kma.printing.proof', compute='_compute_proof')
    last_rejected_proof_id = fields.Many2one('kma.printing.proof', compute='_compute_proof')
    proof_state = fields.Selection([
        ('draft', 'Not Sent'),
        ('sent', 'Sent'),
        ('approved', 'Approved'),
        ('rejected', 'Changes Requested'),
    ], compute='_compute_proof', string='Proof Status')
    proof_version = fields.Char(compute='_compute_proof', string='Proof')
    current_proof_image = fields.Binary(compute='_compute_proof_image', string='Proof Preview')

    # production
    production_id = fields.Many2one('mrp.production', string='Manufacturing Order', copy=False, readonly=True)
    production_state = fields.Selection(related='production_id.state', string='MO Status')
    qc_line_ids = fields.One2many('kma.printing.job.qc', 'job_line_id', string='Quality Check')
    qc_progress = fields.Char(compute='_compute_qc_progress', string='QC')
    qc_percent = fields.Integer(compute='_compute_qc_percent', store=True, string='QC %')

    # stage dates
    date_proof_sent = fields.Datetime(string='Proof Sent', readonly=True, copy=False)
    date_approved = fields.Datetime(string='Approved On', readonly=True, copy=False)
    date_production = fields.Datetime(string='Production Started', readonly=True, copy=False)
    date_qc = fields.Datetime(string='QC Started', readonly=True, copy=False)
    date_ready = fields.Datetime(string='Ready On', readonly=True, copy=False)
    date_delivered = fields.Datetime(string='Delivered On', readonly=True, copy=False)
    days_waiting = fields.Integer(compute='_compute_days_waiting', string='Days Waiting')
    status_hint = fields.Html(compute='_compute_status_hint', sanitize=False)

    @api.model
    def _expand_stages(self, states, domain, order):
        return [key for key, _label in ITEM_STAGES if key not in ('cancel', 'delivered')]

    def _compute_display_name(self):
        for line in self:
            line.display_name = '%s · %s' % (line.job_id.name or '', line.product_id.name or '')

    def write(self, vals):
        if vals.get('stage') in STAGE_DATE_FIELDS:
            vals = dict(vals, **{STAGE_DATE_FIELDS[vals['stage']]: fields.Datetime.now()})
        return super().write(vals)

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('job_id.designer_id')
    def _compute_designer(self):
        for line in self:
            if not line.designer_id:
                line.designer_id = line.job_id.designer_id

    @api.depends('brief_note', 'brief_attachment_ids')
    def _compute_brief(self):
        for line in self:
            line.brief_file_count = len(line.brief_attachment_ids)
            line.has_brief = bool(line.brief_attachment_ids) or bool(
                html2plaintext(line.brief_note or '').strip())

    @api.depends('proof_ids.version', 'proof_ids.state')
    def _compute_proof(self):
        for line in self:
            proofs = line.proof_ids.sorted('version', reverse=True)
            current = proofs[:1]
            line.current_proof_id = current
            line.last_rejected_proof_id = proofs.filtered(lambda p: p.state == 'rejected')[:1]
            line.proof_count = len(proofs)
            line.proof_state = current.state
            line.proof_version = current and 'v%s' % current.version or False

    def _compute_proof_image(self):
        for line in self:
            proof = line.current_proof_id
            line.current_proof_image = proof.with_context(bin_size=False).file if (
                proof and (proof.mimetype or '').startswith('image/')) else False

    @api.depends('qc_line_ids.done')
    def _compute_qc_progress(self):
        for line in self:
            total = len(line.qc_line_ids)
            done = len(line.qc_line_ids.filtered('done'))
            line.qc_progress = '%s / %s' % (done, total) if total else False

    @api.depends('qc_line_ids.done')
    def _compute_qc_percent(self):
        for line in self:
            total = len(line.qc_line_ids)
            line.qc_percent = int(100 * len(line.qc_line_ids.filtered('done')) / total) if total else 0

    def _compute_days_waiting(self):
        now = fields.Datetime.now()
        for line in self:
            line.days_waiting = (now - line.date_proof_sent).days \
                if line.stage == 'awaiting_approval' and line.date_proof_sent else 0

    @api.depends('stage', 'proof_state', 'qc_percent', 'production_state', 'has_brief')
    def _compute_status_hint(self):
        for line in self:
            p = line.current_proof_id
            if line.stage == 'artwork_pending':
                if p.state == 'draft':
                    hint = _banner('info', 'fa-paper-plane', _('%s is uploaded. Click Send for Approval.', p.name))
                elif line.last_rejected_proof_id:
                    r = line.last_rejected_proof_id
                    hint = _banner('warning', 'fa-comment', Markup(_(
                        '<b>Changes requested</b> on %(proof)s by %(who)s: “%(text)s”. Add a new proof below.')) % {
                        'proof': r.name, 'who': r.decided_by or '', 'text': r.customer_comment or ''})
                elif not line.has_brief:
                    hint = _banner('warning', 'fa-inbox', _(
                        'No requirement recorded yet. Open the Requirement tab, write what the customer '
                        'asked for and attach the files they sent - then design the proof.'))
                else:
                    hint = _banner('info', 'fa-upload', _(
                        'Requirement recorded. Add the proof file in the Artwork tab, then click Send for Approval.'))
            elif line.stage == 'awaiting_approval':
                ago = _('%s day(s) ago', line.days_waiting) if line.days_waiting else _('today')
                hint = _banner('warning', 'fa-hourglass-half', _(
                    '%(proof)s sent to the customer %(ago)s. Waiting for approval.', proof=p.name, ago=ago))
            elif line.stage == 'approved':
                hint = _banner('warning', 'fa-thumbs-up', _(
                    '%(proof)s approved by %(who)s. No manufacturing order yet - click Create MO.',
                    proof=p.name or '', who=p.decided_by or ''))
            elif line.stage == 'in_production':
                hint = _banner('info', 'fa-cogs', _('In production on %(mo)s (%(state)s).', mo=line.production_id.name,
                                                    state=dict(line.production_id._fields['state']._description_selection(
                                                        self.env)).get(line.production_state, '')))
            elif line.stage == 'qc':
                hint = _banner('info', 'fa-check-square-o', _(
                    'Quality check: %s passed. Tick every check, then click QC Passed.', line.qc_progress or '0 / 0'))
            elif line.stage == 'ready':
                hint = _banner('success', 'fa-cube', _('Ready for delivery.'))
            else:
                hint = False
            line.status_hint = hint

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _check_stage(self, allowed):
        labels = dict(ITEM_STAGES)
        for line in self:
            if line.stage not in allowed:
                raise UserError(_('%(item)s is in stage "%(stage)s". This action needs: %(allowed)s.',
                                  item=line.display_name, stage=labels[line.stage],
                                  allowed=', '.join(labels[a] for a in allowed)))

    # ------------------------------------------------------------------
    # Artwork
    # ------------------------------------------------------------------
    def _send_proof(self):
        self._check_stage(['artwork_pending'])
        for line in self:
            proof = line.current_proof_id
            if not proof or proof.state != 'draft' or not proof.file:
                raise UserError(_('%s: upload a new proof file before sending it for approval.', line.display_name))
            proof.write({'state': 'sent', 'date_sent': fields.Datetime.now()})
            line.stage = 'awaiting_approval'
            line.job_id.message_post(body=_('%(proof)s of %(item)s sent for approval.',
                                            proof=proof.name, item=line.product_id.name))

    def action_send_proof(self):
        template = self.env.ref('kma_printing_job.mail_template_proof_sent', raise_if_not_found=False)
        self._send_proof()
        for job in self.job_id:
            job._notify_proofs_sent(self.filtered(lambda l: l.job_id == job), template)
        return True

    def _approve_proof(self, decided_by):
        self._check_stage(['awaiting_approval'])
        for line in self:
            proof = line.current_proof_id
            proof.write({'state': 'approved', 'date_decided': fields.Datetime.now(), 'decided_by': decided_by})
            line.stage = 'approved'
            line.job_id.message_post(body=_('%(proof)s of %(item)s approved by %(who)s.',
                                            proof=proof.name, item=line.product_id.name, who=decided_by))
        self._kma_auto_create_mo()
        return True

    def _reject_proof(self, reason, decided_by):
        self._check_stage(['awaiting_approval'])
        for line in self:
            proof = line.current_proof_id
            proof.write({'state': 'rejected', 'date_decided': fields.Datetime.now(),
                         'decided_by': decided_by, 'customer_comment': reason})
            line.stage = 'artwork_pending'
            line.job_id.message_post(body=_('Changes requested on %(proof)s of %(item)s by %(who)s: %(reason)s',
                                            proof=proof.name, item=line.product_id.name, who=decided_by, reason=reason))
            designer = line.designer_id or line.job_id.designer_id
            if designer:
                line.job_id.activity_schedule('mail.mail_activity_data_todo', user_id=designer.id,
                                              summary=_('Revise artwork: %s', line.product_id.name), note=reason)
        return True

    def action_approve_proof(self):
        return self._approve_proof(_('%s (on behalf of customer)', self.env.user.name))

    def action_open_reject_wizard(self):
        self._check_stage(['awaiting_approval'])
        return {
            'type': 'ir.actions.act_window',
            'name': _('Request Changes'),
            'res_model': 'kma.proof.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_line_ids': self.ids},
        }

    def action_skip_artwork(self):
        self._check_stage(['artwork_pending'])
        for line in self:
            line.stage = 'approved'
            line.job_id.message_post(body=_('Artwork approval skipped for %(item)s by %(user)s.',
                                            item=line.product_id.name, user=self.env.user.name))
        self._kma_auto_create_mo()
        return True

    # ------------------------------------------------------------------
    # Production
    # ------------------------------------------------------------------
    def _kma_auto_create_mo(self):
        """Create the manufacturing order as soon as an item's artwork is approved.

        A failure here must never block the approval itself: the customer may be
        approving from the portal, and a missing bill of materials must not turn
        that into an error page. The item then stays in Approved and the Create MO
        button on the item is the way to retry.
        """
        for line in self:
            if line.stage != 'approved' or line.production_id:
                continue
            if line.product_id.type not in ('product', 'consu'):
                continue
            try:
                with self.env.cr.savepoint():
                    line._create_production()
            except Exception as err:  # the approval must survive a production problem
                _logger.warning('kma_printing_job: automatic MO failed for item %s: %s', line.id, err)
                line.job_id.message_post(body=_(
                    'Artwork approved for %(item)s, but its manufacturing order could not be created '
                    'automatically: %(error)s Open the item and click Create MO once that is fixed.',
                    item=line.product_id.name, error=err))
        return True

    def _kma_on_mo_cancelled(self):
        """Send an item whose manufacturing order was cancelled back to Approved.

        Without this the item stays In Production for good: Create MO needs
        Approved and QC Passed needs QC, so no button can move it and the whole
        job freezes behind it. An item already in QC or beyond keeps its stage,
        because the work is done.
        """
        for line in self:
            if line.stage != 'in_production':
                continue
            if line.production_id and line.production_id.state != 'cancel':
                continue
            mo_name = line.production_id.name or _('The manufacturing order')
            line.write({'stage': 'approved', 'production_id': False})
            line.job_id.message_post(body=_(
                '%(mo)s was cancelled. %(item)s is back to Approved - click Create MO to produce it again.',
                mo=mo_name, item=line.product_id.name))
        return True

    def _create_production(self):
        self._check_stage(['approved'])
        Production = self.env['mrp.production']
        for line in self:
            product = line.product_id
            if product.type not in ('product', 'consu'):
                raise UserError(_('%s is a service and cannot be manufactured.', product.display_name))
            job = line.job_id
            bom = self.env['mrp.bom']._bom_find(product, company_id=job.company_id.id, bom_type='normal').get(product)
            production = Production.create({
                'product_id': product.id,
                'product_qty': line.product_uom_qty,
                'product_uom_id': line.product_uom.id,
                'bom_id': bom.id if bom else False,
                'origin': '%s / %s' % (job.name, job.sale_order_id.name),
                'company_id': job.company_id.id,
                'printing_job_line_id': line.id,
                'date_deadline': job.date_deadline and fields.Datetime.to_datetime(job.date_deadline),
            })
            production.action_confirm()
            line.write({'production_id': production.id, 'stage': 'in_production'})
            job.message_post(body=_('%(mo)s created for %(item)s.', mo=production._get_html_link(),
                                    item=line.product_id.name))
        return True

    def action_create_mo(self):
        self._create_production()
        if len(self) == 1:
            return {'type': 'ir.actions.act_window', 'res_model': 'mrp.production', 'view_mode': 'form',
                    'res_id': self.production_id.id}
        return True

    def _check_production_done(self):
        templates = self.env['kma.printing.qc.template'].search([])
        for line in self.filtered(lambda l: l.stage == 'in_production' and l.production_id.state == 'done'):
            if not line.qc_line_ids:
                line.qc_line_ids = [(0, 0, {'name': t.name, 'sequence': t.sequence}) for t in templates]
            line.stage = 'qc'

    def action_qc_pass(self):
        self._check_stage(['qc'])
        for line in self:
            if line.qc_line_ids.filtered(lambda q: not q.done):
                raise UserError(_('%s: tick every quality check before marking QC as passed.', line.display_name))
            line.stage = 'ready'
            line.job_id.message_post(body=_('%s passed quality check.', line.product_id.name))
        return True

    def action_open_item(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('kma_printing_job.view_printing_job_line_form').id, 'form')],
            'target': 'current',
        }

    def action_open_job(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'kma.printing.job', 'view_mode': 'form',
                'res_id': self.job_id.id}
