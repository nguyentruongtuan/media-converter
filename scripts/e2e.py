#!/usr/bin/env python3
"""End-to-end smoke test against a running stack (docker compose or Kubernetes).

    python3 scripts/e2e.py sample.mp4 --api http://localhost:8000 --key dev-key

Uploads via the presigned URL, completes the upload, creates a job, waits for it,
then fetches the HLS master/variant playlists and one segment through NGINX.
Uses only the standard library.
"""

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def call(method, url, key=None, body=None, headers=None, data=None):
    headers = dict(headers or {})
    if key:
        headers["X-API-Key"] = key
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, resp.headers, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def check(cond, message):
    if not cond:
        sys.exit(f"FAIL: {message}")
    print(f"ok   {message}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--key", default="dev-key")
    parser.add_argument("--presets", default="1080p,720p,480p,360p")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    data = args.file.read_bytes()
    content_type = mimetypes.guess_type(args.file.name)[0] or "video/mp4"
    status, _, raw = call(
        "POST",
        f"{args.api}/v1/videos",
        args.key,
        {"filename": args.file.name, "content_type": content_type, "size_bytes": len(data)},
    )
    check(status == 201, f"create video -> {status} {raw[:200]!r}")
    created = json.loads(raw)
    video_id = created["video"]["id"]

    status, _, raw = call(
        "PUT", created["upload"]["put_url"], data=data, headers={"Content-Type": content_type}
    )
    check(status == 200, f"upload {len(data)} bytes via presigned PUT -> {status}")

    status, _, raw = call("POST", f"{args.api}/v1/videos/{video_id}/complete", args.key)
    check(status == 200, f"complete upload -> {status} {raw[:300]!r}")
    video = json.loads(raw)
    size = f"{video['width']}x{video['height']}"
    print(f"     source {size} {video['duration_seconds']:.1f}s audio={video['has_audio']}")

    body = {"presets": args.presets.split(","), "formats": ["mp4", "hls"]}
    headers = {"Idempotency-Key": f"e2e-{video_id}"}
    status, _, raw = call("POST", f"{args.api}/v1/videos/{video_id}/jobs", args.key, body, headers)
    check(status == 202, f"create job -> {status}")
    job_id = json.loads(raw)["id"]
    status, _, raw = call("POST", f"{args.api}/v1/videos/{video_id}/jobs", args.key, body, headers)
    check(status == 200 and json.loads(raw)["id"] == job_id, "idempotent replay returns the same job")

    deadline = time.time() + args.timeout
    last = None
    while time.time() < deadline:
        _, _, raw = call("GET", f"{args.api}/v1/jobs/{job_id}", args.key)
        job = json.loads(raw)
        if (job["status"], round(job["progress"], 2)) != last:
            last = (job["status"], round(job["progress"], 2))
            print(f"     job {job['status']} {job['progress'] * 100:.0f}%")
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(2)
    check(job["status"] == "completed", f"job completed (error={job.get('error')})")

    status, headers, raw = call("GET", job["hls_url"])
    check(status == 200, f"master playlist via NGINX -> {status}")
    check(
        headers["Content-Type"] == "application/vnd.apple.mpegurl", f"master MIME {headers['Content-Type']}"
    )
    master = raw.decode()
    variants = [line for line in master.splitlines() if line and not line.startswith("#")]
    check(len(variants) >= 1, f"{len(variants)} variants: {', '.join(variants)}")
    base = job["hls_url"].rsplit("/", 1)[0]
    status, _, raw = call("GET", f"{base}/{variants[0]}")
    segment = next(line for line in raw.decode().splitlines() if line and not line.startswith("#"))
    status, headers, raw = call("GET", f"{base}/{variants[0].rsplit('/', 1)[0]}/{segment}")
    check(
        status == 200 and headers["Content-Type"] == "video/mp2t", f"segment MIME {headers['Content-Type']}"
    )
    mp4 = next(o for o in job["outputs"] if o["kind"] == "mp4")
    status, headers, _ = call("GET", mp4["url"], headers={"Range": "bytes=0-1023"})
    check(status == 206 and headers["Content-Type"] == "video/mp4", f"MP4 range request -> {status}")
    print(f"\nPlayer: {job['hls_url'].split('/media/')[0]}/?src={job['hls_url']}")


if __name__ == "__main__":
    main()
