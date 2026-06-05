import type { AgentRoom, AntelligentEvent, AntelligentStatus, ChatResult, RoomContext, RoomMessage } from "../api/types";

export type AppView = "retrieval" | "chat" | "rooms";

export type AppState = {
  view: AppView;
  status: AntelligentStatus | null;
  rooms: AgentRoom[];
  selectedRoomId: string;
  messages: RoomMessage[];
  context: RoomContext | null;
  events: AntelligentEvent[];
  lastQuery: string;
  lastChatResult: ChatResult | null;
  online: boolean;
  busy: boolean;
};

export function createInitialState(): AppState {
  return {
    view: "retrieval",
    status: null,
    rooms: [],
    selectedRoomId: "",
    messages: [],
    context: null,
    events: [],
    lastQuery: "",
    lastChatResult: null,
    online: false,
    busy: false,
  };
}

export function selectedRoom(state: AppState): AgentRoom | null {
  return state.rooms.find(room => room.room_id === state.selectedRoomId) || null;
}

export function localNodeId(state: AppState): string {
  return state.status?.peer?.node?.node_id || "";
}
