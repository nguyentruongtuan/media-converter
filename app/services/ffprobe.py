import json
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction

from app.config import Settings


class ProbeError(Exception):
    pass


@dataclass
class ProbeResult:
    duration: float
    width: int  # display width (rotation and SAR applied)
    height: int
    frame_rate: float
    video_codec: str
    audio_codec: str | None
    bitrate: int | None
    rotation: int
    container: str | None
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None


def _ratio(value: str | None, default: float = 0.0) -> float:
    if not value or value in ("0/0", "N/A"):
        return default
    try:
        return float(Fraction(value.replace(":", "/")))
    except (ValueError, ZeroDivisionError):
        return default


def _float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # filter NaN


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return int(float(side["rotation"])) % 360
    if rotate := (stream.get("tags") or {}).get("rotate"):
        return int(float(rotate)) % 360
    return 0


def parse_probe(data: dict) -> ProbeResult:
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise ProbeError("No video stream found")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ProbeError("Video stream has no valid dimensions")

    sar = _ratio(video.get("sample_aspect_ratio"), 1.0) or 1.0
    width = round(width * sar)
    rotation = _rotation(video)
    if rotation in (90, 270):
        width, height = height, width

    duration = _float(fmt.get("duration")) or _float(video.get("duration"))
    if not duration or duration <= 0:
        raise ProbeError("Could not determine video duration")

    frame_rate = _ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate"))
    if frame_rate <= 0 or frame_rate > 1000:
        raise ProbeError("Could not determine a valid frame rate")

    bitrate = _float(fmt.get("bit_rate"))
    return ProbeResult(
        duration=duration,
        width=width,
        height=height,
        frame_rate=round(frame_rate, 3),
        video_codec=video.get("codec_name") or "unknown",
        audio_codec=audio.get("codec_name") if audio else None,
        bitrate=int(bitrate) if bitrate else None,
        rotation=rotation,
        container=fmt.get("format_name"),
        raw=data,
    )


def validate_probe(result: ProbeResult, settings: Settings) -> None:
    if result.duration > settings.max_video_duration_seconds:
        raise ProbeError(
            f"Video is {result.duration:.0f}s long; the limit is {settings.max_video_duration_seconds:.0f}s"
        )
    if max(result.width, result.height) > max(settings.max_source_width, settings.max_source_height) or min(
        result.width, result.height
    ) > min(settings.max_source_width, settings.max_source_height):
        raise ProbeError(f"Resolution {result.width}x{result.height} exceeds the supported maximum")
    if min(result.width, result.height) < 16:
        raise ProbeError(f"Resolution {result.width}x{result.height} is too small")


def probe(source: str, settings: Settings) -> ProbeResult:
    """Probe a local path or an HTTP(S) URL (e.g. a presigned MinIO URL)."""
    cmd = [
        settings.ffprobe_path,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        source,
    ]  # fmt: skip
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.ffprobe_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError("ffprobe timed out") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(f"ffprobe binary not found: {settings.ffprobe_path}") from exc
    if proc.returncode != 0:
        raise ProbeError(f"Not a readable video file: {proc.stderr.strip()[-500:] or 'ffprobe failed'}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid output") from exc
    return parse_probe(data)
