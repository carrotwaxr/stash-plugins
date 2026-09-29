"""Offline tests for the image host allowlist (no network)."""

import pytest

import image_search


@pytest.mark.parametrize("url,source", [
    ("https://www.babepedia.com/pics/Jane%20Doe.jpg", "Babepedia"),
    ("https://cdni.pornpics.com/1280/1/2/12345678/12345678_001_abcd.jpg", "PornPics"),
    ("https://thumbs.freeones.com/a/b/c.jpg", "FreeOnes"),
    ("https://cdn.elitebabes.com/content/123456/image.jpg", "EliteBabes"),
    ("https://www.boobpedia.com/wiki/images/a/ab/Jane.jpg", "Boobpedia"),
    ("https://www.javdatabase.com/idolimages/full/jane.webp", "JavDatabase"),
    ("https://example.com/photo.jpg", "DuckDuckGo"),
    ("http://images.example.co.uk/photo.jpg", "DuckDuckGo"),
    ("https://8.8.8.8/photo.jpg", "DuckDuckGo"),
])
def test_allowed(url, source):
    assert image_search.is_allowed_image_url(url, source)


@pytest.mark.parametrize("url,source", [
    # Known sources are pinned to their own hosts
    ("https://example.com/photo.jpg", "Babepedia"),
    ("https://www.babepedia.com.evil.com/pics/x.jpg", "Babepedia"),
    ("https://www.babepedia.com@10.0.0.4/pics/x.jpg", "Babepedia"),
    ("http://10.0.0.4:6969/graphql", "PornPics"),
    # Open-web sources may not point at this machine or the LAN
    ("http://localhost:9999/graphql", "DuckDuckGo"),
    ("http://127.0.0.1/x.jpg", "DuckDuckGo"),
    ("http://10.0.0.4:8080/x.jpg", "DuckDuckGo"),
    ("http://192.168.1.1/x.jpg", "DuckDuckGo"),
    ("http://169.254.169.254/latest/meta-data/", "DuckDuckGo"),
    ("http://[::1]/x.jpg", "DuckDuckGo"),
    ("http://[::ffff:10.0.0.4]/x.jpg", "DuckDuckGo"),
    ("http://nas/x.jpg", "DuckDuckGo"),
    ("http://nas.local/x.jpg", "DuckDuckGo"),
    ("http://router.lan/x.jpg", "DuckDuckGo"),
    ("http://167772164/x.jpg", "DuckDuckGo"),
    ("http://127.1:9999/graphql", "DuckDuckGo"),
    ("http://10.1/x.jpg", "DuckDuckGo"),
    ("http://0x7f.0.0.1/x.jpg", "DuckDuckGo"),
    ("http://0x7f.0x1/x.jpg", "DuckDuckGo"),
    ("http://127.0.0.1./x.jpg", "DuckDuckGo"),
    # Non-HTTP schemes and unknown sources
    ("file:///etc/passwd", "DuckDuckGo"),
    ("ftp://example.com/x.jpg", "DuckDuckGo"),
    ("https://example.com/x.jpg", "SomethingNew"),
    ("https://example.com/x.jpg", None),
    ("", "DuckDuckGo"),
    ("http://[not-an-ip/x.jpg", "DuckDuckGo"),
])
def test_rejected(url, source):
    assert not image_search.is_allowed_image_url(url, source)


def test_drop_disallowed_hosts_checks_thumbnail_too():
    results = [
        {"image": "https://example.com/a.jpg", "thumbnail": "https://example.com/a_t.jpg", "source": "DuckDuckGo"},
        {"image": "https://example.com/b.jpg", "thumbnail": "http://10.0.0.4/b_t.jpg", "source": "DuckDuckGo"},
        {"image": "http://10.0.0.4/c.jpg", "thumbnail": "https://example.com/c_t.jpg", "source": "DuckDuckGo"},
        {"image": "https://www.babepedia.com/pics/d.jpg", "thumbnail": "", "source": "Babepedia"},
    ]
    kept, dropped = image_search.drop_disallowed_hosts(results, "test")
    assert [r["image"] for r in kept] == [
        "https://example.com/a.jpg",
        "https://www.babepedia.com/pics/d.jpg",
    ]
    assert dropped == 2
