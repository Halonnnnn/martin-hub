const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new ApiError(
      body?.error?.code ?? "http_error",
      body?.error?.message ?? resp.statusText,
      resp.status,
    );
  }
  return body as T;
}

/** 访客请求：不带 cookie，只带临时 token。 */
async function tempRequest<T>(
  path: string,
  token: string | null,
  init?: RequestInit,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(BASE + path, {
    credentials: "omit",
    headers,
    ...init,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new ApiError(
      body?.error?.code ?? "http_error",
      body?.error?.message ?? resp.statusText,
      resp.status,
    );
  }
  return body as T;
}

export interface WebMe {
  owner: {
    id: number;
    displayName: string;
    createdAt?: string | null;
    uiTheme?: string | null;
  };
  humanMember: { id: string; name: string };
}

export interface WebMember {
  id: string;
  name: string;
  platform: string;
  human: boolean;
  yourOwner?: boolean;
  ownerId?: number | null;
  guest?: boolean;
  label?: string;
  intro?: string;
  reach?: string;
  pushConnected?: boolean;
  pushReason?: string | null;
  presence?: string;
  presenceAt?: string | null;
  role?: string;
}

export interface WebGroup {
  id: string;
  name: string;
  topic: string;
  unread: number;
  kind?: string;
  archived?: boolean;
  inviteLink?: string;
  inviteMessage?: string;
}

export interface WebAgent extends WebMember {
  groups: { id: string; name: string }[];
  tokenRevoked?: boolean;
}

export interface WebChat {
  id: string;
  name: string;
  unread: number;
  peer: WebMember | null;
  kind?: string;
}

export interface SetupStatus {
  needsSetup: boolean;
}

export interface WebPairCodeCreated {
  id: number;
  code: string;
  expiresAt: string;
  instruction: string;
}

export interface WebPairCodeRow {
  id: number;
  groupId: string | null;
  groupName: string | null;
  reclaimName: string | null;
  status: "active" | "used" | "expired" | "revoked";
  createdAt: string;
  expiresAt: string;
  usedAt: string | null;
  revokedAt: string | null;
}

export interface WebQuestion {
  prompt: string;
  options: { id: string; label: string; consequence?: string | null }[];
  recommended?: { id: string; reason?: string | null } | null;
}

export interface WebMessage {
  seq: number;
  type: string;
  text: string | null;
  recalled: boolean;
  createdAt: string;
  author: {
    id: string;
    name: string;
    human: boolean;
    guest?: boolean;
    label?: string;
    yourOwner?: boolean;
  };
  replyToSeq?: number | null;
  priority?: string;
  question?: WebQuestion | null;
}

export type TempGrantStatus =
  | "unredeemed"
  | "active"
  | "exhausted"
  | "expired"
  | "revoked"
  | "closed";

export interface WebTempGrant {
  id: string;
  agent: { id: string; name: string } | null;
  label: string | null;
  status: TempGrantStatus;
  /** 服务器当前时间（ISO）：浏览器用它纠正本机时钟偏差 */
  serverNow: string;
  accessMinutes: number;
  maxAccessMinutes: number;
  maxAccessAt: string;
  maxRemainingSeconds: number;
  canRenew: boolean;
  maxMessages: number;
  messagesUsed: number;
  remainingSeconds: number;
  remainingMessages: number;
  createdAt: string;
  expiresAt: string;
  redeemedAt: string | null;
  closedAt: string | null;
  closeReason: string | null;
  hasTranscript: boolean;
}

export interface WebTempGrantCreated extends WebTempGrant {
  code: string;
  shareText: string;
}

export interface WebTempState {
  grantId: string;
  groupId: string;
  agent: { id: string; name: string; reach?: string };
  status: TempGrantStatus;
  /** 服务器当前时间（ISO）：浏览器用它纠正本机时钟偏差 */
  serverNow: string;
  expiresAt: string;
  maxAccessAt: string;
  remainingSeconds: number;
  maxMessages: number;
  messagesUsed: number;
  remainingMessages: number;
  closeReason?: string | null;
}

export interface PushDegraded {
  memberId: string;
  name: string;
  reach: string;
  reason: string;
}

export interface WebSendResult {
  seq: number;
  mentioned?: string[];
  mentionHint?: string | null;
  priority?: string;
  push?: { agents: number; degraded: PushDegraded[] };
  pushHint?: string | null;
  urgentDowngraded?: string | null;
}

export interface WebFriendRequest {
  id: number;
  from: WebMember;
  note: string;
  status: string;
  createdAt: string | null;
}

export interface AdminMe {
  id: number;
  username: string;
}

export interface AdminUser {
  id: number;
  displayName: string;
  email: string | null;
  humanMember: string | null;
  agentCount: number;
  approved: boolean;
  disabled: boolean;
  createdAt: string | null;
}

export interface AdminStats {
  owners: number;
  pendingOwners: number;
  admins: number;
  agents: number;
  humans: number;
  groups: number;
  directChats: number;
  messages: number;
  friendships: number;
  pendingFriendRequests: number;
}

export interface LoginSessionResult {
  kind: "owner" | "admin";
  owner?: { id: number; displayName: string };
  admin?: AdminMe;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ ok: boolean }>("/web/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  loginSession: (username: string, password: string) =>
    request<LoginSessionResult>("/web/session", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/web/logout", { method: "POST" }),
  me: () => request<WebMe>("/web/me"),
  setTheme: (uiTheme: string) =>
    request<{ owner: { id: number; uiTheme: string } }>("/web/me", {
      method: "PATCH",
      body: JSON.stringify({ uiTheme }),
    }),
  groups: () => request<{ groups: WebGroup[] }>("/web/groups"),
  createGroup: (body: { name: string; topic?: string }) =>
    request<{ group: WebGroup }>("/web/groups", {
      method: "POST",
      body: JSON.stringify({ name: body.name, topic: body.topic ?? "" }),
    }),
  joinGroup: (invite: string) =>
    request<{ joined: boolean; group: WebGroup; note: string }>("/web/join", {
      method: "POST",
      body: JSON.stringify({ invite }),
    }),
  members: (groupId: string) =>
    request<{ members: WebMember[] }>(`/web/groups/${groupId}/members`),
  removeMember: (groupId: string, memberId: string, withAgents = false) =>
    request<{ removed: boolean; removedAgents: number }>(
      `/web/groups/${groupId}/members/${encodeURIComponent(memberId)}${
        withAgents ? "?with_agents=true" : ""
      }`,
      { method: "DELETE" },
    ),
  leaveGroup: (groupId: string) =>
    request<{ left: boolean }>(`/web/groups/${groupId}/leave`, { method: "POST" }),
  archiveGroup: (groupId: string, archived: boolean) =>
    request<{ archived: boolean }>(`/web/groups/${groupId}/archive`, {
      method: "POST",
      body: JSON.stringify({ archived }),
    }),
  dissolveGroup: (groupId: string) =>
    request<{ dissolved: boolean }>(`/web/groups/${groupId}`, { method: "DELETE" }),
  agents: () => request<{ agents: WebAgent[] }>("/web/agents"),
  addAgentToGroup: (groupId: string, agentId: string) =>
    request<{ joined: boolean; agent: WebMember }>(
      `/web/groups/${groupId}/agents`,
      {
        method: "POST",
        body: JSON.stringify({ agentId }),
      },
    ),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ ok: boolean; relogin: boolean }>("/web/password", {
      method: "POST",
      body: JSON.stringify({ currentPassword, newPassword }),
    }),
  messages: (groupId: string) =>
    request<{ messages: WebMessage[] }>(`/web/groups/${groupId}/messages?limit=100`),
  send: (
    groupId: string,
    text: string,
    mentions: string[] = [],
    priority: "normal" | "urgent" = "normal",
  ) =>
    request<WebSendResult>(`/web/groups/${groupId}/messages`, {
      method: "POST",
      body: JSON.stringify({ text, mentions, priority }),
    }),
  chats: () => request<{ chats: WebChat[] }>("/web/chats"),
  closeChat: (groupId: string) =>
    request<{ dissolved: boolean }>(`/web/chats/${groupId}`, { method: "DELETE" }),
  openChat: (withId: string) =>
    request<{ group: WebGroup; peer: WebMember }>("/web/chats", {
      method: "POST",
      body: JSON.stringify({ with: withId }),
    }),
  friends: () => request<{ friends: WebMember[] }>("/web/friends"),
  addFriend: (to: string, note: string) =>
    request<{ ok: boolean }>("/web/friends", {
      method: "POST",
      body: JSON.stringify({ to, note }),
    }),
  friendRequests: () =>
    request<{ requests: WebFriendRequest[] }>("/web/friend-requests"),
  actFriendRequest: (id: number, action: "accept" | "ignore") =>
    request<{ ok: boolean; status: string }>(`/web/friend-requests/${id}`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
  deleteFriend: (id: string) =>
    request<{ ok: boolean }>(`/web/friends/${id}`, { method: "DELETE" }),
  tempGrants: (agentId?: string) =>
    request<{ grants: WebTempGrant[] }>(
      `/web/temp-grants${agentId ? `?agentId=${encodeURIComponent(agentId)}` : ""}`,
    ),
  createTempGrant: (
    agentId: string,
    body: {
      accessMinutes: number;
      maxAccessMinutes: number;
      maxMessages: number;
      label?: string;
    },
  ) =>
    request<{ grant: WebTempGrantCreated }>(
      `/web/agents/${encodeURIComponent(agentId)}/temp-grants`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  renewTempGrant: (id: string) =>
    request<{ grant: WebTempGrantCreated }>(
      `/web/temp-grants/${encodeURIComponent(id)}/renew`,
      { method: "POST" },
    ),
  revokeTempGrant: (id: string) =>
    request<{ ok: boolean; status: string }>(
      `/web/temp-grants/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),
  tempTranscript: (id: string, before?: number) =>
    request<{ messages: WebMessage[] }>(
      `/web/temp-grants/${encodeURIComponent(id)}/messages${
        before ? `?before=${before}` : ""
      }`,
    ),
  setupStatus: () => request<SetupStatus>("/web/setup-status"),
  register: (username: string, password: string) =>
    request<{ ok: boolean; pending: boolean; message: string }>("/web/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  setup: (username: string, password: string) =>
    request<{ ok: boolean; owner: { id: number; displayName: string } }>(
      "/web/setup",
      {
        method: "POST",
        body: JSON.stringify({ username, password }),
      },
    ),
  pairCodes: () => request<{ codes: WebPairCodeRow[] }>("/web/pairing-codes"),
  createPairCode: (groupId?: string | null, reclaimName?: string | null) =>
    request<WebPairCodeCreated>("/web/pairing-codes", {
      method: "POST",
      body: JSON.stringify({
        groupId: groupId ?? null,
        reclaimName: reclaimName || null,
      }),
    }),
  revokePairCode: (id: number) =>
    request<{ revoked: boolean }>(`/web/pairing-codes/${id}`, {
      method: "DELETE",
    }),
};

export const tempApi = {
  redeem: (sessionId: string, code: string, label?: string) =>
    tempRequest<{
      sessionToken: string;
      temp: WebTempState;
      messages: WebMessage[];
    }>("/temp/redeem", null, {
      method: "POST",
      body: JSON.stringify({
        sessionId,
        code,
        ...(label ? { label } : {}),
      }),
    }),
  session: (token: string) =>
    tempRequest<{ temp: WebTempState }>("/temp/session", token),
  messages: (token: string, after: number) =>
    tempRequest<{ messages: WebMessage[]; cursor: number }>(
      `/temp/messages?after=${after}`,
      token,
    ),
  send: (token: string, text: string, opKey: string) =>
    tempRequest<{ seq: number; remainingMessages: number; status: TempGrantStatus }>(
      "/temp/messages",
      token,
      { method: "POST", body: JSON.stringify({ text, opKey }) },
    ),
  close: (token: string) =>
    tempRequest<{ closed: boolean }>("/temp/close", token, { method: "POST" }),
};

export const adminApi = {
  login: (username: string, password: string) =>
    request<{ ok: boolean; admin: AdminMe }>("/admin/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/admin/logout", { method: "POST" }),
  me: () => request<{ admin: AdminMe }>("/admin/me"),
  users: () => request<{ users: AdminUser[] }>("/admin/users"),
  createUser: (username: string, password: string) =>
    request<{ user: AdminUser }>("/admin/users", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  resetPassword: (id: number, password: string) =>
    request<{ ok: boolean }>(`/admin/users/${id}/password`, {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  disable: (id: number) =>
    request<{ ok: boolean }>(`/admin/users/${id}/disable`, { method: "POST" }),
  enable: (id: number) =>
    request<{ ok: boolean }>(`/admin/users/${id}/enable`, { method: "POST" }),
  revokeTokens: (id: number) =>
    request<{ ok: boolean; revoked: number }>(`/admin/users/${id}/revoke-tokens`, {
      method: "POST",
    }),
  stats: () => request<{ stats: AdminStats }>("/admin/stats"),
  approve: (id: number) =>
    request<{ ok: boolean; approved: boolean }>(`/admin/users/${id}/approve`, {
      method: "POST",
    }),
};