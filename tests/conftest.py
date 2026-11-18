from pathlib import Path

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('ROOT_URL', 'https://www.astronet.ru')
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'src'))
    import main

    return main
