"""El filtro de ffmpeg de cada captura según el HDR (src/screenshots/generator.py)."""

import asyncio

import pytest

from src.mediainfo import MediaInfoHandler
from src.screenshots import generator as gen


@pytest.fixture
def capturas(tmp_path, monkeypatch):
    monkeypatch.setattr(MediaInfoHandler, "_instance", None)
    llamadas = []

    class Proceso:
        returncode = 1

        async def communicate(self):
            return b"", b""

    async def ffmpeg_falso(*args, **_kwargs):
        llamadas.append(list(args))
        return Proceso()

    monkeypatch.setattr(gen.asyncio, "create_subprocess_exec", ffmpeg_falso)

    def argumentos(hdr):
        generador = gen.ScreenshotGenerator({"SCREENS": {}}, str(tmp_path))
        asyncio.run(generador._take_screenshot("v.mkv", str(tmp_path / "s.png"), 60, "", False, retries=1, hdr=hdr))
        return llamadas[-1]

    return argumentos


def test_dolby_vision_sin_hdr10_usa_libplacebo(capturas):
    args = capturas("DV")
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("libplacebo=") and "contrast_recovery=0" in vf
    assert args[1:3] == ["-init_hw_device", "vulkan"]


@pytest.mark.parametrize("hdr", ["DV HDR", "HDR", "HDR10+", "HLG"])
def test_con_capa_hdr_sigue_con_zscale(capturas, hdr):
    args = capturas(hdr)
    assert args[args.index("-vf") + 1].startswith("zscale=")
    assert "-init_hw_device" not in args


def test_sdr_sin_tonemap(capturas):
    args = capturas("")
    assert args[args.index("-vf") + 1] == "scale=iw:ih"
    assert "-init_hw_device" not in args
