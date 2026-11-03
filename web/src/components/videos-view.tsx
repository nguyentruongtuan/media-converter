import { useState } from "react"
import { Film, FolderPlus, Play, Trash2 } from "lucide-react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/native-select"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { routeHref } from "@/hooks/use-route"
import type { useFolders } from "@/hooks/use-folders"
import { ALL_VIDEOS, NO_FOLDER, useVideos } from "@/hooks/use-videos"
import { type Folder, formatBytes, formatDuration, type Job, playerUrl, type Video } from "@/lib/api"

function status(video: Video, job: Job | undefined) {
  if (video.status === "pending_upload") return { label: "Not uploaded", variant: "secondary" as const }
  if (video.status === "invalid") return { label: "Invalid", variant: "destructive" as const, error: video.error }
  if (!job) return { label: "Not transcoded", variant: "secondary" as const }
  if (job.status === "pending") return { label: "Queued", variant: "secondary" as const }
  if (job.status === "processing") return { label: "Transcoding", variant: "secondary" as const }
  if (job.status === "failed") return { label: "Failed", variant: "destructive" as const, error: job.error }
  return { label: "Ready", variant: "default" as const }
}

type CardProps = {
  video: Video
  job: Job | undefined
  folders: Folder[]
  onMove: (folderId: string | null) => void
}

function VideoCard({ video, job, folders, onMove }: CardProps) {
  const state = status(video, job)
  const watchUrl = job?.status === "completed" && job.hls_url ? playerUrl(job.hls_url) : null
  const facts = [
    formatBytes(video.size_bytes ?? video.declared_size_bytes),
    video.duration_seconds != null && formatDuration(video.duration_seconds),
    video.width && video.height && `${video.width}×${video.height}`,
  ].filter(Boolean)

  return (
    <Card className="gap-0 overflow-hidden py-0">
      <div className="relative flex aspect-video items-center justify-center bg-muted">
        {job?.thumbnail_url ? (
          <img src={job.thumbnail_url} alt="" loading="lazy" className="absolute inset-0 size-full object-contain" />
        ) : (
          <Film className="size-8 text-muted-foreground" />
        )}
      </div>
      <CardContent className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="min-w-0 font-medium break-words" title={video.filename}>
            {video.filename}
          </p>
          <Badge variant={state.variant}>{state.label}</Badge>
        </div>
        <p className="text-xs text-muted-foreground tabular-nums">{facts.join(" · ")}</p>
        <p className="text-xs text-muted-foreground">{new Date(video.created_at).toLocaleString()}</p>
        {job?.status === "processing" && <Progress value={job.progress * 100} />}
        {state.error && <p className="text-xs break-words text-destructive">{state.error}</p>}
        <NativeSelect
          aria-label={`Folder of ${video.filename}`}
          value={video.folder_id ?? ""}
          onChange={(e) => onMove(e.target.value || null)}
        >
          <option value="">No folder</option>
          {/* A video can only sit in a folder of its own owner. */}
          {folders
            .filter((folder) => folder.owner === video.owner)
            .map((folder) => (
              <option key={folder.id} value={folder.id}>
                {folder.name}
              </option>
            ))}
        </NativeSelect>
        {watchUrl && (
          // A new tab, so that watching does not interrupt an upload running on this page.
          <Button
            className="mt-auto"
            variant="outline"
            nativeButton={false}
            render={<a href={watchUrl} target="_blank" rel="noopener" />}
          >
            <Play />
            Watch
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

type Props = {
  apiKey: string
  uploading: boolean
  onUnauthorized: () => void
  folders: ReturnType<typeof useFolders>
}

export function VideosView({ apiKey, uploading, onUnauthorized, folders }: Props) {
  const { library, error, offset, setOffset, pageSize, folder, openFolder, moveVideo, reload } = useVideos(
    apiKey,
    uploading,
    onUnauthorized,
  )
  const [newName, setNewName] = useState("")
  const [folderError, setFolderError] = useState("")
  const openedFolder = folders.folders.find((f) => f.id === folder)

  async function createFolder() {
    try {
      const created = await folders.create(newName)
      setNewName("")
      setFolderError("")
      openFolder(created.id)
    } catch (err) {
      setFolderError((err as Error).message)
    }
  }

  async function deleteFolder(target: Folder) {
    if (!window.confirm(`Delete the folder "${target.name}"? Its videos stay, outside any folder.`)) return
    try {
      await folders.remove(target.id)
      setFolderError("")
      openFolder(ALL_VIDEOS)
      reload()
    } catch (err) {
      setFolderError((err as Error).message)
    }
  }

  const tabs = [
    { id: ALL_VIDEOS, label: "All videos" },
    { id: NO_FOLDER, label: "No folder" },
    ...folders.folders.map((f) => ({ id: f.id, label: f.mine ? f.name : `${f.name} (${f.owner})` })),
  ]
  const shownError = error || folderError || folders.error

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {tabs.map((tab) => (
          <Button
            key={tab.id}
            size="sm"
            variant={tab.id === folder ? "default" : "outline"}
            aria-pressed={tab.id === folder}
            className="max-w-full"
            onClick={() => openFolder(tab.id)}
          >
            <span className="truncate">{tab.label}</span>
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <form
          className="flex w-full gap-2 sm:max-w-sm"
          onSubmit={(e) => {
            e.preventDefault()
            createFolder()
          }}
        >
          <Input
            aria-label="New folder name"
            placeholder="New folder name"
            maxLength={100}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <Button type="submit" variant="outline" disabled={!newName.trim()}>
            <FolderPlus />
            Create
          </Button>
        </form>
        {openedFolder && (
          <Button variant="outline" onClick={() => deleteFolder(openedFolder)}>
            <Trash2 />
            Delete folder
          </Button>
        )}
      </div>
      {shownError && (
        <Alert variant="destructive">
          <AlertDescription className="break-words">{shownError}</AlertDescription>
        </Alert>
      )}
      {!library && !error && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="aspect-video rounded-xl" />
          ))}
        </div>
      )}
      {library?.videos.total === 0 && (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed p-10 text-center">
          <Film className="size-8 text-muted-foreground" />
          <p className="text-muted-foreground">{folder === ALL_VIDEOS ? "No videos yet." : "No videos here."}</p>
          <Button nativeButton={false} render={<a href={routeHref("upload")} />}>
            Upload a video
          </Button>
        </div>
      )}
      {library && library.videos.total > 0 && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {library.videos.items.map((video) => (
              <VideoCard
                key={video.id}
                video={video}
                job={library.jobs[video.id]}
                folders={folders.folders}
                onMove={(folderId) => moveVideo(video.id, folderId)}
              />
            ))}
          </div>
          {library.videos.total > pageSize && (
            <div className="flex items-center justify-between gap-2">
              <Button variant="outline" disabled={offset === 0} onClick={() => setOffset(offset - pageSize)}>
                Previous
              </Button>
              <span className="text-sm text-muted-foreground tabular-nums">
                {offset + 1}–{Math.min(offset + pageSize, library.videos.total)} of {library.videos.total}
              </span>
              <Button
                variant="outline"
                disabled={offset + pageSize >= library.videos.total}
                onClick={() => setOffset(offset + pageSize)}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
