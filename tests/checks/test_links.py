from site_doctor.checks import links


def test_working_link_is_not_a_finding():
    assert links.link_finding("https://a.com/x", 200, internal=True) is None


def test_broken_internal_link():
    f = links.link_finding("https://a.com/x", 404, internal=True, found_on="https://a.com/")
    assert (f.id, f.severity) == ("links.broken-internal", "high")
    assert f.evidence == "HTTP 404 · linked from https://a.com/"


def test_broken_external_and_unreachable():
    assert links.link_finding("https://b.com", 500, internal=False).id == "links.broken-external"
    f = links.link_finding("https://b.com", "Error: ENOTFOUND", internal=False)
    assert (f.id, f.severity, f.evidence) == ("links.unreachable", "medium", "Error: ENOTFOUND")
