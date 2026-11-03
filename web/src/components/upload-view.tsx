import { useState } from "react"
import { UploadCloud } from "lucide-react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import type { useUpload } from "@/hooks/use-upload"
import { routeHref } from "@/hooks/use-route"
import { formatBytes } from "@/lib/api"
import { cn } from "@/lib/utils"

export function UploadView({ upload }: { upload: ReturnType<typeof useUpload> }) {
  const { file, progress, error, watchUrl, pick, start, cancel } = upload
  const [over, setOver] = useState(false)

  return (
    <Card className="mx-auto w-full max-w-2xl">
      <CardContent className="flex flex-col gap-4">
        {progress ? (
          <div className="flex flex-col gap-2" aria-live="polite">
            <p className="break-words">{progress.step}</p>
            <Progress
              value={progress.fraction === null ? 100 : progress.fraction * 100}
              className={cn(progress.fraction === null && "animate-pulse")}
            />
            <p className="min-h-4 text-xs text-muted-foreground tabular-nums">{progress.detail}</p>
            {progress.canCancel && (
              <Button variant="outline" size="lg" className="self-start" onClick={cancel}>
                Cancel
              </Button>
            )}
          </div>
        ) : (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              start()
            }}
          >
            <label
              className={cn(
                "flex min-h-48 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed p-6 text-center transition-colors hover:border-primary focus-within:border-primary",
                over && "border-primary bg-muted",
              )}
              onDragEnter={() => setOver(true)}
              onDragOver={(e) => {
                e.preventDefault()
                setOver(true)
              }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => {
                e.preventDefault()
                setOver(false)
                pick(e.dataTransfer.files[0])
              }}
            >
              {/* video/* opens the photo library and camera on phones, a file dialog on PCs. */}
              <input
                type="file"
                accept="video/*,.mkv,.avi,.ts,.flv,.wmv,.mpg,.mpeg,.3gp"
                className="sr-only"
                onChange={(e) => {
                  pick(e.target.files?.[0])
                  e.target.value = ""
                }}
              />
              <UploadCloud className="mb-2 size-8 text-muted-foreground" />
              <span className="font-medium">Choose a video</span>
              <span className="text-sm break-all text-muted-foreground">
                {file ? `${file.name} (${formatBytes(file.size)})` : "or drop it here"}
              </span>
            </label>
            <Button type="submit" size="lg" disabled={!file}>
              Upload and transcode
            </Button>
          </form>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertDescription className="break-words">{error}</AlertDescription>
          </Alert>
        )}
        {watchUrl && (
          <div className="flex flex-wrap gap-2">
            <Button nativeButton={false} render={<a href={watchUrl} target="_blank" rel="noopener" />}>
              Watch the video
            </Button>
            <Button variant="outline" nativeButton={false} render={<a href={routeHref("videos")} />}>
              Go to uploaded videos
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
