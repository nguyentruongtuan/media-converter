declare global {
  interface Window {
    MEDIA_CONVERTER: { apiBaseUrl: string }
  }
}

const API = window.MEDIA_CONVERTER.apiBaseUrl.replace(/\/$/, "")
const KEY_STORAGE = "media-converter.api-key"

export type Folder = {
  id: string
  name: string
  owner: string
  mine: boolean // an admin also sees other owners' folders
}

export type Video = {
  id: string
  owner: string
  folder_id: string | null
  filename: string
  status: "pending_upload" | "ready" | "invalid"
  size_bytes: number | null
  declared_size_bytes: number
  duration_seconds: number | null
  width: number | null
  height: number | null
  error: string | null
  created_at: string
}

export type Job = {
  id: string
  status: "pending" | "processing" | "completed" | "failed"
  progress: number
  error: string | null
  hls_url: string | null
  thumbnail_url: string | null
}

export type Page<T> = { items: T[]; total: number; limit: number; offset: number }

export type VideoCreated = {
  video: Video
  upload: { url: string; fields: Record<string, string> }
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export async function api<T>(
  apiKey: string,
  method: string,
  path: string,
  body?: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  let res: Response
  try {
    res = await fetch(API + path, {
      method,
      headers: {
        "X-API-Key": apiKey,
        ...(body !== undefined && { "Content-Type": "application/json" }),
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new Error(`Cannot reach the API at ${API}. Check the connection.`)
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) {
    throw new ApiError(data?.error?.message || `API request failed (${res.status})`, res.status)
  }
  return data as T
}

export function readApiKey(): string {
  try {
    return localStorage.getItem(KEY_STORAGE) || ""
  } catch {
    return ""
  }
}

export function saveApiKey(value: string) {
  try {
    if (value) localStorage.setItem(KEY_STORAGE, value)
    else localStorage.removeItem(KEY_STORAGE)
  } catch {
    // Private windows can refuse storage; the key then lasts until the page closes.
  }
}

export function playerUrl(hlsUrl: string): string {
  return "player.html?src=" + encodeURIComponent(hlsUrl)
}

// Plays full screen with remote controls, in this tab because a TV has no way to switch tabs.
export function tvPlayerUrl(hlsUrl: string, title: string): string {
  return `${playerUrl(hlsUrl)}&tv=1&title=${encodeURIComponent(title)}`
}

export function formatBytes(n: number): string {
  if (n < 1024 ** 2) return (n / 1024).toFixed(0) + " KB"
  if (n < 1024 ** 3) return (n / 1024 ** 2).toFixed(1) + " MB"
  return (n / 1024 ** 3).toFixed(2) + " GB"
}

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds)
  const mins = Math.floor(total / 60)
  const secs = String(total % 60).padStart(2, "0")
  if (mins < 60) return `${mins}:${secs}`
  return `${Math.floor(mins / 60)}:${String(mins % 60).padStart(2, "0")}:${secs}`
}
