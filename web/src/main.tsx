import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import App from "@/App"
import { TvApp } from "@/components/tv-app"
import { isTvMode } from "@/lib/tv"
import "@/index.css"

const tv = isTvMode()
const dark = window.matchMedia("(prefers-color-scheme: dark)")
// A TV has no reliable color scheme preference, and a dark screen is easier on a dark room.
const applyTheme = () => document.documentElement.classList.toggle("dark", tv || dark.matches)
applyTheme()
dark.addEventListener("change", applyTheme)
document.documentElement.toggleAttribute("data-tv", tv)

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {tv ? <TvApp /> : <App />}
  </StrictMode>,
)
