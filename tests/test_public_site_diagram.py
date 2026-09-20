"""Diagram-first page contracts approved after the text-heavy first design."""
import re
import unittest
import test_public_site_design as design


def visible_text(element):
    if isinstance(element, str):
        return element
    if element.tag in {'head', 'svg'}:
        return ''
    children = element.children
    if element.tag == 'details' and 'open' not in element.attrs:
        children = [child for child in children if not isinstance(child, str) and child.tag == 'summary']
    return ' '.join(visible_text(child) for child in children)


class DiagramTests(unittest.TestCase):
    setUp = design.PublicDesignTests.setUp
    pages = design.PublicDesignTests.pages

    def test_hero_has_ordered_visual_flow_with_native_playback_control(self):
        for page in self.pages():
            illustrations = page.find('aside', 'workflow-illustration')
            self.assertEqual(len(illustrations), 1, 'hero needs a flow diagram, not a terminal transcript')
            diagram = illustrations[0]
            nodes = diagram.find('li', 'diagram-node')
            self.assertEqual(len(nodes), 6)
            self.assertEqual([n.attrs.get('data-stage') for n in nodes], list(design.STAGES))
            self.assertTrue(all(len(n.find('svg')) == 1 for n in nodes))
            toggle = diagram.find('input')
            self.assertEqual(len(toggle), 1)
            self.assertEqual(toggle[0].attrs.get('type'), 'checkbox')
            self.assertEqual(toggle[0].attrs.get('id'), 'motion-toggle')
            self.assertIn('checked', toggle[0].attrs)
            self.assertEqual(diagram.find('label')[0].attrs.get('for'), 'motion-toggle')
            self.assertTrue(diagram.find('div', 'request-node'))
            self.assertTrue(diagram.find('div', 'diagram-delivery'))

    def test_details_are_opt_in_and_default_reading_is_compact(self):
        for page, budget in zip(self.pages(), (1700, 3400)):
            for cls in ('workflow-details', 'optional-details', 'delivery-details', 'updates-details'):
                groups = page.find('details', cls)
                self.assertEqual(len(groups), 1, cls)
                self.assertNotIn('open', groups[0].attrs)
            self.assertEqual(len(page.find('details', 'install-note-details')), 2)
            for stage in page.find('details', 'stage'):
                self.assertNotIn('open', stage.attrs)
            self.assertLess(len(re.sub(r'\s+', ' ', visible_text(page)).strip()), budget)

    def test_capabilities_are_distinct_illustrations_with_examples_inside_details(self):
        for page in self.pages():
            cards = page.find('div', 'scenes')[0].find('article')
            pictures = []
            for card in cards:
                art = card.find('svg', 'scene-art')
                self.assertEqual(len(art), 1)
                pictures.append(tuple(path.attrs.get('d') for path in art[0].find('path')))
                self.assertTrue(card.find('details')[0].find('code', 'example-command'))
            self.assertEqual(len(set(pictures)), 6)

    def test_svg_is_fixed_decorative_inline_content_not_an_execution_channel(self):
        for page in self.pages():
            svgs = page.find('svg')
            self.assertGreaterEqual(len(svgs), 17)
            for svg in svgs:
                self.assertEqual(svg.attrs.get('aria-hidden'), 'true')
                self.assertEqual(svg.attrs.get('focusable'), 'false')
                self.assertFalse(svg.find('script') or svg.find('foreignobject') or svg.find('image'))
                for element in [svg, *svg.find()]:
                    self.assertFalse(any(k.lower().startswith('on') or k in {'href', 'xlink:href'}
                                         for k in element.attrs))
