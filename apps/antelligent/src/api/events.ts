import { backend } from "./client";
import type { AntelligentEvent } from "./types";

export type EventHandler = (event: AntelligentEvent) => void;

export async function connectEvents(onEvent: EventHandler, onState: (state: "open" | "closed" | "error") => void): Promise<() => void> {
  const info = await backend();
  const wsUrl = info.baseUrl.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
  const socket = new WebSocket(`${wsUrl}/api/antelligent/events?token=${encodeURIComponent(info.token)}`);
  socket.addEventListener("open", () => onState("open"));
  socket.addEventListener("close", () => onState("closed"));
  socket.addEventListener("error", () => onState("error"));
  socket.addEventListener("message", event => {
    try {
      onEvent(JSON.parse(String(event.data)) as AntelligentEvent);
    } catch {
      onEvent({ type: "event_parse_error", payload: { raw: String(event.data) } });
    }
  });
  return () => socket.close();
}
