import { useEffect } from "react"

type Direction = "left" | "right" | "up" | "down"

const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled), [tabindex="0"]'

// Some TV browsers report no usable event.key for the D-pad, but they all send these key codes.
const DIRECTIONS: Record<number, Direction> = { 37: "left", 38: "up", 39: "right", 40: "down" }

export function focusables(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.getClientRects().length > 0)
}

export function moveFocus(target: HTMLElement) {
  target.focus({ preventScroll: true })
  target.scrollIntoView({ block: "center", inline: "nearest" })
}

// Gap along the axis of travel, or null when the candidate is not in that direction.
function travel(from: DOMRect, to: DOMRect, direction: Direction): number | null {
  const gap = {
    left: from.left - to.right,
    right: to.left - from.right,
    up: from.top - to.bottom,
    down: to.top - from.bottom,
  }[direction]
  // Focused cards are scaled up, so neighbours may overlap by a little and still count.
  const overlapAllowance = Math.min(from.width, from.height) * 0.1
  return gap >= -overlapAllowance ? Math.max(gap, 0) : null
}

function nearest(current: HTMLElement, direction: Direction): HTMLElement | undefined {
  const from = current.getBoundingClientRect()
  const horizontal = direction === "left" || direction === "right"
  let best: HTMLElement | undefined
  let bestScore = Infinity

  for (const el of focusables()) {
    if (el === current) continue
    const to = el.getBoundingClientRect()
    const distance = travel(from, to, direction)
    if (distance === null) continue
    // Staying in the same row or column matters more than being close: 0 when the two line up.
    const sideways = horizontal
      ? Math.max(to.top - from.bottom, from.top - to.bottom, 0)
      : Math.max(to.left - from.right, from.left - to.right, 0)
    const centres = horizontal
      ? Math.abs(to.top + to.height / 2 - (from.top + from.height / 2))
      : Math.abs(to.left + to.width / 2 - (from.left + from.width / 2))
    const score = distance + sideways * 2 + centres * 0.1
    if (score < bestScore) {
      best = el
      bestScore = score
    }
  }
  return best
}

// In a text field, left and right move the caret until it reaches the end of the text.
function caretKeepsKey(el: Element, direction: Direction): boolean {
  if (!(el instanceof HTMLInputElement)) return false
  if (direction === "left") return el.selectionStart !== 0
  if (direction === "right") return el.selectionEnd !== el.value.length
  return false
}

// Moves focus with the arrow keys of a remote, to the closest element on screen in that direction.
export function useSpatialNavigation() {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const direction = DIRECTIONS[event.keyCode]
      if (!direction || event.altKey || event.ctrlKey || event.metaKey) return
      const active = document.activeElement
      if (active && caretKeepsKey(active, direction)) return
      event.preventDefault()
      const target =
        active instanceof HTMLElement && active !== document.body ? nearest(active, direction) : focusables()[0]
      if (target) moveFocus(target)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])
}
