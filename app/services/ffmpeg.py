"""FFmpeg presets, command builders and a progress-reporting runner."""

import logging
import resource
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Preset:
    name: str
    height: int  # applied to the short side, so portrait video is handled the same way
    video_bitrate_k: int
    maxrate_k: int
    bufsize_k: int
    audio_bitrate_k: int
    profile: str
    level: str


# Levels are chosen so each rung stays decodable at up to 60 fps.
PRESETS: dict[str, Preset] = {
    "1080p": Preset("1080p", 1080, 5000, 5350, 7500, 128, "high", "4.2"),
    "720p": Preset("720p", 720, 2800, 2996, 4200, 128, "high", "4.0"),
    "480p": Preset("480p", 480, 1400, 1498, 2100, 96, "main", "3.1"),
    "360p": Preset("360p", 360, 800, 856, 1200, 96, "main", "3.1"),
}
PRESET_ORDER = ["1080p", "720p", "480p", "360p"]

_PROFILE_IDC = {"baseline": "42e0", "main": "4d40", "high": "6400"}


@dataclass(frozen=True)
class EncodeOptions:
    crf: int = 23
    x264_preset: str = "veryfast"
    keyframe_interval: int = 2
    max_fps: int = 60


@dataclass(frozen=True)
class Rendition:
    name: str
    width: int
    height: int
    preset: Preset

    @property
    def peak_bandwidth(self) -> int:
        return (self.preset.maxrate_k + self.preset.audio_bitrate_k) * 1000

    def codecs(self, has_audio: bool) -> str:
        level = int(round(float(self.preset.level) * 10))
        video = f"avc1.{_PROFILE_IDC[self.preset.profile]}{level:02x}"
        return f"{video},mp4a.40.2" if has_audio else video


def even(value: float) -> int:
    return max(2, int(value / 2 + 0.5) * 2)


def even_floor(value: int) -> int:
    return max(2, value // 2 * 2)


def build_ladder(src_width: int, src_height: int, preset_names: list[str]) -> list[Rendition]:
    """Pick renditions that never upscale, keeping the source display aspect ratio."""
    unknown = set(preset_names) - PRESETS.keys()
    if unknown:
        raise ValueError(f"Unknown presets: {sorted(unknown)}")
    if not preset_names:
        raise ValueError("At least one preset is required")

    short_side = min(src_width, src_height)
    landscape = src_width >= src_height
    requested = [PRESETS[n] for n in PRESET_ORDER if n in preset_names]
    selected = [p for p in requested if p.height <= short_side]

    def dims(target: int) -> tuple[int, int]:
        if landscape:
            return even(src_width * target / src_height), even(target)
        return even(target), even(src_height * target / src_width)

    if not selected:
        # Source is smaller than every requested rung: keep native size with the smallest rung's settings.
        width, height = even_floor(src_width), even_floor(src_height)
        return [Rendition(f"{min(width, height)}p", width, height, requested[-1])]
    return [Rendition(p.name, *dims(p.height), p) for p in selected]


def mp4_command(
    ffmpeg: str, source: Path, output: Path, rendition: Rendition, opts: EncodeOptions, has_audio: bool
) -> list[str]:
    p = rendition.preset
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", str(source),
        "-map", "0:v:0",
        "-vf", f"scale={rendition.width}:{rendition.height}:flags=lanczos,setsar=1,format=yuv420p",
        "-fpsmax", str(opts.max_fps),
        "-c:v", "libx264",
        "-preset", opts.x264_preset,
        "-crf", str(opts.crf),
        "-maxrate", f"{p.maxrate_k}k",
        "-bufsize", f"{p.bufsize_k}k",
        "-profile:v", p.profile,
        "-level:v", p.level,
        # Fixed-interval keyframes keep HLS segments aligned across renditions.
        "-force_key_frames", f"expr:gte(t,n_forced*{opts.keyframe_interval})",
        "-sc_threshold", "0",
    ]  # fmt: skip
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", f"{p.audio_bitrate_k}k", "-ac", "2", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output)]
    return cmd


def hls_command(ffmpeg: str, mp4: Path, out_dir: Path, segment_seconds: int, segment_type: str) -> list[str]:
    ext = "m4s" if segment_type == "fmp4" else "ts"
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", str(mp4),
        "-map", "0", "-c", "copy",
        "-f", "hls",
        "-hls_time", str(segment_seconds),
        "-hls_playlist_type", "vod",
        "-hls_segment_type", segment_type,
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", str(out_dir / f"segment_%05d.{ext}"),
    ]  # fmt: skip
    if segment_type == "fmp4":
        cmd += ["-hls_fmp4_init_filename", "init.mp4"]
    cmd += ["-progress", "pipe:1", "-nostats", str(out_dir / "index.m3u8")]
    return cmd


def thumbnail_command(ffmpeg: str, source: Path, output: Path, at_seconds: float, width: int) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-ss", f"{at_seconds:.3f}",
        "-i", str(source),
        "-frames:v", "1",
        "-vf", f"scale='min({width},iw)':-2,setsar=1",
        "-q:v", "3",
        str(output),
    ]  # fmt: skip


class FFmpegError(Exception):
    def __init__(self, message: str, returncode: int | None = None, stderr_tail: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail


class FFmpegTimeout(FFmpegError):
    pass


@dataclass
class FFmpegResult:
    wall_seconds: float
    cpu_seconds: float
    max_rss_bytes: int


def _children_usage() -> tuple[float, int]:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    rss_scale = 1 if sys.platform == "darwin" else 1024  # Linux reports KiB
    return usage.ru_utime + usage.ru_stime, usage.ru_maxrss * rss_scale


def run_ffmpeg(
    cmd: list[str],
    *,
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    timeout: float | None = None,
) -> FFmpegResult:
    """Run FFmpeg, reporting fractional progress parsed from `-progress pipe:1`."""
    cpu_before, _ = _children_usage()
    start = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    except FileNotFoundError as exc:
        raise FFmpegError(f"FFmpeg binary not found: {cmd[0]}") from exc

    stderr_tail: deque[str] = deque(maxlen=40)
    drain = threading.Thread(target=lambda: stderr_tail.extend(proc.stderr), daemon=True)
    drain.start()

    timed_out = threading.Event()

    def _kill():
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, _kill) if timeout else None
    if timer:
        timer.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            # ffmpeg reports out_time_ms in microseconds too; out_time_us is the unambiguous one.
            if key == "out_time_us" and duration and on_progress and value.isdigit():
                on_progress(min(1.0, int(value) / 1_000_000 / duration))
            elif key == "progress" and value == "end" and on_progress:
                on_progress(1.0)
        returncode = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        if timer:
            timer.cancel()
        drain.join(timeout=5)

    tail = "".join(stderr_tail).strip()
    if timed_out.is_set():
        raise FFmpegTimeout(f"FFmpeg exceeded {timeout:.0f}s timeout", returncode, tail)
    if returncode != 0:
        last = tail.splitlines()[-1] if tail else "no error output"
        raise FFmpegError(f"FFmpeg exited with code {returncode}: {last}", returncode, tail)

    cpu_after, max_rss = _children_usage()
    return FFmpegResult(time.monotonic() - start, max(0.0, cpu_after - cpu_before), max_rss)
