from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.timezones import timezone_groups


class TimezoneChoiceTests(unittest.TestCase):
    def test_common_choices_have_friendly_labels_and_iana_values(self) -> None:
        groups = timezone_groups(datetime(2026, 7, 24, tzinfo=timezone.utc))
        self.assertEqual(groups[0][0], "Common choices")
        choices = dict(groups[0][1])
        self.assertIn("America/New_York", choices)
        self.assertIn("Eastern Time", choices["America/New_York"])
        self.assertIn("UTC−04:00 currently", choices["America/New_York"])

    def test_every_choice_uses_a_unique_iana_value(self) -> None:
        groups = timezone_groups(datetime(2026, 7, 24, tzinfo=timezone.utc))
        values = [value for _group, choices in groups for value, _label in choices]
        self.assertEqual(len(values), len(set(values)))
        self.assertIn("Europe/London", values)
        self.assertIn("Australia/Sydney", values)


if __name__ == "__main__":
    unittest.main()
