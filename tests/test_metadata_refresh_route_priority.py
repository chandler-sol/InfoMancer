import unittest

from app.routes import ROUTER_BUILDERS


class MetadataRefreshRoutePriorityTests(unittest.TestCase):
    def test_security_hooks_install_first_and_single_title_refresh_precedes_legacy_titles(self):
        modules = [builder.__module__ for builder in ROUTER_BUILDERS]
        self.assertEqual(modules[0], "app.routes.security_hardening")
        self.assertLess(
            modules.index("app.routes.title_metadata_async"),
            modules.index("app.routes.titles"),
        )


if __name__ == "__main__":
    unittest.main()
