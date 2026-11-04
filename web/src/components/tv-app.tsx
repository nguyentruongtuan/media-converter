import { useCallback, useEffect, useRef, useState } from "react"
import { Clapperboard, Film, KeyRound, MonitorX, Play } from "lucide-react"

import { ApiKeyForm } from "@/components/api-key-form"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { useFolders } from "@/hooks/use-folders"
import { focusables, moveFocus, useSpatialNavigation } from "@/hooks/use-spatial-navigation"
import { ALL_VIDEOS, NO_FOLDER, useVideos } from "@/hooks/use-videos"
import { formatDuration, type Job, readApiKey, saveApiKey, tvPlayerUrl, type Video } from "@/lib/api"
import { isBackKey, readLastVideo, saveLastVideo } from "@/lib/tv"
import { cn } from "@/lib/utils"

const CONTROL =
  "inline-flex h-14 shrink-0 items-center gap-3 rounded-xl px-6 text-lg font-medium whitespace-nowrap transition-transform focus:scale-105 [&_svg]:size-6"
const NOT_PLAYABLE: Partial<Record<Video["status"], string>> = { pending_upload: "Not uploaded", invalid: "Invalid" }

function jobLabel(job: Job | undefined): string {
  if (!job) return "Not transcoded"
  if (job.status === "pending") return "Queued"
  if (job.status === "processing") return "Transcoding"
  if (job.status === "failed") return "Failed"
  return ""
}

function VideoCard({ video, job }: { video: Video; job: Job | undefined }) {
  const label = NOT_PLAYABLE[video.status] ?? jobLabel(job)
  const url = job?.status === "completed" && job.hls_url ? tvPlayerUrl(job.hls_url, video.filename) : null
  const body = (
    <>
      <div className="relative flex aspect-video items-center justify-center overflow-hidden rounded-xl bg-muted">
        {job?.thumbnail_url ? (
          <img src={job.thumbnail_url} alt="" loading="lazy" className="absolute inset-0 size-full object-contain" />
        ) : (
          <Film className={cn("size-16 text-muted-foreground", url && "group-focus:opacity-0")} />
        )}
        {url && (
          <Play className="absolute size-16 fill-white text-white opacity-0 drop-shadow-lg transition-opacity group-focus:opacity-100" />
        )}
        {label && (
          <span className="absolute inset-x-0 bottom-0 bg-black/75 px-4 py-2 text-center text-base text-white">
            {label}
          </span>
        )}
      </div>
      {job?.status === "processing" && <Progress value={job.progress * 100} className="mt-2" />}
      <p className="mt-3 line-clamp-2 text-lg font-medium break-words">{video.filename}</p>
      {video.duration_seconds != null && (
        <p className="text-base text-muted-foreground tabular-nums">{formatDuration(video.duration_seconds)}</p>
      )}
    </>
  )
  const shared = {
    "data-video-id": video.id,
    className: cn("group block rounded-xl p-2 transition-transform focus:z-10 focus:scale-105", !url && "opacity-70"),
  }

  // Unplayable cards still take focus: the D-pad has to reach them to read their status.
  return url ? (
    <a {...shared} href={url} onClick={() => saveLastVideo(video.id)}>
      {body}
    </a>
  ) : (
    <div {...shared} tabIndex={0}>
      {body}
    </div>
  )
}

function TvLibrary({ apiKey, onForgetKey }: { apiKey: string; onForgetKey: () => void }) {
  const folders = useFolders(apiKey, onForgetKey)
  const { library, error, offset, setOffset, pageSize, folder, openFolder } = useVideos(apiKey, false, onForgetKey)
  const paged = useRef(false)

  // Back returns to the folder row before the TV leaves the app.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const active = document.activeElement
      if (!isBackKey(event) || active?.closest("[data-tv-tabs]")) return
      const tab = document.querySelector<HTMLElement>('[data-tv-tabs] [aria-pressed="true"]')
      if (!tab) return
      event.preventDefault()
      moveFocus(tab)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  // A remote has no pointer, so something must hold focus; polling must never take it from the viewer.
  useEffect(() => {
    if (!library) return
    if (!paged.current && document.activeElement !== document.body) return
    const cards = document.querySelectorAll<HTMLElement>("[data-video-id]")
    const last = paged.current ? null : [...cards].find((card) => card.dataset.videoId === readLastVideo())
    const target = last ?? cards[0] ?? focusables()[0]
    paged.current = false
    if (target) moveFocus(target)
  }, [library])

  function page(nextOffset: number) {
    paged.current = true
    setOffset(nextOffset)
  }

  const tabs = [
    { id: ALL_VIDEOS, label: "All videos" },
    { id: NO_FOLDER, label: "No folder" },
    ...folders.folders.map((f) => ({ id: f.id, label: f.mine ? f.name : `${f.name} (${f.owner})` })),
  ]
  const shownError = error || folders.error
  const total = library?.videos.total ?? 0

  return (
    <div className="flex flex-col gap-8 px-[5vw] py-[5vh]">
      <header className="flex flex-wrap items-center justify-between gap-6">
        <div className="flex items-center gap-4 text-3xl font-semibold">
          <Clapperboard className="size-10" />
          Media Converter
        </div>
        <div className="flex flex-wrap gap-3">
          <button className={cn(CONTROL, "bg-secondary")} onClick={onForgetKey}>
            <KeyRound />
            Change API key
          </button>
          <a className={cn(CONTROL, "bg-secondary")} href="?tv=0">
            <MonitorX />
            Exit TV mode
          </a>
        </div>
      </header>
      {/* Padding leaves room for the focus scale and outline, which overflow-x would otherwise clip. */}
      <nav data-tv-tabs aria-label="Folders" className="-m-3 flex gap-3 overflow-x-auto p-3 [scrollbar-width:none]">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            aria-pressed={tab.id === folder}
            className={cn(CONTROL, tab.id === folder ? "bg-primary text-primary-foreground" : "bg-secondary")}
            onClick={() => openFolder(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      {shownError && <p className="rounded-xl bg-destructive/20 p-6 text-xl break-words">{shownError}</p>}
      {!library && !error && (
        <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="aspect-video rounded-xl" />
          ))}
        </div>
      )}
      {library && total === 0 && (
        <div className="flex flex-col items-center gap-4 rounded-xl border-2 border-dashed p-16 text-center">
          <Film className="size-16 text-muted-foreground" />
          <p className="text-2xl">{folder === ALL_VIDEOS ? "No videos yet." : "No videos here."}</p>
          <p className="text-xl text-muted-foreground">To upload, exit TV mode on a phone or computer.</p>
        </div>
      )}
      {library && total > 0 && (
        <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
          {library.videos.items.map((video) => (
            <VideoCard key={video.id} video={video} job={library.jobs[video.id]} />
          ))}
        </div>
      )}
      {total > pageSize && (
        <div className="flex items-center justify-center gap-8">
          {offset > 0 && (
            <button className={cn(CONTROL, "bg-secondary")} onClick={() => page(offset - pageSize)}>
              Previous
            </button>
          )}
          <span className="text-xl text-muted-foreground tabular-nums">
            {offset + 1}–{Math.min(offset + pageSize, total)} of {total}
          </span>
          {offset + pageSize < total && (
            <button className={cn(CONTROL, "bg-secondary")} onClick={() => page(offset + pageSize)}>
              Next
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// The lean-back app for watching from a couch with a remote. Uploading and organising stay on phones
// and PCs: both need typing or a file picker, which a remote does not offer.
export function TvApp() {
  const [apiKey, setApiKey] = useState(readApiKey)
  useSpatialNavigation()

  const changeKey = useCallback((key: string) => {
    saveApiKey(key)
    setApiKey(key)
  }, [])
  const forgetKey = useCallback(() => changeKey(""), [changeKey])

  useEffect(() => {
    if (!apiKey) focusables()[0]?.focus()
  }, [apiKey])

  if (apiKey) return <TvLibrary apiKey={apiKey} onForgetKey={forgetKey} />
  return (
    <main className="flex min-h-svh items-center justify-center p-[5vw]">
      <ApiKeyForm onSubmit={changeKey} />
    </main>
  )
}
