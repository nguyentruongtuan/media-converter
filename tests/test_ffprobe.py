import pytest

from app.services.ffprobe import ProbeError, parse_probe, validate_probe
from tests.conftest import make_probe


def sample(**video):
    stream = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "30000/1001",
        "r_frame_rate": "30000/1001",
        "sample_aspect_ratio": "1:1",
    } | video
    return {
        "streams": [stream, {"codec_type": "audio", "codec_name": "aac"}],
        "format": {"duration": "12.345", "bit_rate": "8000000", "format_name": "mov,mp4"},
    }


def test_parse_basic():
    r = parse_probe(sample())
    assert (r.width, r.height, r.frame_rate) == (1920, 1080, 29.97)
    assert r.duration == pytest.approx(12.345)
    assert r.has_audio and r.audio_codec == "aac"
    assert r.bitrate == 8_000_000


def test_rotation_swaps_dimensions():
    r = parse_probe(sample(side_data_list=[{"rotation": -90}]))
    assert (r.width, r.height, r.rotation) == (1080, 1920, 270)
    legacy = parse_probe(sample(tags={"rotate": "90"}))
    assert (legacy.width, legacy.height) == (1080, 1920)


def test_anamorphic_sample_aspect_ratio():
    r = parse_probe(sample(width=720, height=576, sample_aspect_ratio="16:11"))
    assert (r.width, r.height) == (1047, 576)


def test_no_audio():
    data = sample()
    data["streams"] = data["streams"][:1]
    assert parse_probe(data).has_audio is False


def test_rejects_audio_only_or_cover_art():
    with pytest.raises(ProbeError, match="No video stream"):
        parse_probe({"streams": [{"codec_type": "audio"}], "format": {"duration": "3"}})
    cover = sample(disposition={"attached_pic": 1})
    cover["streams"] = cover["streams"][:1]
    with pytest.raises(ProbeError):
        parse_probe(cover)


def test_rejects_missing_duration_or_dimensions():
    data = sample()
    data["format"].pop("duration")
    with pytest.raises(ProbeError, match="duration"):
        parse_probe(data)
    with pytest.raises(ProbeError, match="dimensions"):
        parse_probe(sample(width=0))


def test_validate_limits(settings):
    validate_probe(make_probe(), settings)
    validate_probe(make_probe(width=2160, height=3840), settings)  # portrait 4K is fine
    with pytest.raises(ProbeError):
        validate_probe(make_probe(width=10000, height=5000), settings)
    with pytest.raises(ProbeError):
        validate_probe(make_probe(width=8, height=8), settings)
