"""Download a source, encode an MP4/HLS ladder plus a thumbnail, and upload the results."""

import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.config import Settings
from app.media_types import content_type_for
from app.metrics import FFMPEG_CPU, FFMPEG_DURATION, FFMPEG_FAILURES, FFMPEG_MAX_RSS
from app.models import OutputKind
from app.services import ffmpeg
from app.services.ffmpeg import EncodeOptions, FFmpegError, FFmpegResult, Rendition
from app.services.hls import Variant, master_playlist
from app.storage import StorageService

logger = logging.getLogger(__name__)

# Share of overall progress spent in each phase (download, encode, package, upload).
DOWNLOAD_WEIGHT, ENCODE_WEIGHT, PACKAGE_WEIGHT, UPLOAD_WEIGHT = 0.01, 0.85, 0.05, 0.09


class PermanentError(Exception):
    """Failure that retrying cannot fix (missing source, unsupported request)."""


class InsufficientDiskSpace(Exception):
    pass


class JobTimeout(Exception):
    pass


@dataclass
class TranscodeRequest:
    job_id: str
    video_id: str
    source_key: str
    width: int
    height: int
    duration: float
    frame_rate: float
    has_audio: bool
    presets: list[str]
    formats: list[str]
    crf: int
    x264_preset: str


@dataclass
class OutputArtifact:
    kind: str
    object_key: str
    content_type: str
    rendition: str | None = None
    width: int | None = None
    height: int | None = None
    bitrate: int | None = None
    size_bytes: int | None = None


def output_prefix(video_id: str, job_id: str) -> str:
    return f"{video_id}/{job_id}/"


class TranscodePipeline:
    def __init__(
        self,
        storage: StorageService,
        settings: Settings,
        runner: Callable[..., FFmpegResult] = ffmpeg.run_ffmpeg,
    ):
        self.storage = storage
        self.settings = settings
        self.runner = runner

    def run(self, req: TranscodeRequest, on_progress: Callable[[float], None]) -> list[OutputArtifact]:
        deadline = time.monotonic() + self.settings.job_timeout_seconds
        work = Path(self.settings.work_dir) / req.job_id
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            return self._run(req, work, deadline, on_progress)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise JobTimeout(f"Job exceeded {self.settings.job_timeout_seconds}s timeout")
        return remaining

    def _ffmpeg(self, step: str, rendition: str, cmd: list[str], deadline: float, **kwargs) -> FFmpegResult:
        try:
            result = self.runner(cmd, timeout=self._remaining(deadline), **kwargs)
        except FFmpegError:
            FFMPEG_FAILURES.labels(step).inc()
            raise
        FFMPEG_DURATION.labels(step, rendition).observe(result.wall_seconds)
        FFMPEG_CPU.labels(step).inc(result.cpu_seconds)
        FFMPEG_MAX_RSS.set(result.max_rss_bytes)
        return result

    def _check_disk(self, work: Path, source_key: str) -> None:
        stat = self.storage.stat(self.storage.source_bucket, source_key)
        if stat is None:
            raise PermanentError(f"Source object {source_key} no longer exists")
        needed = int(stat.size * self.settings.disk_space_factor)
        free = shutil.disk_usage(work).free
        if free < needed:
            raise InsufficientDiskSpace(f"Need ~{needed} bytes of scratch space, {free} available")

    def _run(self, req, work: Path, deadline: float, on_progress) -> list[OutputArtifact]:
        s = self.settings
        self._check_disk(work, req.source_key)

        source = work / f"source{PurePosixPath(req.source_key).suffix or '.bin'}"
        self.storage.download_file(self.storage.source_bucket, req.source_key, source)
        on_progress(DOWNLOAD_WEIGHT)

        try:
            ladder = ffmpeg.build_ladder(req.width, req.height, req.presets)
        except ValueError as exc:
            raise PermanentError(str(exc)) from exc
        opts = EncodeOptions(req.crf, req.x264_preset, s.keyframe_interval_seconds, s.max_fps)
        frame_rate = min(req.frame_rate, s.max_fps)

        # 1. Encode one MP4 per rendition (also the input for HLS packaging).
        mp4s: dict[str, Path] = {}
        for i, rendition in enumerate(ladder):
            out = work / f"{rendition.name}.mp4"

            def encode_progress(frac: float, i=i):
                on_progress(DOWNLOAD_WEIGHT + ENCODE_WEIGHT * (i + frac) / len(ladder))

            cmd = ffmpeg.mp4_command(s.ffmpeg_path, source, out, rendition, opts, req.has_audio)
            logger.info(
                "Encoding rendition",
                extra={"rendition": rendition.name, "size": f"{rendition.width}x{rendition.height}"},
            )
            self._ffmpeg(
                "encode", rendition.name, cmd, deadline, duration=req.duration, on_progress=encode_progress
            )
            mp4s[rendition.name] = out
        source.unlink(missing_ok=True)

        # 2. Thumbnail from 10% into the video, taken from the best rendition.
        thumb = work / "thumbnail.jpg"
        best = mp4s[ladder[0].name]
        cmd = ffmpeg.thumbnail_command(
            s.ffmpeg_path, best, thumb, min(req.duration * 0.1, 10.0), s.thumbnail_width
        )
        self._ffmpeg("thumbnail", "-", cmd, deadline)

        # 3. HLS: remux each MP4 into segments, then write the master playlist.
        hls_root = work / "hls"
        variants: list[Variant] = []
        if "hls" in req.formats:
            for rendition in ladder:
                out_dir = hls_root / rendition.name
                out_dir.mkdir(parents=True)
                cmd = ffmpeg.hls_command(
                    s.ffmpeg_path, mp4s[rendition.name], out_dir, s.hls_segment_seconds, s.hls_segment_type
                )
                self._ffmpeg("package", rendition.name, cmd, deadline)
                variants.append(self._variant(rendition, mp4s[rendition.name], req, frame_rate))
            (hls_root / "master.m3u8").write_text(master_playlist(variants))
        on_progress(DOWNLOAD_WEIGHT + ENCODE_WEIGHT + PACKAGE_WEIGHT)

        # 4. Upload. Clear the prefix first so a retried job never mixes outputs from attempts.
        prefix = output_prefix(req.video_id, req.job_id)
        self.storage.delete_prefix(self.storage.output_bucket, prefix)
        uploads: list[tuple[Path, str, OutputArtifact | None]] = []

        if "mp4" in req.formats:
            for rendition in ladder:
                path = mp4s[rendition.name]
                key = f"{prefix}mp4/{rendition.name}.mp4"
                uploads.append(
                    (path, key, self._artifact(OutputKind.MP4, key, path, rendition, req.duration))
                )
        thumb_key = f"{prefix}thumbnail.jpg"
        uploads.append(
            (
                thumb,
                thumb_key,
                OutputArtifact(
                    OutputKind.THUMBNAIL, thumb_key, "image/jpeg", size_bytes=thumb.stat().st_size
                ),
            )
        )

        if variants:
            by_name = {r.name: r for r in ladder}
            for path in sorted(hls_root.rglob("*")):
                if path.is_dir():
                    continue
                rel = path.relative_to(hls_root).as_posix()
                key = f"{prefix}hls/{rel}"
                artifact = None
                if rel == "master.m3u8":
                    artifact = OutputArtifact(
                        OutputKind.HLS_MASTER, key, content_type_for(key), size_bytes=path.stat().st_size
                    )
                elif path.name == "index.m3u8":
                    r = by_name[path.parent.name]
                    artifact = OutputArtifact(
                        OutputKind.HLS_VARIANT, key, content_type_for(key), r.name, r.width, r.height,
                        r.peak_bandwidth, sum(p.stat().st_size for p in path.parent.iterdir()),
                    )  # fmt: skip
                uploads.append((path, key, artifact))
            # Upload the master playlist last so it never references missing variants.
            uploads.sort(key=lambda u: u[1].endswith("master.m3u8"))

        artifacts = []
        for n, (path, key, artifact) in enumerate(uploads, 1):
            self._remaining(deadline)
            self.storage.upload_file(self.storage.output_bucket, key, path)
            if artifact:
                artifacts.append(artifact)
            on_progress(DOWNLOAD_WEIGHT + ENCODE_WEIGHT + PACKAGE_WEIGHT + UPLOAD_WEIGHT * n / len(uploads))
        on_progress(1.0)
        return artifacts

    @staticmethod
    def _avg_bitrate(path: Path, duration: float) -> int:
        return int(path.stat().st_size * 8 / duration) if duration > 0 else 0

    def _artifact(self, kind, key, path: Path, rendition: Rendition, duration: float) -> OutputArtifact:
        return OutputArtifact(
            kind, key, content_type_for(key), rendition.name, rendition.width, rendition.height,
            self._avg_bitrate(path, duration), path.stat().st_size,
        )  # fmt: skip

    def _variant(self, rendition: Rendition, mp4: Path, req: TranscodeRequest, frame_rate: float) -> Variant:
        average = self._avg_bitrate(mp4, req.duration)
        return Variant(
            uri=f"{rendition.name}/index.m3u8",
            bandwidth=max(rendition.peak_bandwidth, int(average * 1.1)),
            average_bandwidth=average,
            width=rendition.width,
            height=rendition.height,
            frame_rate=frame_rate,
            codecs=rendition.codecs(req.has_audio),
        )
