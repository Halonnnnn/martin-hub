import { useEffect, useState } from "react";
import { api, WebAgent, WebGroup, WebMember, WebPairCodeCreated, WebPairCodeRow } from "./api";
import { Avatar, IconChat, PresenceDot } from "./ui";
import TempGrantsDialog from "./TempGrants";

const STATUS_LABEL: Record<WebPairCodeRow["status"], string> = {
  active: "可使用",
  used: "已使用",
  expired: "已过期",
  revoked: "已撤销",
};

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }
}

export default function Agents({
  onOpenChat,
}: {
  onOpenChat: (groupId: string, peer: WebMember) => void;
}) {
  const [agents, setAgents] = useState<WebAgent[]>([]);
  const [tab, setTab] = useState<"connect" | "mine" | null>(null);
  const [groups, setGroups] = useState<WebGroup[]>([]);
  const [groupId, setGroupId] = useState("");
  const [reclaim, setReclaim] = useState("");
  const [created, setCreated] = useState<WebPairCodeCreated | null>(null);
  const [codes, setCodes] = useState<WebPairCodeRow[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [copied, setCopied] = useState(false);
  const [tempAgent, setTempAgent] = useState<WebAgent | null>(null);

  const refresh = () => {
    api
      .agents()
      .then((d) => {
        setAgents(d.agents);
        setTab((cur) => cur ?? (d.agents.length ? "mine" : "connect"));
      })
      .catch(() => {});
    api
      .groups()
      .then((d) => setGroups(d.groups))
      .catch(() => {});
    api
      .pairCodes()
      .then((d) => setCodes(d.codes))
      .catch(() => {});
  };

  useEffect(refresh, []);

  async function generate() {
    setBusy(true);
    setError("");
    setCreated(null);
    try {
      const d = await api.createPairCode(
        groupId || null,
        reclaim.trim() || null,
      );
      setCreated(d);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: number) {
    setError("");
    try {
      await api.revokePairCode(id);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "撤销失败");
    }
  }

  async function chat(agent: WebAgent) {
    setError("");
    try {
      const d = await api.openChat(agent.id);
      onOpenChat(d.group.id, d.peer);
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法发起私聊");
    }
  }

  async function removeAgentFromGroup(agent: WebAgent, groupId: string, groupName: string) {
    if (!window.confirm(`让 ${agent.name} 退出「${groupName}」？`)) return;
    setError("");
    setBusyKey(`${agent.id}:${groupId}`);
    try {
      await api.removeMember(groupId, agent.id);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "退出群聊失败");
    } finally {
      setBusyKey("");
    }
  }

  async function copyInstruction() {
    if (!created) return;
    await copyText(created.instruction);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="doc-page">
      <div className="doc-inner">
        <div className="doc-hero">
          <h1 className="doc-title">Agent 协作</h1>
          <p className="doc-lede">
            接入新的 agent，或管理已经接入的：查看在线状态、发起私聊、检查它加入了哪些群。
          </p>
        </div>

        <nav className="guide-tabs" role="tablist" aria-label="Agent 协作分区">
          <button
            role="tab"
            aria-selected={tab === "connect"}
            className={`guide-tab${tab === "connect" ? " active" : ""}`}
            onClick={() => setTab("connect")}
          >
            接入 Agent
          </button>
          <button
            role="tab"
            aria-selected={tab === "mine"}
            className={`guide-tab${tab === "mine" ? " active" : ""}`}
            onClick={() => setTab("mine")}
          >
            我的 Agent
          </button>
        </nav>

        {error && <div className="error">{error}</div>}

        {tab === "mine" && (
          <section className="friends-section">
            <h2 className="friends-h">我的 Agent · {agents.length}</h2>
            {agents.length === 0 && (
              <div className="empty">
                还没有接入的 agent。
                <button className="link" onClick={() => setTab("connect")}>
                  去生成配对码
                </button>
              </div>
            )}
            {agents.map((a) => {
              const online = a.presence === "online";
              return (
                <div className="friend-card" key={a.id}>
                  <Avatar name={a.name} size={40} />
                  <div className="friend-txt">
                    <b className="friend-name">
                      <PresenceDot online={online} />
                      {a.name}
                    </b>
                    <span className={`friend-state ${online ? "online" : "offline"}`}>
                      {online ? "在线" : "离线"}
                      <span className="friend-meta"> · {a.platform || "agent"}</span>
                    </span>
                    {a.intro && <span className="friend-intro">{a.intro}</span>}
                    <span className="friend-meta">
                      {a.tokenRevoked ? "token 已吊销 · " : ""}
                      {a.groups.length > 0 ? "在群：" : "还没有加入群"}
                    </span>
                    {a.groups.length > 0 && (
                      <span className="friend-meta agent-groups">
                        {a.groups.map((g) => (
                          <span className="agent-group-chip" key={g.id}>
                            {g.name}
                            <button
                              type="button"
                              className="chip-x"
                              title={`让 ${a.name} 退出「${g.name}」`}
                              aria-label={`让 ${a.name} 退出 ${g.name}`}
                              disabled={busyKey === `${a.id}:${g.id}`}
                              onClick={() => void removeAgentFromGroup(a, g.id, g.name)}
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </span>
                    )}
                  </div>
                  <div className="friend-actions">
                    <button className="mini accept" onClick={() => void chat(a)}>
                      <IconChat size={15} />
                      私聊
                    </button>
                    <button className="mini" onClick={() => setTempAgent(a)}>
                      临时聊天
                    </button>
                  </div>
                </div>
              );
            })}
          </section>
        )}

        {tab === "connect" && (
          <section className="guide-steps">
            <div className="guide-step">
              <div className="step-no">1</div>
              <div className="step-body">
                <h3>生成配对码</h3>
                <div className="pair-form">
                  <select value={groupId} onChange={(e) => setGroupId(e.target.value)}>
                    <option value="">不进群（仅接入）</option>
                    {groups.map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                  </select>
                  <input
                    value={reclaim}
                    onChange={(e) => setReclaim(e.target.value)}
                    placeholder="找回身份时填 agent 原名（可选）"
                  />
                  <button className="copy-btn" disabled={busy} onClick={generate}>
                    {busy ? "生成中…" : "生成配对码"}
                  </button>
                </div>
                {created && (
                  <div className="pair-result">
                    <p>
                      配对码 <code className="pair-code">{created.code}</code>
                      <span className="muted">
                        {" "}
                        {new Date(created.expiresAt).toLocaleString()} 前有效
                      </span>
                    </p>
                    <pre>{created.instruction}</pre>
                    <button className="copy-btn" onClick={copyInstruction}>
                      {copied ? "已复制 ✓" : "复制 instruction"}
                    </button>
                  </div>
                )}
                <p className="muted">
                  把 instruction 和「Agent 接入手册」一起发给 agent。选群时它接入后自动进群；
                  找回时它会替换该名字原 agent 的 token（旧 token 立即失效）。
                </p>
              </div>
            </div>

            <div className="guide-step">
              <div className="step-no">2</div>
              <div className="step-body">
                <h3>配对码生成记录</h3>
                {codes.length === 0 ? (
                  <p className="muted">还没有生成过配对码。</p>
                ) : (
                  <ul className="pair-list">
                    {codes.map((c) => (
                      <li key={c.id}>
                        <code>#{c.id}</code>
                        <span className={`pair-status ${c.status}`}>
                          {STATUS_LABEL[c.status]}
                        </span>
                        <span className="muted">
                          {c.groupId ? `群 ${c.groupName ?? c.groupId}` : "不进群"}
                          {c.reclaimName ? ` · 找回 ${c.reclaimName}` : ""} ·{" "}
                          {new Date(c.createdAt).toLocaleString()} 生成
                        </span>
                        {c.status === "active" && (
                          <button className="link" onClick={() => revoke(c.id)}>
                            撤销
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                <p className="muted">
                  保留最近 50 条生成记录；可使用状态的码才能撤销，撤销后立即失效。
                </p>
              </div>
            </div>
          </section>
        )}
      </div>
      {tempAgent && (
        <TempGrantsDialog
          agent={tempAgent}
          onClose={() => setTempAgent(null)}
        />
      )}
    </div>
  );
}
