import base64
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

PNG = base64.b64encode(bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082'))


@tagged('post_install', '-at_install')
class TestPrintingFlow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'Gulf Horizon Events', 'email': 'gh@example.com'})
        cls.flex = cls.env['product.product'].create({'name': 'Flex 440 gsm', 'type': 'product'})
        cls.card = cls.env['product.product'].create({'name': 'Art card 400 gsm', 'type': 'product'})
        cls.banner = cls.env['product.product'].create({
            'name': 'Roll-up Banner', 'type': 'product', 'kma_is_printable': True,
            'kma_print_process': 'large_format'})
        cls.cards = cls.env['product.product'].create({
            'name': 'Business Cards', 'type': 'product', 'kma_is_printable': True,
            'kma_print_process': 'digital'})
        cls.mugs = cls.env['product.product'].create({
            'name': 'Plain Mug Stock', 'type': 'product', 'kma_is_printable': True,
            'kma_requires_artwork': False})
        cls.service = cls.env['product.product'].create({'name': 'Design fee', 'type': 'service'})
        for prod, comp in ((cls.banner, cls.flex), (cls.cards, cls.card)):
            cls.env['mrp.bom'].create({
                'product_tmpl_id': prod.product_tmpl_id.id, 'product_qty': 1,
                'bom_line_ids': [(0, 0, {'product_id': comp.id, 'product_qty': 1})]})

    def _order(self, products):
        so = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'order_line': [(0, 0, {'product_id': p.id, 'product_uom_qty': 10}) for p in products]})
        so.action_confirm()
        return so

    def _add_proof(self, line):
        return self.env['kma.printing.proof'].create({'job_line_id': line.id, 'file': PNG, 'filename': 'proof.png'})

    def _finish_mo(self, mo):
        mo.qty_producing = mo.product_qty
        mo._set_qty_producing()
        mo.move_raw_ids.picked = True
        mo.button_mark_done()

    def test_one_job_many_items(self):
        so = self._order(self.banner | self.cards | self.service)
        job = so.printing_job_ids
        self.assertEqual(len(job), 1)
        self.assertEqual(len(job.line_ids), 2, 'Only printable products become items')
        self.assertEqual(set(job.line_ids.mapped('stage')), {'artwork_pending'})
        self.assertEqual(job.stage, 'artwork_pending')
        self.assertIn('Upload a proof for', job.status_banner)

    def test_item_wise_artwork_and_production(self):
        so = self._order(self.banner | self.cards)
        job = so.printing_job_ids
        banner = job.line_ids.filtered(lambda l: l.product_id == self.banner)
        cards = job.line_ids.filtered(lambda l: l.product_id == self.cards)

        with self.assertRaises(UserError):
            job.action_send_proofs()  # no proof on any item

        # banner: proof, send, approve, produce -- while cards still waits for artwork
        self._add_proof(banner)
        self.assertEqual(job.to_send_count, 1)
        job.action_send_proofs()
        self.assertEqual(banner.stage, 'awaiting_approval')
        self.assertEqual(cards.stage, 'artwork_pending')
        self.assertEqual(job.stage, 'artwork_pending', 'Job follows its slowest item')

        banner._approve_proof('Customer')
        self.assertEqual(banner.stage, 'approved')
        self.assertEqual(job.to_produce_count, 1)
        job.action_create_mos()
        self.assertEqual(banner.stage, 'in_production')
        self.assertEqual(banner.production_id.bom_id.product_tmpl_id, self.banner.product_tmpl_id)
        self.assertEqual(banner.production_id.printing_job_id, job)
        self.assertFalse(cards.production_id, 'Cards have no approved artwork, so no MO yet')
        with self.assertRaises(UserError):
            cards.action_create_mo()

        # cards: rejection loop, item-level only
        self._add_proof(cards)
        cards.action_send_proof()
        cards._reject_proof('Make the logo bigger', 'Customer')
        self.assertEqual(cards.stage, 'artwork_pending')
        self.assertEqual(banner.stage, 'in_production', 'Rejecting one item does not touch the others')
        self.assertIn('Make the logo bigger', job.status_banner)
        self._add_proof(cards)
        self.assertEqual(cards.current_proof_id.version, 2)
        self.assertEqual(banner.current_proof_id.version, 1, 'Proof versions are counted per item')
        cards.action_send_proof()
        cards.action_approve_proof()
        cards.action_create_mo()
        self.assertEqual(job.stage, 'in_production')
        self.assertEqual(job.production_count, 2)
        self.assertEqual(job.approved_count, 2)

        # production and QC per item
        self._finish_mo(banner.production_id)
        self.assertEqual(banner.stage, 'qc')
        self.assertEqual(len(banner.qc_line_ids), 4)
        self.assertEqual(cards.stage, 'in_production')
        with self.assertRaises(UserError):
            banner.action_qc_pass()
        banner.qc_line_ids.write({'done': True})
        banner.action_qc_pass()
        self.assertEqual(banner.stage, 'ready')
        self.assertEqual(job.stage, 'in_production')

        self._finish_mo(cards.production_id)
        cards.qc_line_ids.write({'done': True})
        cards.action_qc_pass()
        self.assertEqual(job.stage, 'ready')
        self.assertEqual(job.ready_count, 2)
        self.assertTrue(job.date_ready)
        self.assertIn('o_kma_step_current', job.timeline_html)

        picking = so.picking_ids
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.move_ids.picked = True
        picking.button_validate()
        self.assertEqual(job.stage, 'delivered')
        self.assertEqual(set(job.line_ids.mapped('stage')), {'delivered'})
        self.assertEqual(job.deadline_state, 'done')

    def test_item_without_artwork_starts_approved(self):
        so = self._order(self.banner | self.mugs)
        job = so.printing_job_ids
        mug = job.line_ids.filtered(lambda l: l.product_id == self.mugs)
        self.assertEqual(mug.stage, 'approved')
        self.assertEqual(job.stage, 'artwork_pending')

    def test_reject_wizard_and_partial_timeline(self):
        so = self._order(self.banner | self.cards)
        job = so.printing_job_ids
        for line in job.line_ids:
            self._add_proof(line)
        job.action_send_proofs()
        self.assertEqual(job.stage, 'awaiting_approval')
        first = job.line_ids[0]
        wiz = self.env['kma.proof.reject.wizard'].create({'line_ids': [(6, 0, first.ids)], 'reason': 'Wrong colour'})
        wiz.action_confirm()
        self.assertEqual(first.stage, 'artwork_pending')
        self.assertEqual(first.last_rejected_proof_id.customer_comment, 'Wrong colour')
        self.assertIn('1 of 2 items', job.timeline_html)
        with self.assertRaises(UserError):
            first.last_rejected_proof_id.unlink()

    def test_cancel_and_deadline(self):
        so = self._order(self.banner)
        job = so.printing_job_ids
        today = fields.Date.context_today(job)
        job.date_deadline = today - timedelta(days=2)
        self.assertEqual(job.deadline_state, 'overdue')
        self.assertIn('Overdue by 2', job.status_banner)
        job.date_deadline = today + timedelta(days=1)
        self.assertEqual(job.deadline_state, 'due_soon')
        self.env['kma.printing.job']._cron_refresh_deadline_state()
        so._action_cancel()
        self.assertEqual(job.stage, 'cancel')
        self.assertEqual(job.line_ids.stage, 'cancel')

    def test_portal_url_and_report(self):
        so = self._order(self.banner | self.cards)
        job = so.printing_job_ids
        self.assertEqual(job.access_url, '/my/printing_jobs/%s' % job.id)
        html = self.env['ir.actions.report']._render_qweb_html('kma_printing_job.report_job_ticket', job.ids)[0]
        self.assertIn(job.name.encode(), html)
        self.assertIn(b'Business Cards', html)
