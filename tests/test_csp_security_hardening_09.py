import re
from types import SimpleNamespace
import unittest

from app.routes.security_hardening import CSP_META, _harden_template_source, _nonce


class CSPHardening09Tests(unittest.TestCase):
    def test_nonce_is_stable_within_request_and_unique_between_requests(self):
        first = SimpleNamespace(state=SimpleNamespace())
        second = SimpleNamespace(state=SimpleNamespace())

        first_nonce = _nonce(first)
        self.assertTrue(first_nonce)
        self.assertEqual(_nonce(first), first_nonce)
        self.assertNotEqual(_nonce(second), first_nonce)

    def test_hardening_adds_one_nonce_to_script_and_style(self):
        source = "<html><head></head><body><script>ok()</script><style>.x{}</style></body></html>"
        hardened = _harden_template_source(source)

        self.assertEqual(hardened.count('<script nonce="{{ csp_nonce(request) }}"'), 1)
        self.assertEqual(hardened.count('<style nonce="{{ csp_nonce(request) }}"'), 1)
        self.assertEqual(hardened.casefold().count('http-equiv="content-security-policy"'), 1)

    def test_existing_nonce_is_not_duplicated(self):
        source = '<html><head></head><body><script nonce="existing">ok()</script><style nonce="existing">.x{}</style></body></html>'
        hardened = _harden_template_source(source)

        self.assertIn('<script nonce="existing">', hardened)
        self.assertIn('<style nonce="existing">', hardened)
        self.assertNotIn('<script nonce="{{ csp_nonce(request) }}" nonce=', hardened)
        self.assertNotIn('<style nonce="{{ csp_nonce(request) }}" nonce=', hardened)

    def test_existing_csp_meta_is_not_duplicated(self):
        source = '<html><head><meta http-equiv="Content-Security-Policy" content="default-src \'self\'"></head><body></body></html>'
        hardened = _harden_template_source(source)

        self.assertEqual(hardened.casefold().count('http-equiv="content-security-policy"'), 1)

    def test_every_inline_script_tag_gets_a_nonce(self):
        source = "<html><head></head><body><script>one()</script><script type=\"module\">two()</script></body></html>"
        hardened = _harden_template_source(source)
        script_tags = re.findall(r"<script[^>]*>", hardened, flags=re.IGNORECASE)

        self.assertEqual(len(script_tags), 2)
        self.assertTrue(all("nonce=" in tag.casefold() for tag in script_tags))

    def test_policy_uses_same_request_nonce_expression_as_tags(self):
        hardened = _harden_template_source("<html><head></head><body><script>ok()</script></body></html>")
        nonce_expression = "{{ csp_nonce(request) }}"

        self.assertIn(f"'nonce-{nonce_expression}'", CSP_META)
        self.assertIn(f'nonce="{nonce_expression}"', hardened)
        self.assertIn("script-src-attr 'none'", CSP_META)
        self.assertIn("object-src 'none'", CSP_META)


if __name__ == "__main__":
    unittest.main()
