"""Resolución a partir del JSON de mediainfo (src/mediainfo.py)."""

import pytest

from src.mediainfo import MediaInfoHandler


@pytest.fixture
def handler(tmp_path, monkeypatch):
    monkeypatch.setattr(MediaInfoHandler, "_instance", None)
    return MediaInfoHandler({}, str(tmp_path))


@pytest.mark.parametrize(
    ("ancho", "alto", "barrido", "resolucion"),
    [
        (960, 540, "Progressive", "540p"),
        (1280, 540, "Progressive", "720p"),
        (1920, 1080, "Progressive", "1080p"),
        (720, 576, "Interlaced", "576i"),
        (720, 480, "Progressive", "480p"),
    ],
)
def test_resolucion(handler, ancho, alto, barrido, resolucion):
    video = {"@type": "Video", "Width": str(ancho), "Height": str(alto), "ScanType": barrido}
    handler._cache["u"] = {"media": {"track": [{"@type": "General"}, video]}}
    assert handler.get_resolution({}, "u") == resolucion
