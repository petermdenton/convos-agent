#!/usr/bin/env python3
"""Unit tests for the sutochno skill's pure logic.

No browser, network, or Patchright required — the `patchright` import in
search_sutochno.py is lazy (inside `search()`), so importing the module is safe.

Run with either:
    python3 -m unittest discover -s skills/sutochno/tests
    python3 -m pytest skills/sutochno/tests
"""

import importlib.util
import os
import unittest

# Load search_sutochno.py by path (it lives in ../scripts, not on sys.path).
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "..", "scripts", "search_sutochno.py")
_spec = importlib.util.spec_from_file_location("search_sutochno", _SCRIPT)
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)


class HelpersExistTests(unittest.TestCase):
    """Guard the public parse/scrape helpers exist and are callable."""

    def test_expected_functions_present(self):
        for fn in (
            "build_city_url",
            "parse_price",
            "parse_rating",
            "listing_id_from_url",
            "normalize_listing",
            "is_skeleton",
            "enrich_listings",
            "search",
        ):
            self.assertTrue(hasattr(ss, fn), f"missing helper: {fn}")
            self.assertTrue(callable(getattr(ss, fn)), f"not callable: {fn}")

    def test_extract_js_targets_known_selectors(self):
        js = ss.EXTRACT_JS
        self.assertIn("card-content__object-type", js)
        self.assertIn("card-prices", js)
        self.assertIn("card-content__address", js)


class BuildCityUrlTests(unittest.TestCase):
    def test_bare_subdomain(self):
        self.assertEqual(ss.build_city_url("tbilisi"), "https://tbilisi.sutochno.ru/")
        self.assertEqual(ss.build_city_url("batumi"), "https://batumi.sutochno.ru/")

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(ss.build_city_url("  Tbilisi  "), "https://tbilisi.sutochno.ru/")

    def test_full_host_passthrough(self):
        self.assertEqual(
            ss.build_city_url("kutaisi.sutochno.ru"), "https://kutaisi.sutochno.ru/"
        )

    def test_strips_scheme_and_path(self):
        self.assertEqual(
            ss.build_city_url("https://batumi.sutochno.ru/foo"),
            "https://batumi.sutochno.ru/",
        )

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            ss.build_city_url("")
        with self.assertRaises(ValueError):
            ss.build_city_url(None)


class ParsePriceTests(unittest.TestCase):
    def test_real_sutochno_string(self):
        # The literal SERP string — thousands separator is a non-breaking space.
        self.assertEqual(ss.parse_price("4 890 ₽ за сутки"), 4890)

    def test_five_digit_price(self):
        self.assertEqual(ss.parse_price("12 500 ₽ за сутки"), 12500)

    def test_nbsp_separator(self):
        # Explicit non-breaking space (U+00A0) — the real on-page separator.
        self.assertEqual(ss.parse_price("7 250 ₽ за сутки"), 7250)

    def test_no_ruble_symbol(self):
        self.assertIsNone(ss.parse_price("Цена по запросу"))

    def test_empty(self):
        self.assertIsNone(ss.parse_price(""))
        self.assertIsNone(ss.parse_price(None))


class ParseRatingTests(unittest.TestCase):
    def test_real_sutochno_string(self):
        rating, reviews = ss.parse_rating("10 отзывов 9,8 (10)")
        self.assertEqual(rating, 9.8)
        self.assertEqual(reviews, 10)

    def test_singular_review(self):
        rating, reviews = ss.parse_rating("1 отзыв 8,5")
        self.assertEqual(rating, 8.5)
        self.assertEqual(reviews, 1)

    def test_no_rating(self):
        rating, reviews = ss.parse_rating("Нет отзывов")
        self.assertIsNone(rating)
        self.assertIsNone(reviews)

    def test_empty(self):
        self.assertEqual(ss.parse_rating(""), (None, None))
        self.assertEqual(ss.parse_rating(None), (None, None))


class ListingIdTests(unittest.TestCase):
    def test_simple_id(self):
        self.assertEqual(
            ss.listing_id_from_url("https://tbilisi.sutochno.ru/1234567"), "1234567"
        )

    def test_hotels_path_with_query(self):
        self.assertEqual(
            ss.listing_id_from_url("https://tbilisi.sutochno.ru/hotels/9876543?x=1"),
            "9876543",
        )

    def test_trailing_slash(self):
        self.assertEqual(
            ss.listing_id_from_url("https://batumi.sutochno.ru/555000/"), "555000"
        )

    def test_no_id(self):
        self.assertIsNone(ss.listing_id_from_url("https://batumi.sutochno.ru/about"))
        self.assertIsNone(ss.listing_id_from_url(None))


class NormalizeListingTests(unittest.TestCase):
    def test_full_card(self):
        raw = {
            "name": "Уютная студия в центре",
            "href": "https://tbilisi.sutochno.ru/1234567",
            "priceText": "4 890 ₽ за сутки",
            "ratingText": "10 отзывов 9,8 (10)",
            "area": "Тбилиси, Старый город",
        }
        l = ss.normalize_listing(raw)
        self.assertEqual(l["id"], "1234567")
        self.assertEqual(l["name"], "Уютная студия в центре")
        self.assertEqual(l["rub_per_night"], 4890)
        self.assertEqual(l["rating"], 9.8)
        self.assertEqual(l["reviews"], 10)
        self.assertEqual(l["area"], "Тбилиси, Старый город")
        self.assertEqual(l["url"], "https://tbilisi.sutochno.ru/1234567")

    def test_missing_fields(self):
        l = ss.normalize_listing({"href": "https://batumi.sutochno.ru/9001"})
        self.assertEqual(l["id"], "9001")
        self.assertIsNone(l["name"])
        self.assertIsNone(l["rub_per_night"])
        self.assertIsNone(l["rating"])


class SkeletonTests(unittest.TestCase):
    def test_all_null_is_skeleton(self):
        self.assertTrue(ss.is_skeleton({"id": None, "name": None, "url": None}))

    def test_any_identity_field_is_not_skeleton(self):
        self.assertFalse(ss.is_skeleton({"id": "1", "name": None, "url": None}))
        self.assertFalse(ss.is_skeleton({"id": None, "name": "Студия", "url": None}))
        self.assertFalse(ss.is_skeleton({"id": None, "name": None, "url": "u"}))


class EnrichListingsTests(unittest.TestCase):
    def _raw(self):
        return [
            {"name": "A", "href": "https://tbilisi.sutochno.ru/1001",
             "priceText": "4 890 ₽ за сутки", "ratingText": "10 отзывов 9,8 (10)"},
            {"name": "A-dup", "href": "https://tbilisi.sutochno.ru/1001",  # dup id 1001
             "priceText": "9 000 ₽ за сутки", "ratingText": "2 отзыва 7,0 (2)"},
            {"name": "B", "href": "https://tbilisi.sutochno.ru/1002",
             "priceText": "3 000 ₽ за сутки", "ratingText": "5 отзывов 9,8 (5)"},
            {"name": "C", "href": "https://tbilisi.sutochno.ru/1003",
             "priceText": "12 000 ₽ за сутки", "ratingText": "1 отзыв 6,0"},
            {"name": None, "href": None, "priceText": None, "ratingText": None},  # skeleton
        ]

    def test_dedupe_and_drop_skeleton(self):
        out = ss.enrich_listings(self._raw())
        ids = [l["id"] for l in out]
        self.assertEqual(ids.count("1001"), 1)   # deduped
        self.assertNotIn(None, ids)              # skeleton dropped
        self.assertEqual(len(out), 3)

    def test_sort_rating_desc_then_price_asc(self):
        out = ss.enrich_listings(self._raw())
        # Two 9.8s (1001 @ 4890 and 1002 @ 3000): cheaper first; then 6.0 (1003).
        self.assertEqual([l["id"] for l in out], ["1002", "1001", "1003"])

    def test_min_rating_filter(self):
        out = ss.enrich_listings(self._raw(), min_rating=9.0)
        self.assertEqual({l["id"] for l in out}, {"1001", "1002"})

    def test_max_price_filter(self):
        out = ss.enrich_listings(self._raw(), max_price=5000)
        self.assertEqual({l["id"] for l in out}, {"1001", "1002"})  # 12000 dropped

    def test_limit(self):
        out = ss.enrich_listings(self._raw(), limit=1)
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
