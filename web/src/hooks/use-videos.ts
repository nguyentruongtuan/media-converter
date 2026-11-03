import { useEffect, useState } from "react"

import { api, ApiError, type Job, type Page, type Video } from "@/lib/api"

const PAGE_SIZE = 24
const POLL_MS = 3000

type Library = { videos: Page<Video>; jobs: Record<string, Job> }

const isSettled = (job: Job) => job.status === "completed" || job.status === "failed"

// A video's outputs live on its newest job, which the list endpoint does not include.
async function latestJob(apiKey: string, videoId: string): Promise<Job | undefined> {
  const jobs = await api<Page<Job>>(apiKey, "GET", `/v1/jobs?video_id=${videoId}&limit=1`)
  return jobs.items[0]
}

export function useVideos(apiKey: string, uploading: boolean, onUnauthorized: () => void) {
  const [offset, setOffset] = useState(0)
  const [library, setLibrary] = useState<Library | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const settled = new Map<string, Job>()

    async function refresh() {
      let inProgress = false
      try {
        const videos = await api<Page<Video>>(apiKey, "GET", `/v1/videos?limit=${PAGE_SIZE}&offset=${offset}`)
        const ready = videos.items.filter((v) => v.status === "ready")
        const latest = await Promise.all(ready.map((v) => settled.get(v.id) ?? latestJob(apiKey, v.id)))
        if (stopped) return

        const jobs: Record<string, Job> = {}
        ready.forEach((video, i) => {
          const job = latest[i]
          if (!job) return
          jobs[video.id] = job
          if (isSettled(job)) settled.set(video.id, job)
          else inProgress = true
        })
        setLibrary({ videos, jobs })
        setError("")
      } catch (err) {
        if (stopped) return
        if (err instanceof ApiError && err.status === 401) return onUnauthorized()
        setError((err as Error).message)
        inProgress = true
      }
      if (inProgress || uploading) timer = setTimeout(refresh, POLL_MS)
    }

    refresh()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [apiKey, offset, uploading, onUnauthorized])

  return { library, error, offset, setOffset, pageSize: PAGE_SIZE }
}
