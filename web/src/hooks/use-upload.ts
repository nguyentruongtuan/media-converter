import { useCallback, useEffect, useRef, useState } from "react"

import { api, formatBytes, type Job, playerUrl, saveApiKey, type VideoCreated } from "@/lib/api"

const POLL_MS = 2000
// Android pickers can return names like "video:1234", which the extension allow-list would reject.
const EXTENSIONS: Record<string, string> = {
  "video/mp4": ".mp4",
  "video/quicktime": ".mov",
  "video/webm": ".webm",
  "video/3gpp": ".3gp",
  "video/x-matroska": ".mkv",
}

class Cancelled extends Error {}

type Run = { xhr: XMLHttpRequest | null; cancelled: boolean }

export type UploadProgress = {
  step: string
  detail: string
  fraction: number | null // null while the length of the step is unknown
  canCancel: boolean
}

function uploadName(file: File) {
  let name = file.name.replace(/[/\\]/g, "_")
  if (!/\.[a-z0-9]+$/i.test(name)) name += EXTENSIONS[file.type] || ""
  return name.slice(-255)
}

// Some systems report other types for video files (.ts as a Qt translation file, .mkv as nothing).
function uploadType(file: File) {
  return file.type.startsWith("video/") ? file.type : "application/octet-stream"
}

// XHR rather than fetch: fetch has no upload progress events.
function send(run: Run, upload: VideoCreated["upload"], file: File, onProgress: (fraction: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const form = new FormData()
    Object.entries(upload.fields).forEach(([name, value]) => form.append(name, value))
    form.append("file", file) // S3 ignores any form field after the file
    const xhr = new XMLHttpRequest()
    run.xhr = xhr
    xhr.open("POST", upload.url)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`Storage rejected the upload (${xhr.status})`)))
    xhr.onerror = () =>
      reject(new Error(`Upload interrupted. Check the connection to ${new URL(upload.url).host} and try again.`))
    xhr.onabort = () => reject(new Cancelled())
    xhr.send(form)
  })
}

// Lives above the views so that moving to another menu item does not interrupt a running upload.
export function useUpload(apiKey: string) {
  const [file, setFile] = useState<File | null>(null)
  const [progress, setProgress] = useState<UploadProgress | null>(null)
  const [error, setError] = useState("")
  const [watchUrl, setWatchUrl] = useState("")
  const run = useRef<Run | null>(null)
  const wakeLock = useRef<WakeLockSentinel | null>(null)

  // Phones suspend the page, and with it the upload, when the screen locks.
  const keepAwake = useCallback(async () => {
    try {
      wakeLock.current = (await navigator.wakeLock?.request("screen")) ?? null
    } catch {
      // Refused, for example on low battery: the upload still runs while the screen stays on.
    }
  }, [])

  const pick = useCallback((picked: File | undefined) => {
    if (!picked || run.current) return
    setFile(picked)
    setError("")
    setWatchUrl("")
  }, [])

  const cancel = useCallback(() => {
    if (!run.current) return
    run.current.cancelled = true
    run.current.xhr?.abort()
  }, [])

  async function start() {
    if (!file || run.current) return
    const current: Run = { xhr: null, cancelled: false }
    run.current = current
    setError("")
    setWatchUrl("")
    let videoId: string | null = null
    let uploaded = false

    const show = (step: string, canCancel: boolean, fraction: number | null = 0) =>
      setProgress({ step, detail: "", fraction, canCancel })

    async function waitForJob(jobId: string): Promise<Job> {
      for (;;) {
        let job: Job | null = null
        try {
          job = await api<Job>(apiKey, "GET", `/v1/jobs/${jobId}`)
        } catch {
          // The work continues on the server, so a phone losing signal for a moment must not fail the run.
        }
        if (job?.status === "completed") return job
        if (job?.status === "failed") throw new Error(job.error || "Transcoding failed")
        const update = job
          ? { fraction: job.progress, detail: `${job.status}, ${Math.round(job.progress * 100)}%` }
          : { detail: "Reconnecting..." }
        setProgress((p) => p && { ...p, ...update })
        await new Promise((r) => setTimeout(r, POLL_MS))
      }
    }

    try {
      saveApiKey(apiKey)
      show("Preparing upload...", true)
      const created = await api<VideoCreated>(apiKey, "POST", "/v1/videos", {
        filename: uploadName(file),
        content_type: uploadType(file),
        size_bytes: file.size,
      })
      videoId = created.video.id
      if (current.cancelled) throw new Cancelled()

      show("Uploading...", true)
      await keepAwake()
      await send(current, created.upload, file, (fraction) =>
        setProgress(
          (p) => p && { ...p, fraction, detail: `${formatBytes(fraction * file.size)} of ${formatBytes(file.size)}` },
        ),
      )
      uploaded = true

      show("Checking the video...", false, null)
      await api(apiKey, "POST", `/v1/videos/${videoId}/complete`)

      show("Transcoding... You can close this page; the job keeps running.", false)
      const job = await api<Job>(apiKey, "POST", `/v1/videos/${videoId}/jobs`, {}, { "Idempotency-Key": videoId })
      const done = await waitForJob(job.id)
      if (done.hls_url) setWatchUrl(playerUrl(done.hls_url))
      setFile(null)
    } catch (err) {
      if (!(err instanceof Cancelled)) setError((err as Error).message)
      // Without the file the row is useless; a rejected file was already marked invalid by the API.
      if (!uploaded && videoId) api(apiKey, "DELETE", `/v1/videos/${videoId}`).catch(() => {})
    } finally {
      run.current = null
      setProgress(null)
      wakeLock.current?.release().catch(() => {})
      wakeLock.current = null
    }
  }

  useEffect(() => {
    // A file dropped beside the target would otherwise navigate away from the page.
    const ignore = (e: DragEvent) => e.preventDefault()
    const warn = (e: BeforeUnloadEvent) => {
      const xhr = run.current?.xhr
      if (xhr && !run.current?.cancelled && xhr.readyState !== 4) e.preventDefault()
    }
    const resume = () => {
      const xhr = run.current?.xhr
      if (document.visibilityState === "visible" && xhr && xhr.readyState < 4) keepAwake()
    }
    window.addEventListener("dragover", ignore)
    window.addEventListener("drop", ignore)
    window.addEventListener("beforeunload", warn)
    document.addEventListener("visibilitychange", resume)
    return () => {
      window.removeEventListener("dragover", ignore)
      window.removeEventListener("drop", ignore)
      window.removeEventListener("beforeunload", warn)
      document.removeEventListener("visibilitychange", resume)
    }
  }, [keepAwake])

  return { file, progress, error, watchUrl, pick, start, cancel }
}
