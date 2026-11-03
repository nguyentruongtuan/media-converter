import { useSyncExternalStore } from "react"

export type Route = "videos" | "upload"

export const routeHref = (route: Route) => `#/${route}`

function subscribe(onChange: () => void) {
  window.addEventListener("hashchange", onChange)
  return () => window.removeEventListener("hashchange", onChange)
}

function current(): Route {
  return window.location.hash === routeHref("upload") ? "upload" : "videos"
}

// The hash keeps the view across reloads and back/forward, and NGINX needs no SPA fallback.
export function useRoute(): Route {
  return useSyncExternalStore(subscribe, current)
}
