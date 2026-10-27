"""End-to-end transcodes with real FFmpeg. Run inside the Docker image: `make test-docker`."""

import json
import shutil
import subprocess

import pytest

from app.services.ffmpeg import FFmpegError
from app.services.ffprobe import ProbeError, probe
from app.services.transcoder import TranscodePipeline, TranscodeRequest

pytestmark = [
    pytest.mark.ffmpeg,
    pytest.mark.skipif(
        not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed"
    ),
]


def make_video(path, width, height, seconds=4, audio=True, extra=()):
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate=30:duration={seconds}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", *extra, str(path)]
    subprocess.run(cmd, check=True)
    return path


def ffprobe_json(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


@pytest.fixture
def pipeline(storage, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "work_dir", str(tmp_path / "work"))
    monkeypatch.setattr(settings, "x264_preset", "ultrafast")
    monkeypatch.setattr(settings, "hls_segment_seconds", 2)
    return TranscodePipeline(storage, settings)


def request_for(storage, settings, path, presets, formats=("mp4", "hls"), job_id="job1"):
    key = f"vid1/source{path.suffix}"
    storage.put(storage.source_bucket, key, path.read_bytes())
    info = probe(str(path), settings)
    return TranscodeRequest(
        job_id=job_id,
        video_id="vid1",
        source_key=key,
        width=info.width,
        height=info.height,
        duration=info.duration,
        frame_rate=info.frame_rate,
        has_audio=info.has_audio,
        presets=list(presets),
        formats=list(formats),
        crf=28,
        x264_preset="ultrafast",
    )


def test_720p_source_produces_mp4_and_hls_without_upscaling(pipeline, storage, settings, tmp_path):
    src = make_video(tmp_path / "in.mp4", 1280, 720)
    req = request_for(storage, settings, src, ["1080p", "720p", "480p", "360p"])
    progress = []
    artifacts = pipeline.run(req, progress.append)

    assert progress[-1] == 1.0
    assert progress == sorted(progress)
    out = storage.objects[storage.output_bucket]
    prefix = "vid1/job1/"
    for name in ("720p", "480p", "360p"):
        assert f"{prefix}mp4/{name}.mp4" in out
        assert f"{prefix}hls/{name}/index.m3u8" in out
    assert f"{prefix}mp4/1080p.mp4" not in out
    assert f"{prefix}thumbnail.jpg" in out

    master = out[f"{prefix}hls/master.m3u8"].decode()
    assert master.count("#EXT-X-STREAM-INF") == 3
    assert "RESOLUTION=854x480" in master and 'CODECS="avc1.4d401f,mp4a.40.2"' in master
    kinds = sorted(a.kind for a in artifacts)
    assert kinds == ["hls_master"] + ["hls_variant"] * 3 + ["mp4"] * 3 + ["thumbnail"]

    mp4 = tmp_path / "480p.mp4"
    mp4.write_bytes(out[f"{prefix}mp4/480p.mp4"])
    info = ffprobe_json(mp4)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 854, 480)
    assert video["pix_fmt"] == "yuv420p"
    assert audio["codec_name"] == "aac" and audio["channels"] == 2

    variant = out[f"{prefix}hls/360p/index.m3u8"].decode()
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in variant and "#EXT-X-ENDLIST" in variant
    assert any(k.startswith(f"{prefix}hls/360p/segment_") and k.endswith(".ts") for k in out)


def test_portrait_video_without_audio(pipeline, storage, settings, tmp_path):
    src = make_video(tmp_path / "portrait.mp4", 720, 1280, audio=False)
    req = request_for(storage, settings, src, ["720p", "360p"], formats=["hls"])
    pipeline.run(req, lambda _: None)
    out = storage.objects[storage.output_bucket]
    master = out["vid1/job1/hls/master.m3u8"].decode()
    assert "RESOLUTION=720x1280" in master and "RESOLUTION=360x640" in master
    assert "mp4a" not in master
    assert not any("/mp4/" in k for k in out)  # HLS only


def test_retry_overwrites_previous_attempt(pipeline, storage, settings, tmp_path):
    src = make_video(tmp_path / "in.mp4", 640, 360, seconds=2)
    storage.put(storage.output_bucket, "vid1/job1/hls/stale/segment_00000.ts", b"old")
    pipeline.run(request_for(storage, settings, src, ["360p"]), lambda _: None)
    assert "vid1/job1/hls/stale/segment_00000.ts" not in storage.objects[storage.output_bucket]


def test_corrupted_input_is_rejected(storage, settings, tmp_path, pipeline):
    bad = tmp_path / "bad.mp4"
    good = make_video(tmp_path / "good.mp4", 640, 360, seconds=2)
    bad.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 3])  # truncated, moov atom lost
    with pytest.raises(ProbeError):
        probe(str(bad), settings)

    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"\x00not a video" * 1000)
    with pytest.raises(ProbeError):
        probe(str(garbage), settings)

    # If a corrupt file slips past probing, the encode fails loudly and cleans up scratch space.
    req = request_for(storage, settings, good, ["360p"])
    storage.put(storage.source_bucket, req.source_key, garbage.read_bytes())
    with pytest.raises(FFmpegError):
        pipeline.run(req, lambda _: None)
    assert not (tmp_path / "work" / "job1").exists()
