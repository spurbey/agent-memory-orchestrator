import type { AgentRoom, AntelligentEvent, RoomMessage, SupportRef } from "../api/types";
import { confidenceLabel, compactTime, escapeHtml, initials, truncate } from "./dom";
import type { AppState, AppView } from "./state";
import { localNodeId, selectedRoom } from "./state";

export function renderBubble(root: HTMLElement): void {
  root.innerHTML = `
    <button class="ant-bubble idle" id="openPanel" aria-label="Open Antelligent">
      <span class="bubble-signal"></span>
      <span class="bubble-ant" aria-hidden="true">${antIcon()}</span>
    </button>
  `;
}

export function renderShell(root: HTMLElement): void {
  root.innerHTML = `
    <main class="panel-shell">
      <section class="app-window" aria-label="Antelligent companion">
        <nav class="side-rail" aria-label="Antelligent sections">
          <button class="rail-brand" title="Antelligent" aria-label="Antelligent">${antIcon()}</button>
          ${navButton("retrieval", "Retrieval", "retrieval")}
          ${navButton("chat", "Live Chat", "chat")}
          ${navButton("rooms", "Rooms", "rooms")}
        </nav>
        <section class="stage">
          <header class="top-bar">
            <div class="top-title">
              <p class="eyebrow" id="viewEyebrow">local first</p>
              <h1 id="viewTitle">Retrieval</h1>
              <div class="room-meta" id="viewSubtitle">Ask AMO memory. Peer room opens only when local confidence is low.</div>
            </div>
            <div class="top-actions">
              <span id="connectionPill" class="status-pill warn">connecting</span>
              <span id="modePill" class="status-pill">idle</span>
              <button id="refreshAll" class="icon-btn">Refresh</button>
              <button id="hidePanel" class="icon-btn">Hide</button>
            </div>
          </header>
          <section class="view-root" id="viewRoot"></section>
          <section class="event-strip" id="eventStrip" aria-label="Agent events"></section>
        </section>
      </section>
    </main>
  `;
}

export function setConnection(value: string): void {
  const pill = document.querySelector("#connectionPill");
  if (!pill) return;
  pill.textContent = value;
  pill.className = `status-pill ${value === "online" ? "good" : value === "offline" || value === "error" ? "bad" : "warn"}`;
}

export function renderMode(value: string): void {
  const pill = document.querySelector("#modePill");
  if (pill) pill.textContent = value;
}

export function renderView(state: AppState): void {
  setActiveNav(state.view);
  renderTopCopy(state);
  const root = document.querySelector("#viewRoot");
  if (!root) return;
  if (state.view === "retrieval") root.innerHTML = retrievalView(state);
  if (state.view === "chat") root.innerHTML = liveChatView(state);
  if (state.view === "rooms") root.innerHTML = roomsView(state);
  renderEvents(state.events);
}

export function renderEvents(events: AntelligentEvent[]): void {
  const target = document.querySelector("#eventStrip");
  if (!target) return;
  target.innerHTML = events.length ? events.map(event => `<span>${escapeHtml(event.type)}</span>`).join("") : `<span>waiting for activity</span>`;
}

function retrievalView(state: AppState): string {
  return `<section class="retrieval-screen">
    <div class="retrieval-thread">
      <div class="date-pill">AMO memory</div>
      ${state.lastQuery ? retrievalQueryBubble(state.lastQuery) : retrievalEmpty()}
      ${state.busy ? retrievalWorkingBubble() : renderChatResult(state.lastChatResult)}
    </div>
    <form id="retrievalForm" class="retrieval-composer">
      <textarea id="retrievalInput" rows="1" placeholder="Ask AMO memory..."></textarea>
      <button class="primary-btn" type="submit">Search</button>
    </form>
    <section class="readiness-line">${readinessSummary(state)}</section>
  </section>`;
}

function liveChatView(state: AppState): string {
  const room = selectedRoom(state);
  if (!room) {
    return `<section class="empty-chat-view">
      <div class="empty-conversation compact-empty">
        <span class="empty-mark">${antIcon()}</span>
        <h2>No active room</h2>
        <p>Select a room from Rooms, or run a retrieval query that needs peer help.</p>
        <button id="openRoomsTab" class="ghost-btn" type="button">Open Rooms</button>
      </div>
    </section>`;
  }
  return `<section class="chat-layout">
    <section class="chat-main">
      ${taskBanner(room)}
      <div id="agentConversation" class="agent-conversation">${state.messages.map(message => renderMessage(message, localNodeId(state), room)).join("") || emptyRoom(room)}</div>
      <form id="roomComposer" class="composer">
        <div id="tagChips" class="tag-chips">${tagChips(room)}</div>
        <div class="composer-input-row">
          <textarea id="chatInput" rows="1" placeholder="Ask inside this room..."></textarea>
          <div class="composer-actions">
            <button id="continueRoom" class="ghost-btn" type="button">Continue</button>
            <button id="askRoom" class="primary-btn" type="submit">Ask Room</button>
          </div>
        </div>
      </form>
    </section>
    <aside class="context-drawer">${contextDrawer(state, room)}</aside>
  </section>`;
}

function roomsView(state: AppState): string {
  return `<section class="rooms-screen">
    <div class="rooms-head">
      <div>
        <p class="eyebrow">room history</p>
        <h2>Created and joined rooms</h2>
      </div>
      <span>${state.rooms.length} rooms</span>
    </div>
    <div class="rooms-list-large">
      ${state.rooms.length ? state.rooms.map(renderRoomCard).join("") : `<div class="empty small-empty">No rooms yet. Retrieval creates one when local confidence is low.</div>`}
    </div>
  </section>`;
}

function renderChatResult(result: AppState["lastChatResult"]): string {
  if (!result) {
    return "";
  }
  const ok = result.ok !== false;
  const citations = result.citations || [];
  return `<article class="agent-message retrieval-answer${ok ? "" : " error"}">
    <div class="agent-avatar">${antIcon()}</div>
    <div class="message-stack">
      <header><strong>AMO</strong><span>${escapeHtml(result.mode || "result")}</span>${result.room_id ? `<span>room opened</span>` : ""}</header>
      <div class="message-bubble">
        <p>${escapeHtml(result.answer || result.error || result.reason || "No answer returned.")}</p>
        ${result.room_id ? `<button id="openResultRoom" data-room="${escapeHtml(result.room_id)}" class="inline-room-link" type="button">View live room</button>` : ""}
        ${citations.length ? `<footer class="support-row">${citations.slice(0, 5).map(item => `<code>${escapeHtml(supportLabel(item))}</code>`).join("")}</footer>` : ""}
      </div>
    </div>
  </article>`;
}

function retrievalEmpty(): string {
  return `<div class="retrieval-empty">
    <span>${railIcon("retrieval")}</span>
    <p>Ask a memory question. AMO answers locally when confidence is good and opens a peer room only when it needs help.</p>
  </div>`;
}

function retrievalQueryBubble(query: string): string {
  return `<article class="agent-message self retrieval-query">
    <div class="agent-avatar">You</div>
    <div class="message-stack">
      <header><strong>Initiator</strong><span>memory request</span></header>
      <div class="message-bubble"><p>${escapeHtml(query)}</p></div>
    </div>
  </article>`;
}

function retrievalWorkingBubble(): string {
  return `<article class="agent-message retrieval-answer">
    <div class="agent-avatar">${antIcon()}</div>
    <div class="message-stack">
      <header><strong>AMO</strong><span>working</span></header>
      <div class="message-bubble">${workingBlock("Searching local memory, then checking whether a peer room is needed")}</div>
    </div>
  </article>`;
}

function renderRoomCard(room: AgentRoom): string {
  const topic = room.topic || room.agent_state?.original_query || room.room_id;
  const status = room.agent_state?.status || "open";
  const participants = room.participants?.length || 0;
  const final = room.agent_state?.final?.answer;
  return `<button class="room-row" data-room="${escapeHtml(room.room_id)}" type="button">
    <span class="room-glyph">${escapeHtml(initials(topic))}</span>
    <span class="room-row-copy"><strong>${escapeHtml(topic)}</strong><small>${escapeHtml(status)} / ${participants} participants${room.updated_at ? ` / ${escapeHtml(compactTime(room.updated_at))}` : ""}</small>${final ? `<em>${escapeHtml(truncate(final, 150))}</em>` : ""}</span>
  </button>`;
}

function renderMessage(message: RoomMessage, localId: string, room: AgentRoom): string {
  const sender = message.from_node_id || message.from || "agent";
  const isInitiator = Boolean(room.initiator_node_id && sender === room.initiator_node_id);
  const isLocal = Boolean(localId && sender === localId);
  const isInitiatorTurn = isLocal || isInitiator || message.type === "context_request" || message.type === "final_synthesis";
  const side = isInitiatorTurn ? " self" : "";
  const mode = String(message.metadata?.mode || message.type || "message");
  const confidence = confidenceLabel(message.confidence);
  const targetNames = message.to_node_ids?.length ? `<span>to ${escapeHtml(message.to_node_ids.join(", "))}</span>` : "";
  return `<article class="agent-message${side} ${escapeHtml(message.type || "message")}">
    <div class="agent-avatar">${escapeHtml(initials(sender))}</div>
    <div class="message-stack">
      <header><strong>${escapeHtml(displaySender(sender, room))}</strong><span>${escapeHtml(compactTime(message.created_at))}</span><span>${escapeHtml(mode)}</span>${targetNames}${confidence ? `<span>${confidence}</span>` : ""}</header>
      <div class="message-bubble"><p>${escapeHtml(message.content || "No content attached to this room event.")}</p>${messageTelemetry(message)}</div>
    </div>
  </article>`;
}

function contextDrawer(state: AppState, room: AgentRoom): string {
  const layers = state.context?.layers || {};
  const roster = layers.room_roster?.length ? layers.room_roster : (room.participants || []).map(node_id => ({ node_id }));
  const final = room.agent_state?.final;
  return `<div class="drawer-head"><div><p class="eyebrow">context</p><strong>Current room</strong></div></div>
    ${contextBlock("Brief", layers.room_md || room.agent_state?.original_query || room.topic || "No room brief yet.")}
    ${contextBlock("Summary", layers.rolling_summary_md || "No rolling summary yet.")}
    <section class="context-card"><h3>Participants</h3><div class="roster-grid">${roster.map(renderRosterChip).join("") || "<p>No participants yet.</p>"}</div></section>
    ${final?.answer ? contextBlock("Final", final.answer, "final") : ""}
    ${final?.citations?.length ? `<section class="context-card"><h3>Support</h3>${final.citations.map(renderSupport).join("")}</section>` : ""}`;
}

function taskBanner(room: AgentRoom): string {
  const task = room.agent_state?.original_query || room.topic || room.room_id;
  return `<div class="task-card"><span class="task-icon">${railIcon("retrieval")}</span><p><strong>Task</strong>${escapeHtml(task)}</p></div>`;
}

function emptyRoom(room: AgentRoom): string {
  return `<div class="empty-conversation compact-empty"><h2>No messages yet</h2><p>${escapeHtml(room.topic || "This room is ready.")}</p></div>`;
}

function workingBlock(text: string): string {
  return `<div class="working-line">${escapeHtml(text)}<span>.</span><span>.</span><span>.</span></div>`;
}

function readinessSummary(state: AppState): string {
  const pairs = [
    ["daemon", state.status?.daemon?.ok ? "online" : "offline"],
    ["netd", state.status?.netd?.api_ok ? "linked" : "offline"],
    ["worker", state.status?.worker?.enabled ? "watching" : "disabled"],
    ["llm", state.status?.llm?.local_ollama_ready ? "local" : state.status?.llm?.provider_configured ? "provider" : "retrieval-only"],
  ];
  return pairs.map(([key, value]) => `<span><b>${escapeHtml(value)}</b>${escapeHtml(key)}</span>`).join("");
}

function supportLabel(item: SupportRef): string {
  const shared = item.shared_ref || {};
  return shared.commit || shared.path || item.source_peer || item.claim || "support";
}

function tagChips(room: AgentRoom): string {
  return (room.participants || []).slice(0, 5).map(name => `<span>@${escapeHtml(name)}</span>`).join("") || `<span>@local-amo</span>`;
}

function messageTelemetry(message: RoomMessage): string {
  const citations = message.citations || [];
  if (!citations.length) return "";
  return `<footer class="support-row">${citations.slice(0, 5).map(item => `<code>${escapeHtml(item)}</code>`).join("")}</footer>`;
}

function contextBlock(title: string, value: unknown, variant = ""): string {
  return `<section class="context-card ${variant}"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(value)}</p></section>`;
}

function renderRosterChip(item: Record<string, unknown>): string {
  const node = String(item.display_name || item.node_id || "agent");
  return `<span class="roster-chip"><i>${escapeHtml(initials(node))}</i><b>${escapeHtml(node)}</b></span>`;
}

function renderSupport(item: SupportRef): string {
  const shared = item.shared_ref || {};
  const label = shared.commit || shared.path || item.source_peer || "support";
  return `<div class="support-card"><strong>${escapeHtml(label)}</strong><p>${escapeHtml(item.claim || "Shared support claim")}</p></div>`;
}

function displaySender(sender: string, room: AgentRoom): string {
  if (room.initiator_node_id && sender === room.initiator_node_id) return `${sender} / initiator`;
  return sender;
}

function setActiveNav(view: AppView): void {
  document.querySelectorAll("[data-view]").forEach(item => item.classList.toggle("active", item.getAttribute("data-view") === view));
}

function renderTopCopy(state: AppState): void {
  const room = selectedRoom(state);
  const copy: Record<AppView, [string, string, string]> = {
    retrieval: ["local first", "Retrieval", "Ask AMO memory. Peer room opens only when local confidence is low."],
    chat: ["live room", room?.topic || room?.agent_state?.original_query || "Live Chat", room ? `${room.participants?.length || 0} participants / ${room.agent_state?.status || "open"}` : "Select a room or create one from retrieval."],
    rooms: ["room history", "Rooms", "All peer rooms your agent created or joined."],
  };
  const [eyebrow, title, subtitle] = copy[state.view];
  setText("#viewEyebrow", eyebrow);
  setText("#viewTitle", title);
  setText("#viewSubtitle", subtitle);
}

function setText(selector: string, value: string): void {
  const element = document.querySelector(selector);
  if (element) element.textContent = value;
}

function navButton(view: AppView, label: string, icon: string): string {
  return `<button class="rail-item" data-view="${view}" title="${label}" aria-label="${label}">${railIcon(icon)}</button>`;
}

function antIcon(): string {
  return `<svg class="ant-svg" viewBox="0 0 42 64" role="img" aria-label="Ant"><path d="M21 18v28"/><ellipse cx="21" cy="15" rx="7" ry="9"/><ellipse cx="21" cy="31" rx="8" ry="10"/><ellipse cx="21" cy="49" rx="7" ry="9"/><path d="M17 8C11 1 7 2 4 5M25 8c6-7 10-6 13-3"/><path d="M15 25 5 18M27 25l10-7M14 33 3 35M28 33l11 2M15 41 6 51M27 41l9 10"/></svg>`;
}

function railIcon(name: string): string {
  const icons: Record<string, string> = {
    retrieval: `<svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 5 5"/><path d="M8 10h5"/></svg>`,
    chat: `<svg viewBox="0 0 24 24"><path d="M5 6h14v9H8l-3 3z"/><path d="M9 10h6M9 13h4"/></svg>`,
    rooms: `<svg viewBox="0 0 24 24"><path d="M5 7h14M5 12h14M5 17h14"/><circle cx="8" cy="7" r="1"/><circle cx="8" cy="12" r="1"/><circle cx="8" cy="17" r="1"/></svg>`,
  };
  return `<span class="rail-icon" aria-hidden="true">${icons[name] || icons.retrieval}</span>`;
}
