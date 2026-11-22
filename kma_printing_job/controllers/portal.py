from odoo import _, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class PrintingJobPortal(CustomerPortal):

    def _printing_job_domain(self):
        partner = request.env.user.partner_id
        return [('partner_id', 'child_of', partner.commercial_partner_id.id), ('stage', '!=', 'cancel')]

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'printing_job_count' in counters:
            values['printing_job_count'] = request.env['kma.printing.job'].sudo().search_count(
                self._printing_job_domain())
        return values

    @http.route(['/my/printing_jobs', '/my/printing_jobs/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_printing_jobs(self, page=1, **kw):
        Job = request.env['kma.printing.job'].sudo()
        domain = self._printing_job_domain()
        total = Job.search_count(domain)
        pager = portal_pager(url='/my/printing_jobs', total=total, page=page, step=self._items_per_page)
        jobs = Job.search(domain, order='id desc', limit=self._items_per_page, offset=pager['offset'])
        values = self._prepare_portal_layout_values()
        values.update({'jobs': jobs, 'pager': pager, 'page_name': 'printing_job', 'default_url': '/my/printing_jobs'})
        return request.render('kma_printing_job.portal_my_printing_jobs', values)

    def _job_check(self, job_id, access_token):
        return self._document_check_access('kma.printing.job', job_id, access_token=access_token)

    @http.route('/my/printing_jobs/<int:job_id>', type='http', auth='public', website=True)
    def portal_printing_job(self, job_id, access_token=None, message=None, item=None, **kw):
        try:
            job_sudo = self._job_check(job_id, access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')
        values = self._get_page_view_values(job_sudo, access_token, {
            'job': job_sudo,
            'message': message,
            'message_item': job_sudo.line_ids.filtered(lambda l: str(l.id) == str(item or '')),
            'page_name': 'printing_job',
        }, 'my_printing_jobs_history', False)
        return request.render('kma_printing_job.portal_printing_job_page', values)

    @http.route('/my/printing_jobs/<int:job_id>/proof/<int:proof_id>', type='http', auth='public', website=True)
    def portal_printing_job_proof(self, job_id, proof_id, access_token=None, download=False, **kw):
        try:
            job_sudo = self._job_check(job_id, access_token)
        except (AccessError, MissingError):
            return request.not_found()
        proof = job_sudo.line_ids.proof_ids.filtered(lambda p: p.id == proof_id and p.state != 'draft')
        if not proof:
            return request.not_found()
        stream = request.env['ir.binary']._get_stream_from(proof, 'file', filename=proof.filename)
        return stream.get_response(as_attachment=bool(download))

    @http.route('/my/printing_jobs/<int:job_id>/item/<int:line_id>/decision', type='http', auth='public',
                methods=['POST'], website=True)
    def portal_printing_item_decision(self, job_id, line_id, access_token=None, decision=None, comment=None, **kw):
        try:
            job_sudo = self._job_check(job_id, access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')
        line = job_sudo.line_ids.filtered(lambda l: l.id == line_id)
        if line and line.stage == 'awaiting_approval':
            user = request.env.user
            who = job_sudo.partner_id.name if user._is_public() else user.partner_id.name
            if decision == 'approve':
                line._approve_proof(who)
            elif decision == 'reject':
                line._reject_proof((comment or '').strip() or _('Changes requested'), who)
        query = 'message=%s&item=%s' % (decision or '', line_id)
        if access_token:
            query += '&access_token=%s' % access_token
        return request.redirect('/my/printing_jobs/%s?%s#item-%s' % (job_sudo.id, query, line_id))
