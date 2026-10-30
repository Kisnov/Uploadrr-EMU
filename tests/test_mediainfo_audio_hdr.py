"""Códec de audio y HDR a partir del JSON de mediainfo (src/mediainfo.py), con pistas como las que da mediainfo 25."""

import pytest

from src.mediainfo import MediaInfoHandler


@pytest.fixture
def handler(tmp_path, monkeypatch):
    monkeypatch.setattr(MediaInfoHandler, "_instance", None)
    return MediaInfoHandler({}, str(tmp_path))


def _con_pistas(handler, *pistas):
    handler._cache["u"] = {"media": {"track": [{"@type": "General"}, *pistas]}}
    return "u"


def _audio(**campos):
    return {"@type": "Audio", "Channels": "6", "ChannelLayout": "L R C LFE Ls Rs", "Language_String2": "en", **campos}


def _video(**campos):
    return {"@type": "Video", "colour_primaries": "BT.2020", "transfer_characteristics": "PQ", **campos}


@pytest.mark.parametrize(
    ("pista", "texto"),
    [
        (_audio(Format="DTS", Format_Commercial_IfAny="DTS-HD Master Audio", Format_AdditionalFeatures="XLL"), "DTS-HD MA 5.1"),
        (_audio(Format="DTS", Format_AdditionalFeatures="XLL"), "DTS-HD MA 5.1"),
        (
            _audio(Format="DTS", Format_Commercial_IfAny="DTS-HD High Resolution Audio", Format_AdditionalFeatures="XBR"),
            "DTS-HD HRA 5.1",
        ),
        (
            _audio(Format="MLP FBA", Format_Commercial_IfAny="Dolby TrueHD with Dolby Atmos", Format_AdditionalFeatures="16-ch"),
            "TrueHD 5.1 Atmos",
        ),
        (
            _audio(Format="E-AC-3", Format_Commercial_IfAny="Dolby Digital Plus with Dolby Atmos", Format_AdditionalFeatures="JOC"),
            "DDP 5.1 Atmos",
        ),
        (_audio(Format="E-AC-3", Format_Commercial_IfAny="Dolby Digital Plus"), "DDP 5.1"),
        (_audio(Format="AC-3", Format_Commercial_IfAny="Dolby Digital"), "DD 5.1"),
    ],
    ids=["dts-hd-ma", "dts-xll-sin-comercial", "dts-hd-hra", "truehd-atmos", "ddp-atmos", "ddp", "dd"],
)
def test_codec_de_audio(handler, pista, texto):
    uuid = _con_pistas(handler, _video(), pista)
    assert handler.get_audio(uuid, {"original_language": "en"})[1] == texto


@pytest.mark.parametrize(
    ("video", "hdr"),
    [
        (_video(HDR_Format="SMPTE ST 2086", HDR_Format_Compatibility="HDR10"), "HDR"),
        (_video(HDR_Format="SMPTE ST 2086"), "HDR"),
        (_video(HDR_Format="SMPTE ST 2094 App 4", HDR_Format_Compatibility="HDR10+ Profile B"), "HDR10+"),
        (
            _video(
                HDR_Format="SMPTE ST 2094 App 4",
                HDR_Format_Compatibility="HDR10+ Profile A",
                transfer_characteristics="HLG",
                transfer_characteristics_Original="HLG",
            ),
            "HDR10+",
        ),
        (_video(HDR_Format="SMPTE ST 2094 App 4"), "HDR10+"),
        (_video(HDR_Format="Dolby Vision / SMPTE ST 2086", HDR_Format_Compatibility="Blu-ray / HDR10"), "DV HDR"),
        (_video(HDR_Format="Dolby Vision"), "DV"),
        (_video(), "PQ10"),
        (_video(colour_primaries="BT.709", transfer_characteristics="BT.709"), ""),
    ],
    ids=["hdr10", "solo-2086", "hdr10+", "hdr10+-explicito-con-hlg", "solo-2094", "dv-hdr10", "dv-p5", "pq10", "sdr"],
)
def test_hdr(handler, video, hdr):
    uuid = _con_pistas(handler, video, _audio(Format="AC-3"))
    assert handler.get_hdr(uuid) == hdr


def test_dts_con_varias_caracteristicas_y_xll(handler):
    uuid = _con_pistas(handler, _video(), _audio(Format="DTS", Format_AdditionalFeatures="ES XCh XLL"))
    assert handler.get_audio(uuid, {"original_language": "en"})[1] == "DTS-HD MA 5.1"


@pytest.mark.parametrize(
    "video",
    [
        _video(HDR_Format="SMPTE ST 2086", transfer_characteristics="HLG", transfer_characteristics_Original="HLG"),
        _video(HDR_Format="SMPTE ST 2094 App 4", transfer_characteristics="HLG", transfer_characteristics_Original="HLG"),
    ],
    ids=["2086-hlg", "2094-hlg"],
)
def test_hlg_no_se_confunde_con_hdr10(handler, video):
    uuid = _con_pistas(handler, video, _audio(Format="AC-3"))
    assert handler.get_hdr(uuid) == "HLG"


def test_manda_la_transferencia_principal(handler):
    video = _video(HDR_Format="SMPTE ST 2086", transfer_characteristics="PQ", transfer_characteristics_Original="HLG")
    uuid = _con_pistas(handler, video, _audio(Format="AC-3"))
    assert handler.get_hdr(uuid) == "HDR"
