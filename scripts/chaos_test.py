#!/usr/bin/env python3
"""Concurrency and crash-recovery test against a running stack.

Submits N jobs at once, hard-kills the worker processing one of them, and checks that every job
still completes exactly once (the killed one via lease expiry + recovery).

  # docker compose (worker hostname = container id)
  python3 scripts/chaos_test.py sample.mp4 --jobs 4 --kill-cmd 'docker kill {host}'
  # Kubernetes (worker hostname = pod name)
  python3 scripts/chaos_test.py sample.mp4 --api https://api.example.com --key $KEY \
      --kill-cmd 'kubectl -n media-converter delete pod {host} --grace-period=0 --force'
"""

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e import call, check  # noqa: E402


def upload_video(api, key, path: Path) -> str:
    data = path.read_bytes()
    _, _, raw = call(
        "POST",
        f"{api}/v1/videos",
        key,
        {"filename": path.name, "content_type": "video/mp4", "size_bytes": len(data)},
    )
    created = json.loads(raw)
    status, _, _ = call("PUT", created["upload"]["put_url"], data=data, headers={"Content-Type": "video/mp4"})
    check(status == 200, "uploaded source")
    status, _, raw = call("POST", f"{api}/v1/videos/{created['video']['id']}/complete", key)
    check(status == 200, "upload validated")
    return created["video"]["id"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--key", default="dev-key")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--kill-cmd", help="command template; {host} is the worker hostname")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()

    video_id = upload_video(args.api, args.key, args.file)
    job_ids = []
    started = time.time()
    for i in range(args.jobs):
        body = {"presets": ["1080p", "720p", "480p", "360p"], "crf": 23 + i % 3}
        status, _, raw = call("POST", f"{args.api}/v1/videos/{video_id}/jobs", args.key, body)
        check(status == 202, f"job {i + 1} submitted")
        job_ids.append(json.loads(raw)["id"])

    killed = None
    seen_concurrency = 0
    deadline = time.time() + args.timeout
    jobs = {}
    while time.time() < deadline:
        for job_id in job_ids:
            jobs[job_id] = json.loads(call("GET", f"{args.api}/v1/jobs/{job_id}", args.key)[2])
        processing = [j for j in jobs.values() if j["status"] == "processing"]
        seen_concurrency = max(seen_concurrency, len(processing))
        if args.kill_cmd and killed is None:
            target = next((j for j in processing if j["progress"] > 0.05), None)
            if target:
                host = (target.get("worker_id") or "").split(":")[0]
                if host:
                    cmd = args.kill_cmd.format(host=host)
                    progress = f"{target['progress']:.0%}"
                    print(f"     killing worker {host} during job {target['id'][:8]} at {progress}: {cmd}")
                    subprocess.run(shlex.split(cmd), check=False, capture_output=True)
                    killed = target["id"]
        summary = ", ".join(
            f"{j['id'][:8]}={j['status']}:{j['progress']:.0%}/try{j['attempts']}" for j in jobs.values()
        )
        print(f"     {time.time() - started:5.0f}s {summary}")
        if all(j["status"] in ("completed", "failed") for j in jobs.values()):
            break
        time.sleep(5)

    check(all(j["status"] == "completed" for j in jobs.values()), "all jobs completed")
    check(seen_concurrency > 1 or args.jobs == 1, f"jobs ran concurrently (max {seen_concurrency} at once)")
    if killed:
        check(jobs[killed]["attempts"] >= 2, f"killed job recovered on attempt {jobs[killed]['attempts']}")
    print(f"     total {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
