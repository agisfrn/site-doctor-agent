from urllib.request import urlopen

from tests.crawl_site import PAGES


def test_crawl_fixture_serves_its_routes(crawl_url):
    robots = urlopen(crawl_url + "/robots.txt").read().decode()
    assert robots.count("Disallow: /private/") == 1 and f"Sitemap: {crawl_url}/" in robots
    assert urlopen(crawl_url + "/products/item-29").status == 200
    assert len(PAGES) == 34


def test_demo_site_has_a_blog(demo_url):
    assert "Journal" in urlopen(demo_url + "/").read().decode()
    assert urlopen(demo_url + "/care/deep-guide.html").status == 200
    assert urlopen(demo_url + "/files/care-sheet.pdf").read()[:5] == b"%PDF-"
