#!/usr/bin/env python3
"""
Tests for Performer Image Search scrapers.
Run with: python test_image_search.py

Tests verify:
1. Each scraper returns results for known performers
2. Image URLs are highest quality available (not thumbnails)
3. Results have required fields
4. Deduplication works
"""

import sys
import re

# Import the module to test
import image_search


def test_result_structure(result, source_name):
    """Verify a result has all required fields."""
    required_fields = ["thumbnail", "image", "title", "source", "width", "height"]
    for field in required_fields:
        assert field in result, f"{source_name}: Missing field '{field}'"
    assert result["source"] == source_name, f"Expected source '{source_name}', got '{result['source']}'"
    assert result["image"], f"{source_name}: Empty image URL"
    assert result["thumbnail"], f"{source_name}: Empty thumbnail URL"


def test_babepedia():
    """Test Babepedia scraper with a known female performer."""
    print("\n=== Testing Babepedia ===")
    results = image_search.search_babepedia("Kayden Kross", max_results=5)

    assert len(results) > 0, "Babepedia: No results found for Kayden Kross"
    print(f"  Found {len(results)} results")

    for result in results[:2]:
        test_result_structure(result, "Babepedia")
        assert "_thumb" not in result["image"], f"Babepedia: Image URL contains thumbnail pattern: {result['image']}"
        assert result["image"].endswith(".jpg"), f"Babepedia: Expected .jpg image: {result['image']}"
        print(f"  OK: {result['image'][:60]}...")

    print("  PASSED")


def test_pornpics():
    """Test PornPics scraper - should work for male performers too."""
    print("\n=== Testing PornPics ===")

    results = image_search.search_pornpics("Kayden Kross", max_results=10, max_galleries=2)
    assert len(results) > 0, "PornPics: No results found for Kayden Kross"
    print(f"  Female (Kayden Kross): {len(results)} results")

    for result in results:
        test_result_structure(result, "PornPics")
        if "/models/" not in result["image"]:
            assert "/1280/" in result["image"], f"PornPics: Gallery image should use /1280/: {result['image']}"
            assert "/460/" not in result["image"], f"PornPics: Gallery image contains thumbnail /460/: {result['image']}"

    results_male = image_search.search_pornpics("Jax Slayher", max_results=5, max_galleries=2)
    if len(results_male) > 0:
        print(f"  Male (Jax Slayher): {len(results_male)} results")
    else:
        print("  Male (Jax Slayher): No results (may not be on site)")

    print("  PASSED")


def test_freeones():
    """Test FreeOnes scraper - should work for male and trans performers."""
    print("\n=== Testing FreeOnes ===")

    results = image_search.search_freeones("Kayden Kross", max_results=5, max_galleries=2)
    assert len(results) > 0, "FreeOnes: No results found for Kayden Kross"
    print(f"  Female (Kayden Kross): {len(results)} results")

    for result in results[:2]:
        test_result_structure(result, "FreeOnes")
        assert "freeones.com" in result["image"], f"FreeOnes: Expected freeones.com URL: {result['image']}"

    results_male = image_search.search_freeones("Jax Slayher", max_results=5, max_galleries=2)
    if len(results_male) > 0:
        print(f"  Male (Jax Slayher): {len(results_male)} results")
    else:
        print("  Male (Jax Slayher): No results (may not be on site)")

    results_trans = image_search.search_freeones("Emma Rose", max_results=5, max_galleries=2)
    if len(results_trans) > 0:
        print(f"  Trans (Emma Rose): {len(results_trans)} results")
    else:
        print("  Trans (Emma Rose): No results (may not be on site)")

    print("  PASSED")


def test_elitebabes():
    """Test EliteBabes scraper."""
    print("\n=== Testing EliteBabes ===")
    results = image_search.search_elitebabes("Kayden Kross", max_results=5, max_galleries=2)

    if len(results) == 0:
        print("  SKIPPED: No results found (performer may not be on EliteBabes)")
        return

    print(f"  Found {len(results)} results")

    for result in results[:2]:
        test_result_structure(result, "EliteBabes")
        assert "_w400" not in result["image"], f"EliteBabes: Image URL contains thumbnail _w400: {result['image']}"
        assert "_w200" not in result["image"], f"EliteBabes: Image URL contains thumbnail _w200: {result['image']}"
        print(f"  OK: {result['image'][:60]}...")

    print("  PASSED")


def test_boobpedia():
    """Test Boobpedia scraper."""
    print("\n=== Testing Boobpedia ===")
    results = image_search.search_boobpedia("Kayden Kross", max_results=5)

    if len(results) == 0:
        print("  SKIPPED: No results found (performer may not be on Boobpedia)")
        return

    print(f"  Found {len(results)} results")

    for result in results[:2]:
        test_result_structure(result, "Boobpedia")
        assert "/thumb/" not in result["image"], f"Boobpedia: Image URL contains /thumb/: {result['image']}"
        print(f"  OK: {result['image'][:60]}...")

    print("  PASSED")


def test_javdatabase():
    """Test JavDatabase scraper for JAV performers."""
    print("\n=== Testing JavDatabase ===")
    results = image_search.search_javdatabase("Rei Kamiki", max_results=10, max_pages=1)

    assert len(results) > 0, "JavDatabase: No results found for Rei Kamiki"
    print(f"  Found {len(results)} results")

    for result in results[:3]:
        test_result_structure(result, "JavDatabase")
        if "idolimages" in result["image"]:
            assert "/full/" in result["image"], f"JavDatabase: Idol image should use /full/: {result['image']}"
            assert "/thumb/" not in result["image"], f"JavDatabase: Idol image contains /thumb/: {result['image']}"
        elif "covers" in result["image"]:
            assert "/full/" in result["image"], f"JavDatabase: Cover should use /full/: {result['image']}"
        print(f"  OK: {result['image'][:60]}...")

    results2 = image_search.search_javdatabase("Yua Mikami", max_results=5, max_pages=1)
    assert len(results2) > 0, "JavDatabase: No results found for Yua Mikami"
    print(f"  Also found {len(results2)} results for Yua Mikami")

    print("  PASSED")


def test_duckduckgo():
    """Test DuckDuckGo Images scraper (replaced Bing for NSFW support)."""
    print("\n=== Testing DuckDuckGo ===")
    results = image_search.search_duckduckgo_images("Kayden Kross pornstar", size="Large", max_results=5)

    assert len(results) > 0, "DuckDuckGo: No results found"
    print(f"  Found {len(results)} results")

    for result in results[:2]:
        test_result_structure(result, "DuckDuckGo")
        assert result["image"].startswith("http"), f"DuckDuckGo: Invalid URL: {result['image']}"
        print(f"  OK: {result['image'][:60]}...")

    print("  PASSED")


def test_single_source():
    """Test the single source search function."""
    print("\n=== Testing search_single_source ===")

    sources_to_test = ["babepedia", "pornpics", "freeones", "javdatabase", "duckduckgo"]

    for source in sources_to_test:
        if source == "javdatabase":
            results = image_search.search_single_source(
                source=source,
                name="Rei Kamiki",
                query="Rei Kamiki",
            )
        else:
            results = image_search.search_single_source(
                source=source,
                name="Kayden Kross",
                query="Kayden Kross pornstar",
            )
        print(f"  {source}: {len(results)} results")

    print("  PASSED")


def test_deduplication():
    """Test that deduplication works within a source."""
    print("\n=== Testing Deduplication ===")

    results = image_search.search_freeones("Kayden Kross", max_results=50, max_galleries=5)

    urls = [r["image"] for r in results]
    unique_urls = set(urls)

    assert len(urls) == len(unique_urls), f"Found {len(urls) - len(unique_urls)} duplicate URLs"
    print(f"  {len(results)} results, all unique")

    print("  PASSED")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("Performer Image Search - Scraper Tests")
    print("=" * 60)

    tests = [
        test_babepedia,
        test_pornpics,
        test_freeones,
        test_elitebabes,
        test_boobpedia,
        test_javdatabase,
        test_duckduckgo,
        test_single_source,
        test_deduplication,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
