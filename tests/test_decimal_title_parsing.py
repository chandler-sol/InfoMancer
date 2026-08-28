import unittest

from app.scanner import clean_words, movie_release_title, parse_title


class DecimalTitleParsingTests(unittest.TestCase):
    def test_clean_words_preserves_decimal_punctuation(self):
        self.assertEqual(clean_words("Jackass.3.5"), "Jackass 3.5")
        self.assertEqual(clean_words("Movie.Title"), "Movie Title")

    def test_movie_release_title_keeps_decimal_sequel_number(self):
        self.assertEqual(
            movie_release_title("Jackass.3.5.(2011).1080p.BluRay"),
            "Jackass 3.5 (2011)",
        )

    def test_folder_title_with_decimal_and_year_stays_intact(self):
        parsed = parse_title("Jackass 3.5 (2011)")

        self.assertEqual(parsed.title, "Jackass 3.5")
        self.assertEqual(parsed.year, 2011)


if __name__ == "__main__":
    unittest.main()
