// helios-events: append opencode session lifecycle events to the hub's .helios/events.jsonl.
// The orchestrator watches that file, so a turn steered by hand in the TUI still wakes it.
// A bead worktree lives at <hub>/.claude/worktrees/<bead>; the hub is the path before that.
import { appendFile, mkdir } from "node:fs/promises"
import path from "node:path"

const WANTED = new Set(["session.idle", "session.error"])
const MARK = `${path.sep}.claude${path.sep}worktrees${path.sep}`

function locate(dir) {
  const i = dir.indexOf(MARK)
  if (i < 0) return { hub: dir, bead: null }
  return { hub: dir.slice(0, i), bead: dir.slice(i + MARK.length).split(path.sep)[0] }
}

export const HeliosEvents = async ({ directory }) => {
  const { hub, bead } = locate(process.env.HELIOS_HUB_DIR ?? directory)
  const file = path.join(process.env.HELIOS_HUB ?? hub, ".helios", "events.jsonl")
  return {
    event: async ({ event }) => {
      if (!WANTED.has(event.type)) return
      const props = event.properties ?? {}
      const line = {
        ts: new Date().toISOString(),
        source: "opencode-plugin",
        type: event.type === "session.idle" ? "idle" : "error",
        bead,
        session: props.sessionID ?? null,
        detail: event.type === "session.error" ? String(props.error?.name ?? props.error ?? "") : "",
      }
      try {
        await mkdir(path.dirname(file), { recursive: true })
        await appendFile(file, JSON.stringify(line) + "\n")
      } catch {}
    },
  }
}
