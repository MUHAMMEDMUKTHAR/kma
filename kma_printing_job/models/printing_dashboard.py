from datetime import timedelta

from odoo import _, api, fields, models

from .printing_job import FLOW, ITEM_STAGES

# Rows shown in each "needs attention" list, and weeks on the trend chart.
ATTENTION_LIMIT = 8
TREND_WEEKS = 8

OPEN_JOB_DOMAIN = [('stage', 'not in', ('delivered', 'cancel'))]
OPEN_ITEM_DOMAIN = [('stage', 'not in', ('delivered', 'cancel'))]


class KmaPrintingJob(models.Model):
    _inherit = 'kma.printing.job'

    @api.model
    def get_printing_dashboard(self, days=30):
        """Everything the Printing dashboard draws, in a single call.

        Counts come from the open items rather than from the jobs, because a job
        is only ever as far along as its slowest item: five jobs can be "Artwork
        Pending" while nine of their items are already in production.
        """
        self.check_access_rights('read')
        Line = self.env['kma.printing.job.line']
        labels = dict(ITEM_STAGES)
        today = fields.Date.context_today(self)
        now = fields.Datetime.now()
        days = int(days or 30)
        since = today - timedelta(days=days)

        open_jobs = self.search(OPEN_JOB_DOMAIN)
        open_items = Line.search(OPEN_ITEM_DOMAIN)
        delivered_jobs = self.search([('stage', '=', 'delivered'), ('date_delivered', '>=', since)])

        def items_in(stage):
            return open_items.filtered(lambda l, s=stage: l.stage == s)

        def avg_days(pairs):
            spans = [(b - a).total_seconds() / 86400.0 for a, b in pairs if a and b and b >= a]
            return round(sum(spans) / len(spans), 1) if spans else 0.0

        # ---------------------------------------------------------------- KPIs
        currency = self.env.company.currency_id
        kpi = {
            'open_jobs': len(open_jobs),
            'open_items': len(open_items),
            'awaiting_artwork': len(items_in('artwork_pending')),
            'awaiting_customer': len(items_in('awaiting_approval')),
            'no_mo': len(items_in('approved')),
            'in_production': len(items_in('in_production')),
            'in_qc': len(items_in('qc')),
            'ready': len(items_in('ready')),
            'overdue': len(open_jobs.filtered(lambda j: j.deadline_state == 'overdue')),
            'due_soon': len(open_jobs.filtered(lambda j: j.deadline_state == 'due_soon')),
            'open_value': sum(open_jobs.mapped('amount_total')),
            'delivered_jobs': len(delivered_jobs),
            'delivered_value': sum(delivered_jobs.mapped('amount_total')),
            'currency': currency.symbol or currency.name,
            'position': currency.position,
            'avg_approval_days': avg_days(
                (l.date_proof_sent, l.date_approved)
                for l in Line.search([('date_approved', '>=', since)])),
            'avg_turnaround_days': avg_days(
                (j.create_date, j.date_delivered) for j in delivered_jobs),
            'days': days,
        }

        # ------------------------------------------------------- stage mix
        stage_mix = []
        for key in FLOW:
            if key == 'delivered':
                continue
            count = len(items_in(key))
            if count:
                stage_mix.append({'key': key, 'label': labels[key], 'count': count})

        # ----------------------------------------------------- process mix
        # print_process is related and not stored, so this is counted in Python.
        selection = dict(self.env['product.template']._fields['kma_print_process']
                         ._description_selection(self.env))
        buckets = {}
        for line in open_items:
            buckets.setdefault(line.product_id.kma_print_process or False, 0)
            buckets[line.product_id.kma_print_process or False] += 1
        process_mix = [{'label': selection.get(key) or _('Not set'), 'count': count}
                       for key, count in sorted(buckets.items(), key=lambda kv: -kv[1])]

        # ---------------------------------------------------- weekly trend
        trend = []
        monday = today - timedelta(days=today.weekday())
        for back in range(TREND_WEEKS - 1, -1, -1):
            start = monday - timedelta(weeks=back)
            end = start + timedelta(days=7)
            trend.append({
                'label': start.strftime('%d %b'),
                'created': self.search_count([('create_date', '>=', start), ('create_date', '<', end)]),
                'delivered': self.search_count([('date_delivered', '>=', start), ('date_delivered', '<', end)]),
            })

        # --------------------------------------------------- top customers
        top_customers = []
        for group in self.read_group(OPEN_JOB_DOMAIN, ['amount_total:sum'], ['partner_id'],
                                     limit=5, orderby='amount_total desc'):
            if not group.get('partner_id'):
                continue
            top_customers.append({
                'id': group['partner_id'][0],
                'name': group['partner_id'][1],
                'jobs': group.get('__count') or group.get('partner_id_count') or 0,
                'amount': group.get('amount_total') or 0.0,
            })

        # ------------------------------------------------- needs attention
        def item_rows(lines, date_field):
            rows = []
            for line in lines:
                stamp = line[date_field] if date_field else False
                rows.append({
                    'id': line.id,
                    'job': line.job_id.name,
                    'product': line.product_id.display_name,
                    'partner': line.partner_id.display_name,
                    'days': (now - stamp).days if stamp else 0,
                    'deadline': fields.Date.to_string(line.date_deadline) or '',
                })
            return rows

        waiting = items_in('awaiting_approval').sorted(key=lambda l: l.date_proof_sent or now)
        approved = items_in('approved').sorted(key=lambda l: l.date_approved or now)
        overdue = open_jobs.filtered(lambda j: j.deadline_state == 'overdue').sorted(
            key=lambda j: j.date_deadline)

        return {
            'kpi': kpi,
            'stage_mix': stage_mix,
            'process_mix': process_mix,
            'trend': trend,
            'top_customers': top_customers,
            'waiting_approval': item_rows(waiting[:ATTENTION_LIMIT], 'date_proof_sent'),
            'no_mo': item_rows(approved[:ATTENTION_LIMIT], 'date_approved'),
            'overdue': [{
                'id': job.id,
                'name': job.name,
                'partner': job.partner_id.display_name,
                'stage': labels.get(job.stage, ''),
                'deadline': fields.Date.to_string(job.date_deadline) or '',
                'days': (today - job.date_deadline).days if job.date_deadline else 0,
            } for job in overdue[:ATTENTION_LIMIT]],
        }
