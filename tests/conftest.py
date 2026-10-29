import pytest

from site_doctor.browser import Browser
from site_doctor.demo_server import serve


@pytest.fixture(scope="session")
def demo_url():
    with serve() as url:
        yield url


@pytest.fixture(scope="session")
def browser():
    with Browser() as b:
        yield b
