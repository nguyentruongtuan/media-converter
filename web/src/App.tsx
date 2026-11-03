import { useCallback, useState } from "react"

import { ApiKeyForm } from "@/components/api-key-form"
import { AppSidebar, MENU } from "@/components/app-sidebar"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { UploadView } from "@/components/upload-view"
import { VideosView } from "@/components/videos-view"
import { useFolders } from "@/hooks/use-folders"
import { useRoute } from "@/hooks/use-route"
import { useUpload } from "@/hooks/use-upload"
import { readApiKey, saveApiKey } from "@/lib/api"

export default function App() {
  const route = useRoute()
  const [apiKey, setApiKey] = useState(readApiKey)

  const changeKey = useCallback((key: string) => {
    saveApiKey(key)
    setApiKey(key)
  }, [])
  const forgetKey = useCallback(() => changeKey(""), [changeKey])

  const folders = useFolders(apiKey, forgetKey)
  const upload = useUpload(apiKey, folders.folders)
  const uploading = upload.progress !== null

  const fraction = upload.progress?.fraction
  const uploadBadge = uploading ? (fraction == null ? "..." : `${Math.round(fraction * 100)}%`) : null
  const title = MENU.find((item) => item.route === route)?.title

  return (
    <SidebarProvider>
      <AppSidebar route={route} uploadBadge={uploadBadge} onChangeKey={apiKey && !uploading ? forgetKey : null} />
      <SidebarInset>
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="md:hidden" />
          <h1 className="font-semibold">{title}</h1>
        </header>
        <main className="flex-1 p-4 md:p-6">
          {!apiKey ? (
            <ApiKeyForm onSubmit={changeKey} />
          ) : route === "videos" ? (
            <VideosView apiKey={apiKey} uploading={uploading} onUnauthorized={forgetKey} folders={folders} />
          ) : (
            <UploadView upload={upload} />
          )}
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
