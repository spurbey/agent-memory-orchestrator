import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { askRoom, chat, context, continueRoom, messages, rooms, status } from "../api/client";
import { connectEvents } from "../api/events";
import type { AntelligentEvent } from "../api/types";
import { qs } from "./dom";
import { renderBubble, renderEvents, renderMode, renderShell, renderView, setConnection } from "./render";
import { createInitialState, type AppState, type AppView } from "./state";

export function mountBubble(root: HTMLElement): void {
  renderBubble(root);
  enableWindowDrag("#openPanel", { allowInteractive: true });
  qs<HTMLButtonElement>("#openPanel")?.addEventListener("click", () => {
    const bubble = qs<HTMLButtonElement>("#openPanel");
    if (bubble?.dataset.dragSuppress === "true") return;
    bubble?.classList.add("is-launching");
    window.setTimeout(() => {
      invoke("show_panel")
        .catch(() => undefined)
        .finally(() => bubble?.classList.remove("is-launching"));
    }, 80);
  });
}

export class AntelligentController {
  private readonly state: AppState = createInitialState();
  private disconnectEvents: (() => void) | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly root: HTMLElement) {}

  start(): void {
    renderShell(this.root);
    this.bindShell();
    this.renderAll();
    void this.boot();
  }

  private bindShell(): void {
    enableWindowDrag(".app-window");
    enableWindowDrag(".rail-brand", { allowInteractive: true });
    qs<HTMLButtonElement>("#hidePanel")?.addEventListener("click", () => this.hidePanel());
    qs<HTMLButtonElement>("#refreshAll")?.addEventListener("click", () => void this.boot());
    document.querySelectorAll<HTMLButtonElement>("[data-view]").forEach(button => {
      button.addEventListener("click", () => this.setView((button.dataset.view || "retrieval") as AppView));
    });
  }

  private bindView(): void {
    qs<HTMLFormElement>("#retrievalForm")?.addEventListener("submit", event => {
      event.preventDefault();
      void this.sendRetrieval();
    });
    qs<HTMLButtonElement>("#openResultRoom")?.addEventListener("click", button => {
      const roomId = (button.currentTarget as HTMLButtonElement).dataset.room || this.state.lastChatResult?.room_id || "";
      if (roomId) void this.selectRoom(roomId);
    });
    qs<HTMLButtonElement>("#openRoomsTab")?.addEventListener("click", () => this.setView("rooms"));
    qs<HTMLFormElement>("#roomComposer")?.addEventListener("submit", event => {
      event.preventDefault();
      void this.sendRoomQuestion();
    });
    qs<HTMLButtonElement>("#continueRoom")?.addEventListener("click", () => void this.continueSelectedRoom());
    document.querySelectorAll<HTMLButtonElement>("[data-room]").forEach(button => {
      button.addEventListener("click", () => void this.selectRoom(button.dataset.room || ""));
    });
  }

  private setView(view: AppView): void {
    this.state.view = view;
    this.renderAll();
  }

  private hidePanel(): void {
    const panel = document.querySelector<HTMLElement>(".app-window");
    if (!panel || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      void invoke("hide_panel").catch(() => undefined);
      return;
    }
    panel.classList.remove("opening");
    panel.classList.add("closing");
    window.setTimeout(() => invoke("hide_panel").catch(() => undefined), 170);
  }

  private async boot(): Promise<void> {
    try {
      setConnection("connecting");
      await this.refreshStatus();
      await this.refreshRooms();
      setConnection("online");
      this.disconnectEvents?.();
      this.disconnectEvents = await connectEvents(event => this.onEvent(event), connection => {
        this.state.online = connection === "open";
        setConnection(connection === "open" ? "online" : connection);
        if (connection === "open") this.clearReconnect();
        if (connection === "closed" || connection === "error") this.scheduleReconnect();
      });
    } catch (error) {
      setConnection("offline");
      this.addEvent("ui_error", { error: String(error) });
      this.scheduleReconnect();
      this.renderAll();
    }
  }

  private async refreshStatus(): Promise<void> {
    this.state.status = await status();
    this.renderAll();
  }

  private async refreshRooms(): Promise<void> {
    const payload = await rooms();
    this.state.rooms = payload.rooms || [];
    if (this.state.selectedRoomId && !this.state.rooms.some(room => room.room_id === this.state.selectedRoomId)) {
      this.state.selectedRoomId = "";
      this.state.messages = [];
      this.state.context = null;
    }
    this.renderAll();
  }

  private async selectRoom(roomId: string): Promise<void> {
    if (!roomId) return;
    this.state.selectedRoomId = roomId;
    this.state.view = "chat";
    const [messagePayload, contextPayload] = await Promise.all([messages(roomId), context(roomId)]);
    this.state.messages = messagePayload.messages || [];
    this.state.context = contextPayload.context || null;
    this.renderAll();
  }

  private async sendRetrieval(): Promise<void> {
    const input = qs<HTMLTextAreaElement>("#retrievalInput");
    const query = input?.value.trim() || "";
    if (!query) return;
    this.state.busy = true;
    this.state.lastQuery = query;
    renderMode("retrieving");
    this.addEvent("retrieval_query", { query });
    this.renderAll();
    try {
      const result = await chat(query);
      this.state.lastChatResult = result;
      if (result.room_id) this.state.selectedRoomId = result.room_id;
      this.addEvent("retrieval_result", { mode: result.mode, room_id: result.room_id });
      if (input) input.value = "";
      await this.refreshRooms();
    } catch (error) {
      this.state.lastChatResult = { ok: false, mode: "failed", answer: "", room_id: "", error: String(error) };
      this.addEvent("retrieval_error", { error: String(error) });
    } finally {
      this.state.busy = false;
      renderMode("idle");
      this.renderAll();
    }
  }

  private async sendRoomQuestion(): Promise<void> {
    const input = qs<HTMLTextAreaElement>("#chatInput");
    const query = input?.value.trim() || "";
    if (!query || !this.state.selectedRoomId) return;
    this.state.busy = true;
    renderMode("room ask");
    this.addEvent("room_followup", { room_id: this.state.selectedRoomId, query });
    this.renderAll();
    try {
      await askRoom(this.state.selectedRoomId, query);
      if (input) input.value = "";
      await this.selectRoom(this.state.selectedRoomId);
    } catch (error) {
      this.addEvent("room_error", { error: String(error) });
    } finally {
      this.state.busy = false;
      renderMode("idle");
      this.renderAll();
    }
  }

  private async continueSelectedRoom(): Promise<void> {
    if (!this.state.selectedRoomId) return;
    renderMode("continuing");
    this.addEvent("planner_continue", { room_id: this.state.selectedRoomId });
    try {
      await continueRoom(this.state.selectedRoomId);
      await this.selectRoom(this.state.selectedRoomId);
    } catch (error) {
      this.addEvent("continue_error", { error: String(error) });
    } finally {
      renderMode("idle");
    }
  }

  private onEvent(event: AntelligentEvent): void {
    this.state.events.unshift(event);
    this.state.events = this.state.events.slice(0, 8);
    renderEvents(this.state.events);
    if (event.type !== "heartbeat") void this.refreshRooms();
  }

  private addEvent(type: string, payload: Record<string, unknown>): void {
    this.onEvent({ type, payload, created_at_ms: Date.now() });
  }

  private renderAll(): void {
    renderView(this.state);
    this.bindView();
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.boot();
    }, 4000);
  }

  private clearReconnect(): void {
    if (!this.reconnectTimer) return;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }
}

function enableWindowDrag(selector: string, options: { allowInteractive?: boolean } = {}): void {
  const target = document.querySelector<HTMLElement>(selector);
  if (!target) return;
  target.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    if (!options.allowInteractive && (event.target as HTMLElement).closest("button, textarea, input, a")) return;
    const startX = event.clientX;
    const startY = event.clientY;
    const appWindow = getCurrentWindow();
    let dragging = false;
    const cleanup = () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", cleanup);
    };
    const onMove = (moveEvent: PointerEvent) => {
      if (dragging) return;
      const distance = Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY);
      if (distance < 4) return;
      dragging = true;
      target.dataset.dragSuppress = "true";
      target.classList.add("is-dragging");
      void appWindow.startDragging().catch(() => undefined);
      cleanup();
      window.setTimeout(() => {
        target.classList.remove("is-dragging");
        delete target.dataset.dragSuppress;
      }, 300);
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener(
      "pointerup",
      () => {
        if (!dragging) target.classList.remove("is-dragging");
        cleanup();
      },
      { once: true },
    );
  });
}
