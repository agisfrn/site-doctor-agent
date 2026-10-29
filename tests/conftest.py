import pytest

from site_doctor.demo_server import serve


@pytest.fixture(scope="session")
def demo_url():
    with serve() as url:
        yield url
