from odoo import fields, models

PRINT_PROCESSES = [
    ('digital', 'Digital'),
    ('offset', 'Offset'),
    ('large_format', 'Large Format'),
    ('sublimation', 'Sublimation'),
    ('signage', 'Signage'),
    ('gift', 'Corporate Gift'),
    ('design', 'Design Service'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    kma_is_printable = fields.Boolean(
        string='Printable Product',
        help='Confirming a sales order with this product creates a printing job.')
    kma_print_process = fields.Selection(PRINT_PROCESSES, string='Print Process')
    kma_requires_artwork = fields.Boolean(
        string='Requires Artwork Approval', default=True,
        help='Customer must approve a proof before production starts.')
