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
    results = image_search.search_babepedia("Mia Malkova", max_results=5)

    assert len(results) > 0, "Babepedia: No results found for Mia Malkova"
    print(f"  Found {len(results)} results")

    for result in results[:2]:
        test_result_structure(result, "Babepedia")
        # Verify we're getting full-size, not thumbnails
        assert "_thumb" not in result["image"], f"Babepedia: Image URL contains thumbnail pattern: {result['image']}"
        assert result["image"].endswith(".jpg"), f"Babepedia: Expected .jpg image: {result['image']}"
        print(f"  OK: {result['image'][:60]}...")

    print("  PASSED")


def test_pornpics():
    """Test PornPics scraper - should work for male performers too."""
    print("\n=== Testing PornPics ===")

    # Test female performer
    results = image_search.search_pornpics("Mia Malkova", max_results=10, max_galleries=2)
    assert len(results) > 0, "PornPics: No results found for Mia Malkova"
    print(f"  Female (Mia Malkova): {len(results)} results")

    for result in results:
        test_result_structure(result, "PornPics")
        # Profile images use /models/ path, gallery images use /1280/ or /460/
        # Verify gallery images use 1280px, not 460px thumbnails
        if "/models/" not in result["image"]:
            assert "/1280/" in result["image"], f"PornPics: Gallery image should use /1280/: {result['image']}"
            assert "/460/" not in result["image"], f"PornPics: Gallery image contains thumbnail /460/: {result['image']}"

    # Test male performer
    results_male = image_search.search_pornpics("Johnny Sins", max_results=5, max_galleries=2)
    assert len(results_male) > 0, "PornPics: No results found for Johnny Sins (male)"
    print(f"  Male (Johnny Sins): {len(results_male)} results")

    print("  PASSED")


def test_freeones():
    """Test FreeOnes scraper - should work for male and trans performers."""
    print("\n=== Testing FreeOnes ===")

    # Test female performer
    results = image_search.search_freeones("Mia Malkova", max_results=5, max_galleries=2)
    assert len(results) > 0, "FreeOnes: No results found for Mia Malkova"
    print(f"  Female (Mia Malkova): {len(results)} results")

    for result in results[:2]:
        test_result_structure(result, "FreeOnes")
        # FreeOnes CDN - verify we have freeones.com URLs
        assert "freeones.com" in result["image"], f"FreeOnes: Expected freeones.com URL: {result['image']}"

    # Test male performer
    results_male = image_search.search_freeones("Johnny Sins", max_results=5, max_galleries=2)
    assert len(results_male) > 0, "FreeOnes: No results found for Johnny Sins (male)"
    print(f"  Male (Johnny Sins): {len(results_male)} results")

    # Test trans performer
    results_trans = image_search.search_freeones("Daisy Taylor", max_results=5, max_galleries=2)
    assert len(results_trans) > 0, "FreeOnes: No results found for Daisy Taylor (trans)"
    print(f"  Trans (Daisy Taylor): {len(results_trans)} results")

    print("  PASSED")


def test_elitebabes():
    """Test EliteBabes scraper."""
    print("\n=== Testing EliteBabes ===")
    results = image_search.search_elitebabes("Mia Malkova", max_results=5, max_galleries=2)

    # EliteBabes may not have all performers
    if len(results) == 0:
        print("  SKIPPED: No results found (performer may not be on EliteBabes)")
        return

    print(f"  Found {len(results)} results")

    for result in results[:2]:
        test_result_structure(result, "EliteBabes")
        # Verify we're getting full-size, not _w400 thumbnails
        assert "_w400" not in result["image"], f"EliteBabes: Image URL contains thumbnail _w400: {result['image']}"
        assert "_w200" not in result["image"], f"EliteBabes: Image URL contains thumbnail _w200: {result['image']}"
        print(f"  OK: {result['image'][:60]}...")

    print("  PASSED")


def test_boobpedia():
    """Test Boobpedia scraper."""
    print("\n=== Testing Boobpedia ===")
    results = image_search.search_boobpedia("Mia Malkova", max_results=5)

    # Boobpedia may not have all performers
    if len(results) == 0:
        print("  SKIPPED: No results found (performer may not be on Boobpedia)")
        return

    print(f"  Found {len(results)} results")

    for result in results[:2]:
        test_result_structure(result, "Boobpedia")
        # Verify we're getting full wiki images, not thumbnails
        # Full images: /wiki/images/X/XX/file.jpg
        # Thumbnails: /wiki/images/thumb/X/XX/file.jpg/NNNpx-file.jpg
        assert "/thumb/" not in result["image"], f"Boobpedia: Image URL contains /thumb/: {result['image']}"
        print(f"  OK: {result['image'][:60]}...")

    print("  PASSED")


def test_javdatabase():
    """Test JavDatabase scraper for JAV performers."""
    print("\n=== Testing JavDatabase ===")
    results = image_search.search_javdatabase("Yua Mikami", max_results=10, max_pages=1)

    assert len(results) > 0, "JavDatabase: No results found for Yua Mikami"
    print(f"  Found {len(results)} results")

    for result in results[:3]:
        test_result_structure(result, "JavDatabase")

        # Verify we're getting full-size images
        if "idolimages" in result["image"]:
            # Profile images should use /full/, not /thumb/
            assert "/full/" in result["image"], f"JavDatabase: Idol image should use /full/: {result['image']}"
            assert "/thumb/" not in result["image"], f"JavDatabase: Idol image contains /thumb/: {result['image']}"
        elif "covers" in result["image"]:
            # Covers should use /full/, not /thumb/
            assert "/full/" in result["image"], f"JavDatabase: Cover should use /full/: {result['image']}"

        print(f"  OK: {result['image'][:60]}...")

    # Test with another JAV performer to ensure it's not hardcoded
    results2 = image_search.search_javdatabase("Eimi Fukada", max_results=5, max_pages=1)
    assert len(results2) > 0, "JavDatabase: No results found for Eimi Fukada"
    print(f"  Also found {len(results2)} results for Eimi Fukada")

    print("  PASSED")


def test_bing():
    """Test Bing Images scraper."""
    print("\n=== Testing Bing ===")
    results = image_search.search_bing_images("Mia Malkova pornstar", size="Large", max_results=5)

    # Bing should always return something
    assert len(results) > 0, "Bing: No results found"
    print(f"  Found {len(results)} results")

    for result in results[:2]:
        test_result_structure(result, "Bing")
        # Bing returns direct image URLs, verify they're actual image URLs
        assert result["image"].startswith("http"), f"Bing: Invalid URL: {result['image']}"
        print(f"  OK: {result['image'][:60]}...")

    print("  PASSED")


def test_single_source():
    """Test the single source search function."""
    print("\n=== Testing search_single_source ===")

    # Test each source individually
    sources_to_test = ["babepedia", "pornpics", "freeones", "javdatabase", "bing"]

    for source in sources_to_test:
        if source == "javdatabase":
            results = image_search.search_single_source(
                source=source,
                name="Yua Mikami",
                query="Yua Mikami",
            )
        else:
            results = image_search.search_single_source(
                source=source,
                name="Mia Malkova",
                query="Mia Malkova pornstar",
            )
        print(f"  {source}: {len(results)} results")

    print("  PASSED")


def test_deduplication():
    """Test that deduplication works within a source."""
    print("\n=== Testing Deduplication ===")

    # FreeOnes with multiple galleries should not have duplicates
    results = image_search.search_freeones("Mia Malkova", max_results=50, max_galleries=5)

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
        test_bing,
        test_single_source,
        test_deduplication,
    ]

    passed = 0
    failed = 0
    skipped = 0

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
