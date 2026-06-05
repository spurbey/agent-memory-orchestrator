import { invoke } from "@tauri-apps/api/core";
import type { AgentRoom, AntelligentStatus, ChatResult, RoomContext, RoomMessage } from "./types";

type BackendInfo = {
  baseUrl: string;
  token: string;
};

let backendInfo: BackendInfo | null = null;

export async function backend(): Promise<BackendInfo> {
  if (backendInfo) return backendInfo;
  try {
    backendInfo = await invoke<BackendInfo>("backend_info");
  } catch {
    backendInfo = {
      baseUrl: localStorage.getItem("antelligent.baseUrl") || "http://127.0.0.1:8765",
      token: localStorage.getItem("antelligent.token") || "",
    };
  }
  return backendInfo;
}

export async function apiGet<T>(path: string): Promise<T> {
  const info = await backend();
  const response = await fetch(`${info.baseUrl}${path}`, {
    headers: { Accept: "application/json", Authorization: `Bearer ${info.token}` },
  });
  return parseResponse<T>(response);
}

export async function apiPost<T>(path: string, payload: Record<string, unknown>): Promise<T> {
  const info = await backend();
  const response = await fetch(`${info.baseUrl}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: `Bearer ${info.token}`,
    },
    body: JSON.stringify(payload),
  });
  return parseResponse<T>(response);
}

export async function status(): Promise<AntelligentStatus> {
  return apiGet<AntelligentStatus>("/api/antelligent/status");
}

export async function rooms(): Promise<{ ok: boolean; rooms: AgentRoom[] }> {
  return apiGet<{ ok: boolean; rooms: AgentRoom[] }>("/api/antelligent/rooms");
}

export async function messages(roomId: string): Promise<{ ok: boolean; messages: RoomMessage[] }> {
  return apiGet<{ ok: boolean; messages: RoomMessage[] }>(`/api/antelligent/rooms/${encodeURIComponent(roomId)}/messages`);
}

export async function context(roomId: string): Promise<{ ok: boolean; context: RoomContext }> {
  return apiGet<{ ok: boolean; context: RoomContext }>(`/api/antelligent/rooms/${encodeURIComponent(roomId)}/context`);
}

export async function chat(query: string): Promise<ChatResult> {
  return apiPost<ChatResult>("/api/antelligent/chat", { query });
}

export async function askRoom(roomId: string, query: string): Promise<Record<string, unknown>> {
  return apiPost<Record<string, unknown>>(`/api/antelligent/rooms/${encodeURIComponent(roomId)}/ask`, { query });
}

export async function continueRoom(roomId: string): Promise<Record<string, unknown>> {
  return apiPost<Record<string, unknown>>(`/api/antelligent/rooms/${encodeURIComponent(roomId)}/continue`, {});
}

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(parsed.error || `${response.status} ${response.statusText}`);
  }
  return parsed as T;
}
