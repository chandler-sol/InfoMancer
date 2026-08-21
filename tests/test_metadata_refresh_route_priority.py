import unittest

from app.routes import ROUTER_BUILDERS


class MetadataRefreshRoutePriorityTests(unittest.TestCase):
    def test_single_title_metadata_router_is_registered_first(self):
        first = ROUTER_BUILDERS[0]
        self.assertEqual(first.__module__, "app.routes.title_metadata_async")


if __name__ == "__main__":
    unittest.main()
