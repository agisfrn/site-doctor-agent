from urllib.error import HTTPError
from urllib.request import urlopen

import pytest


def test_demo_server_serves_pages_and_500(demo_url):
    assert urlopen(demo_url + "/index.html").status == 200
    with pytest.raises(HTTPError) as e:
        urlopen(demo_url + "/error")
    assert e.value.code == 500
