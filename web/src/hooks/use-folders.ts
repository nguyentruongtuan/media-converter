import { useCallback, useEffect, useState } from "react"

import { api, ApiError, type Folder } from "@/lib/api"

// Lives above the views so that the upload picker sees folders created on the videos page.
export function useFolders(apiKey: string, onUnauthorized: () => void) {
  const [folders, setFolders] = useState<Folder[]>([])
  const [error, setError] = useState("")

  useEffect(() => {
    if (!apiKey) return
    let stopped = false
    api<{ items: Folder[] }>(apiKey, "GET", "/v1/folders")
      .then((list) => {
        if (stopped) return
        setFolders(list.items)
        setError("")
      })
      .catch((err) => {
        if (stopped) return
        if (err instanceof ApiError && err.status === 401) return onUnauthorized()
        setError((err as Error).message)
      })
    return () => {
      stopped = true
    }
  }, [apiKey, onUnauthorized])

  const create = useCallback(
    async (name: string) => {
      const folder = await api<Folder>(apiKey, "POST", "/v1/folders", { name })
      setFolders((list) => [...list, folder].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)))
      return folder
    },
    [apiKey],
  )

  const remove = useCallback(
    async (id: string) => {
      await api(apiKey, "DELETE", `/v1/folders/${id}`)
      setFolders((list) => list.filter((f) => f.id !== id))
    },
    [apiKey],
  )

  return { folders, error, create, remove }
}
