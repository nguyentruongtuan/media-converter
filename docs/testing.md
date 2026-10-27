# Test plan

| Area | How | Automated |
| --- | --- | --- |
| API unit + integration | `make test`: FastAPI TestClient, SQLite, in-memory storage fake, fakeredis | yes |
| FFmpeg presets | `tests/test_ffmpeg_presets.py`: ladder selection, no upscaling, aspect ratio, even dimensions, command flags, codec strings, master playlist, progress parsing, timeouts | yes |
| Real transcodes | `make test-docker`: 720p and portrait sources through the full pipeline, output codecs/dimensions/pixel format checked with ffprobe, HLS VOD playlists | yes (CI) |
| Invalid / corrupted uploads | API tests (size mismatch, too large, wrong type, probe failure, duration limit) and real FFmpeg tests (truncated MP4, random bytes) | yes |
| Job reliability | `tests/test_worker.py`: idempotent redelivery, claim races, retries with backoff, dead letter, permanent failures, throttling, lost lease, recovery of interrupted and lost jobs | yes |
| End to end | `scripts/e2e.py`: presigned upload → validation → job → HLS/MP4 through NGINX with MIME and range checks | yes (CI, Compose) |
| Concurrent processing + worker restart | `scripts/chaos_test.py`: N parallel jobs, SIGKILL the worker of one mid-encode, all must complete, the killed one on a later attempt | yes (CI, Compose); run against Kubernetes with `--kill-cmd 'kubectl delete pod {host} --force --grace-period=0'` |
| Load | `tests/load/locustfile.py` (API) plus `TranscodeSubmitter` to drive KEDA | manual |
| Device playback | Matrix below | manual |

## Recorded results (local, Docker Compose)

- 64 tests pass in the Docker image (60 without FFmpeg on the host).
- e2e: 1080p 8s source → four renditions; master playlist served as `application/vnd.apple.mpegurl`,
  segments as `video/mp2t`, MP4 range request answered with 206.
- Chaos: four jobs on a 30s 1080p source with three workers and a 30s lease. One worker was killed
  with SIGKILL at 22%. The other jobs ran three at a time; the killed job was re-queued after its
  lease expired and finished on attempt 2. Everything completed in 61s.
- Playback in Chromium through hls.js: all four levels listed, ABR switched up to 1080p.

## Device compatibility

Outputs are chosen for broad compatibility: H.264 High@4.2/High@4.0/Main@3.1, `yuv420p`, AAC-LC
stereo 48 kHz, MPEG-TS segments (HLS version 6 master with `CODECS`/`RESOLUTION`/`FRAME-RATE`),
2s aligned keyframes, frame rate capped at 60, MP4 with `+faststart`.

| Platform | Engine | Expected | Checked |
| --- | --- | --- | --- |
| iOS / iPadOS Safari | native HLS | ✅ | to do before release |
| macOS Safari | native HLS | ✅ | to do before release |
| Android Chrome | hls.js (MSE) | ✅ | to do before release |
| Android ExoPlayer / Media3 apps | native HLS | ✅ | to do before release |
| Chrome, Edge, Firefox (desktop) | hls.js (MSE) | ✅ | Chromium ✅ (local) |
| Smart TVs / older devices | native HLS | Main-profile 480p/360p rungs | spot-check |

Procedure: open `https://media.example.com/?src=<hls_url>` on each device. Confirm playback starts
within a few seconds, seeking works, the quality selector (hls.js) lists every rendition, and
throttling the network (DevTools or Network Link Conditioner) moves playback to a lower rung without
stalling. Apple's `mediastreamvalidator` can also be run against the master playlist.

## Load and performance

1. Upload one video and note its ID.
2. `READY_VIDEO_ID=<id> API_KEY=<key> locust -f tests/load/locustfile.py --host https://api.example.com --users 200 --spawn-rate 20 --run-time 15m --headless`
3. Watch the Grafana dashboard: API p95 latency should stay under 250 ms, queue depth should rise and
   worker pods follow within about a minute, then scale back to zero after the cooldown.
4. Record `transcoder_job_realtime_factor` per rendition set to size worker CPU requests.
