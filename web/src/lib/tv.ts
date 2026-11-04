const STORAGE_KEY = "media-converter.tv"
const LAST_VIDEO_KEY = "media-converter.tv.last-video"
const TV_USER_AGENT = /SmartTV|SMART-TV|Tizen|Web0S|HbbTV|BRAVIA|Android TV|GoogleTV|AppleTV|CrKey|AFT[A-Z]|Roku|VIDAA|NetCast/i

function read(storage: Storage, key: string): string | null {
  try {
    return storage.getItem(key)
  } catch {
    return null
  }
}

function write(storage: Storage, key: string, value: string) {
  try {
    storage.setItem(key, value)
  } catch {
    // Private windows can refuse storage; the choice then lasts until the page closes.
  }
}

// ?tv=1 and ?tv=0 override the user agent and stick, for TVs that do not identify themselves.
export function isTvMode(): boolean {
  const choice = new URLSearchParams(window.location.search).get("tv")
  if (choice === "1" || choice === "0") {
    write(localStorage, STORAGE_KEY, choice)
    return choice === "1"
  }
  const stored = read(localStorage, STORAGE_KEY)
  return stored ? stored === "1" : TV_USER_AGENT.test(navigator.userAgent)
}

// The key codes are what Tizen (10009) and webOS (461) remotes send for Back.
export function isBackKey(event: KeyboardEvent): boolean {
  return (
    ["Escape", "Backspace", "GoBack", "BrowserBack"].includes(event.key) ||
    event.keyCode === 10009 ||
    event.keyCode === 461
  )
}

export const readLastVideo = () => read(sessionStorage, LAST_VIDEO_KEY)
export const saveLastVideo = (id: string) => write(sessionStorage, LAST_VIDEO_KEY, id)
