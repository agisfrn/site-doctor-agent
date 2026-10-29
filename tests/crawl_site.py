"""A generated site for crawler tests: robots.txt, a sitemap index, an orphan page only the
sitemap knows about, a disallowed page, a redirect, a PDF and 30 pages of one template."""

DISALLOWED = "/private/secret"
ORPHAN = "/orphan"
PDF = "/files/guide.pdf"
ITEMS = [f"/products/item-{i}" for i in range(30)]
PAGES = {"/", "/about", ORPHAN, "/products", *ITEMS}  # what a default crawl must find


def _page(title: str, links: list[str]) -> str:
    anchors = "".join(f'<li><a href="{href}">{href}</a></li>' for href in links)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{title}</title></head><body><h1>{title}</h1><ul>{anchors}</ul></body></html>")


def _xml(body: str) -> dict:
    return {"headers": {"Content-Type": "application/xml"}, "body": body}


def crawl_routes() -> dict:
    """Routes for tests.servers.serve_routes. Bodies that need the base URL are callables."""
    routes = {
        "/robots.txt": {
            "headers": {"Content-Type": "text/plain"},
            "body": lambda base: (f"User-agent: *\nDisallow: /private/\n"
                                  f"Sitemap: {base}/sitemap_index.xml\n"),
        },
        "/sitemap_index.xml": _xml(lambda base: (
            '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>{base}/sitemap-pages.xml</loc></sitemap></sitemapindex>")),
        "/sitemap-pages.xml": _xml(lambda base: (
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{base}{p}</loc></url>" for p in ["/", "/about", ORPHAN])
            + "<url><loc>https://elsewhere.example/page</loc></url></urlset>")),
        "/": {"body": _page("Home", ["/about", "/products", DISALLOWED, "/old", PDF,
                                     "https://broken.example.invalid/"])},
        "/about": {"body": _page("About", ["/"])},
        ORPHAN: {"body": _page("Orphan", ["/"])},
        DISALLOWED: {"body": _page("Secret", ["/"])},
        "/products": {"body": _page("Products", ITEMS)},
        "/old": {"status": 301, "headers": {"Location": "/about"}, "body": ""},
        PDF: {"headers": {"Content-Type": "application/pdf"}, "body": b"%PDF-1.4\n%%EOF\n"},
    }
    for item in ITEMS:
        routes[item] = {"body": _page(item, ["/products", "/products/item-999"])}
    return routes
