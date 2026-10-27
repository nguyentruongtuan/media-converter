from dataclasses import dataclass


@dataclass(frozen=True)
class Variant:
    uri: str
    bandwidth: int
    average_bandwidth: int
    width: int
    height: int
    frame_rate: float
    codecs: str


def master_playlist(variants: list[Variant]) -> str:
    """Build a master playlist. Variants are listed lowest-bandwidth first for fast startup."""
    if not variants:
        raise ValueError("A master playlist needs at least one variant")
    lines = ["#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for v in sorted(variants, key=lambda v: v.bandwidth):
        lines.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH={v.bandwidth},AVERAGE-BANDWIDTH={v.average_bandwidth},"
            f'RESOLUTION={v.width}x{v.height},FRAME-RATE={v.frame_rate:.3f},CODECS="{v.codecs}"'
        )
        lines.append(v.uri)
    return "\n".join(lines) + "\n"
