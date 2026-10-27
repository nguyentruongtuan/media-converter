import sys
import textwrap
from pathlib import Path

import pytest

from app.services.ffmpeg import (
    PRESET_ORDER,
    PRESETS,
    EncodeOptions,
    FFmpegError,
    FFmpegTimeout,
    build_ladder,
    hls_command,
    mp4_command,
    run_ffmpeg,
    thumbnail_command,
)
from app.services.hls import Variant, master_playlist


def sizes(ladder):
    return [(r.name, r.width, r.height) for r in ladder]


def test_full_ladder_for_1080p_source():
    assert sizes(build_ladder(1920, 1080, PRESET_ORDER)) == [
        ("1080p", 1920, 1080),
        ("720p", 1280, 720),
        ("480p", 854, 480),
        ("360p", 640, 360),
    ]


def test_never_upscales_720p_source():
    assert [r.name for r in build_ladder(1280, 720, PRESET_ORDER)] == ["720p", "480p", "360p"]


def test_preserves_non_16x9_aspect_ratio():
    ladder = build_ladder(1440, 1080, ["720p"])  # 4:3
    assert sizes(ladder) == [("720p", 960, 720)]
    ladder = build_ladder(2560, 1080, ["1080p", "360p"])  # 21:9
    assert sizes(ladder) == [("1080p", 2560, 1080), ("360p", 854, 360)]


def test_portrait_video_uses_short_side():
    assert sizes(build_ladder(1080, 1920, ["1080p", "720p"])) == [("1080p", 1080, 1920), ("720p", 720, 1280)]


def test_dimensions_are_even():
    for r in build_ladder(1918, 1078, PRESET_ORDER[1:]):
        assert r.width % 2 == 0 and r.height % 2 == 0


def test_tiny_source_keeps_native_resolution():
    ladder = build_ladder(321, 241, PRESET_ORDER)
    assert sizes(ladder) == [("240p", 320, 240)]  # rounded down: never even 1px of upscaling
    assert ladder[0].preset is PRESETS["360p"]


def test_only_requested_presets():
    assert [r.name for r in build_ladder(3840, 2160, ["480p", "1080p"])] == ["1080p", "480p"]


def test_rejects_unknown_preset():
    with pytest.raises(ValueError):
        build_ladder(1920, 1080, ["4k"])


def test_codecs_strings():
    r1080, r720, r480, _ = build_ladder(1920, 1080, PRESET_ORDER)
    assert r1080.codecs(True) == "avc1.64002a,mp4a.40.2"
    assert r720.codecs(False) == "avc1.640028"
    assert r480.codecs(True) == "avc1.4d401f,mp4a.40.2"


def test_mp4_command_h264_aac():
    r = build_ladder(1920, 1080, ["720p"])[0]
    cmd = mp4_command(
        "ffmpeg", Path("in.mov"), Path("out.mp4"), r, EncodeOptions(crf=21, x264_preset="fast"), True
    )
    joined = " ".join(cmd)
    assert "-c:v libx264" in joined and "-c:a aac" in joined
    assert "scale=1280:720" in joined and "format=yuv420p" in joined
    assert "-crf 21" in joined and "-preset fast" in joined and "-maxrate 2996k" in joined
    assert "-profile:v high" in joined and "-movflags +faststart" in joined
    assert "expr:gte(t,n_forced*2)" in joined
    assert cmd[-1] == "out.mp4"


def test_mp4_command_without_audio():
    r = build_ladder(1920, 1080, ["360p"])[0]
    cmd = mp4_command("ffmpeg", Path("in"), Path("out.mp4"), r, EncodeOptions(), False)
    assert "-c:a" not in cmd and "0:a:0" not in cmd


def test_hls_and_thumbnail_commands():
    cmd = hls_command("ffmpeg", Path("720p.mp4"), Path("/w/hls/720p"), 6, "mpegts")
    joined = " ".join(cmd)
    assert "-c copy" in joined and "-hls_time 6" in joined and "-hls_playlist_type vod" in joined
    assert cmd[-1] == "/w/hls/720p/index.m3u8"
    assert "/w/hls/720p/segment_%05d.ts" in cmd
    fmp4 = hls_command("ffmpeg", Path("a.mp4"), Path("/o"), 4, "fmp4")
    assert "/o/segment_%05d.m4s" in fmp4 and "init.mp4" in fmp4

    thumb = thumbnail_command("ffmpeg", Path("a.mp4"), Path("t.jpg"), 1.5, 640)
    assert thumb[thumb.index("-ss") + 1] == "1.500"
    assert "-frames:v" in thumb


def test_master_playlist():
    variants = [
        Variant("720p/index.m3u8", 3_124_000, 2_000_000, 1280, 720, 29.97, "avc1.640028,mp4a.40.2"),
        Variant("360p/index.m3u8", 952_000, 600_000, 640, 360, 29.97, "avc1.4d401f,mp4a.40.2"),
    ]
    text = master_playlist(variants)
    lines = text.strip().splitlines()
    assert lines[0] == "#EXTM3U"
    assert "#EXT-X-INDEPENDENT-SEGMENTS" in lines
    assert lines[3].startswith("#EXT-X-STREAM-INF:BANDWIDTH=952000,") and "RESOLUTION=640x360" in lines[3]
    assert lines[4] == "360p/index.m3u8"
    assert 'CODECS="avc1.640028,mp4a.40.2"' in lines[5]
    with pytest.raises(ValueError):
        master_playlist([])


def fake_ffmpeg(tmp_path, body: str) -> list[str]:
    script = tmp_path / "fake_ffmpeg.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


def test_run_ffmpeg_reports_progress(tmp_path):
    cmd = fake_ffmpeg(
        tmp_path,
        """
        import sys
        for us in (2_500_000, 5_000_000, 10_000_000):
            print(f"out_time_us={us}", flush=True)
            print("progress=continue", flush=True)
        print("progress=end", flush=True)
        """,
    )
    seen = []
    result = run_ffmpeg(cmd, duration=10.0, on_progress=seen.append)
    assert seen == [0.25, 0.5, 1.0, 1.0]
    assert result.wall_seconds > 0


def test_run_ffmpeg_raises_with_stderr_tail(tmp_path):
    cmd = fake_ffmpeg(
        tmp_path,
        """
        import sys
        sys.stderr.write("Invalid data found when processing input\\n")
        sys.exit(1)
        """,
    )
    with pytest.raises(FFmpegError) as exc:
        run_ffmpeg(cmd)
    assert exc.value.returncode == 1
    assert "Invalid data found" in str(exc.value)


def test_run_ffmpeg_timeout(tmp_path):
    cmd = fake_ffmpeg(tmp_path, "import time; time.sleep(30)")
    with pytest.raises(FFmpegTimeout):
        run_ffmpeg(cmd, timeout=0.5)


def test_run_ffmpeg_missing_binary():
    with pytest.raises(FFmpegError):
        run_ffmpeg(["/nonexistent/ffmpeg"])
