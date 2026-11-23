import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  WebAgent,
  WebMessage,
  WebTempGrant,
  WebTempGrantCreated,
} from "./api";

const STATUS_LABEL: Record<string, string> = {
  unredeemed: "未核销",
  active: "进行中",
  exhausted: "条数用尽",
  expired: "已过期",
  revoked: "已撤销",
  closed: "已结束",
};

function remainingSeconds(iso: string, now: number): number {
  return Math.max(0, Math.floor((new Date(iso).getTime() - now) / 1000));
}

function clock(total: number): string {
  const s = Math.max(0, Math.floor(total));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

/** 空字符串 / 0 / 非整数一律视为无效，避免清空输入后偷偷变成 0。 */
function positiveInt(raw: string): number {
  const value = raw.trim();
  if (!value) return Number.NaN;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : Number.NaN;
}

function digitsOnly(value: string): string {
  return value.replace(/[^\d]/g, "");
}

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = value;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }
}

export default function TempGrantsDialog({
  agent,
  onClose,
}: {
  agent: WebAgent;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"create" | "history">("create");
  const [grants, setGrants] = useState<WebTempGrant[]>([]);
  const [created, setCreated] = useState<WebTempGrantCreated | null>(null);
  // 表单保持字符串：数字输入清空时要能真的空着，而不是立刻变回 0
  const [minutes, setMinutes] = useState("30");
  const [maxMinutes, setMaxMinutes] = useState("1440");
  const [maxMessages, setMaxMessages] = useState("10");
  const [label, setLabel] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState("");
  const [now, setNow] = useState(Date.now());
  // 服务端时间 - 本机时间：列表里的倒计时按服务端时钟算，本机时钟不准也不会显示成已过期
  const [clockOffset, setClockOffset] = useState(0);
  const [transcript, setTranscript] = useState<{
    grant: WebTempGrant;
    messages: WebMessage[];
  } | null>(null);
  const [revoking, setRevoking] = useState("");
  const [renewing, setRenewing] = useState("");
  const loading = useRef(false);

  const minutesValue = positiveInt(minutes);
  const maxMinutesValue = positiveInt(maxMinutes);
  const maxMessagesValue = positiveInt(maxMessages);
  const fieldsReady =
    Number.isFinite(minutesValue) &&
    Number.isFinite(maxMinutesValue) &&
    Number.isFinite(maxMessagesValue);
  const maxTooShort = fieldsReady && maxMinutesValue < minutesValue;
  const canSubmit = fieldsReady && !maxTooShort;

  const load = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    try {
      const d = await api.tempGrants(agent.id);
      const list = Array.isArray(d.grants) ? d.grants : [];
      setGrants(list);
      const serverNow = list[0]?.serverNow
        ? new Date(list[0].serverNow).getTime()
        : Number.NaN;
      if (Number.isFinite(serverNow)) setClockOffset(serverNow - Date.now());
      setCreated((prev) => {
        if (!prev) return prev;
        const found = list.find((g) => g.id === prev.id);
        if (!found) return prev;
        return { ...prev, ...found, code: prev.code, shareText: prev.shareText };
      });
    } catch {
      // 列表刷新失败不打断弹窗
    } finally {
      loading.current = false;
    }
  }, [agent.id]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(id);
  }, [load]);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  async function generate() {
    if (!canSubmit) {
      setError("请填写访问时长、最大允许访问时间和消息条数，且最大访问时间不能小于访问时长。");
      return;
    }
    setBusy(true);
    setError("");
    setCreated(null);
    try {
      const d = await api.createTempGrant(agent.id, {
        accessMinutes: minutesValue,
        maxAccessMinutes: maxMinutesValue,
        maxMessages: maxMessagesValue,
        label: label.trim() || undefined,
      });
      setCreated(d.grant);
      setTab("create");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function renew(grant: WebTempGrant) {
    if (
      !window.confirm(
        "续验证码会重置访问窗口和消息条数，并让当前访客断开；历史消息会保留。继续？",
      )
    ) {
      return;
    }
    setRenewing(grant.id);
    setError("");
    try {
      const d = await api.renewTempGrant(grant.id);
      setCreated(d.grant);
      setTab("create");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "续期失败");
    } finally {
      setRenewing("");
    }
  }

  async function revoke(grant: WebTempGrant) {
    if (!window.confirm("撤销后访客会立即断开，确定撤销？")) return;
    setRevoking(grant.id);
    setError("");
    try {
      await api.revokeTempGrant(grant.id);
      await load();
      setCreated((prev) =>
        prev && prev.id === grant.id ? { ...prev, status: "revoked" } : prev,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "撤销失败");
    } finally {
      setRevoking("");
    }
  }

  async function openTranscript(grant: WebTempGrant) {
    setError("");
    try {
      const d = await api.tempTranscript(grant.id);
      setTranscript({
        grant,
        messages: Array.isArray(d.messages) ? d.messages : [],
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "读取对话失败");
    }
  }

  async function copy(value: string, key: string) {
    await copyText(value);
    setCopied(key);
    window.setTimeout(() => setCopied(""), 1500);
  }

  const serverNow = now + clockOffset;
  const createdRemaining = created
    ? remainingSeconds(created.expiresAt, serverNow)
    : 0;
  const canUse = created
    ? created.status === "unredeemed" || created.status === "active"
    : false;
  const shareUrl = created
    ? `${window.location.origin}/t/${encodeURIComponent(created.id)}`
    : "";

  function statusText(grant: WebTempGrant): string {
    if (grant.status === "expired" && grant.canRenew) return "已过期 · 可续期";
    if (grant.status === "closed" && grant.canRenew) return "已结束 · 可续期";
    return STATUS_LABEL[grant.status] ?? grant.status;
  }

  return (
    <>
      <div className="modal-mask" onClick={onClose}>
        <div
          className="modal-card temp-grant-modal"
          onClick={(e) => e.stopPropagation()}
        >
          <h3>临时聊天 · {agent.name}</h3>

          <nav className="guide-tabs temp-tabs" role="tablist" aria-label="临时会话">
            <button
              role="tab"
              aria-selected={tab === "create"}
              className={`guide-tab${tab === "create" ? " active" : ""}`}
              onClick={() => setTab("create")}
            >
              新建授权
            </button>
            <button
              role="tab"
              aria-selected={tab === "history"}
              className={`guide-tab${tab === "history" ? " active" : ""}`}
              onClick={() => setTab("history")}
            >
              历史会话
            </button>
          </nav>

          {error && <div className="temp-grant-error">{error}</div>}

          {tab === "create" && (
            <>
              <p className="modal-hint">
                访客不是 owner：Hub 会向 Harness 下发全局访客策略，限制 agent 在本会话里使用
                shell/文件/网络工具、访问其他会话或泄露信息。该策略由 Harness 统一执行，
                即使是你本人也不能关闭，只能在自己的提示词里补充更严的要求。
              </p>
              {agent.presence !== "online" && (
                <p className="modal-hint">
                  这个 agent 当前不在线：访客消息要等它下次检查才能回复。
                </p>
              )}

              <div className="temp-grant-form">
                <label>
                  访问时长（分钟）
                  <input
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={1440}
                    value={minutes}
                    onChange={(e) => setMinutes(digitsOnly(e.target.value))}
                    placeholder="30"
                  />
                </label>
                <label>
                  最大允许访问时间（分钟，≥ 访问时长）
                  <input
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={10080}
                    value={maxMinutes}
                    onChange={(e) => setMaxMinutes(digitsOnly(e.target.value))}
                    placeholder="1440"
                  />
                </label>
                <label>
                  最多消息条数（只算访客发送）
                  <input
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={200}
                    value={maxMessages}
                    onChange={(e) => setMaxMessages(digitsOnly(e.target.value))}
                    placeholder="10"
                  />
                </label>
                <label>
                  备注（可选）
                  <input
                    value={label}
                    maxLength={20}
                    onChange={(e) => setLabel(e.target.value)}
                    placeholder="例如：朋友A"
                  />
                </label>
                <button
                  className="temp-btn primary temp-grant-submit"
                  disabled={busy || !canSubmit}
                  onClick={() => void generate()}
                >
                  {busy ? "生成中…" : "生成一次性验证码"}
                </button>
              </div>

              {!fieldsReady && (
                <p className="modal-hint">
                  访问时长、最大允许访问时间和消息条数都要填正整数，留空不能生成。
                </p>
              )}
              {maxTooShort && (
                <p className="modal-hint">最大允许访问时间不能小于访问时长。</p>
              )}

              {created && (
                <div className="pair-result temp-grant-created">
                  <div className="temp-created-head">
                    <code className="pair-code">{created.code}</code>
                    <span className={canUse ? "temp-created-state" : "temp-created-state muted"}>
                      {canUse
                        ? `${clock(createdRemaining)} 后失效`
                        : STATUS_LABEL[created.status] ?? created.status}
                    </span>
                  </div>
                  <p className="temp-created-link">
                    <span>会话链接</span>
                    <code>{shareUrl}</code>
                  </p>
                  <div className="temp-grant-copy">
                    <button
                      className="temp-btn primary"
                      onClick={() => void copy(created.code, "code")}
                    >
                      {copied === "code" ? "已复制 ✓" : "复制验证码"}
                    </button>
                    <button
                      className="temp-btn primary"
                      onClick={() => void copy(created.shareText, "share")}
                    >
                      {copied === "share" ? "已复制 ✓" : "复制邀请文案"}
                    </button>
                    <button
                      className="temp-btn"
                      onClick={() => void copy(shareUrl, "url")}
                    >
                      {copied === "url" ? "已复制" : "复制链接"}
                    </button>
                  </div>
                  <p className="modal-hint">
                    验证码只显示这一次，关闭弹窗后无法再查看；访客刷新后需要新码。
                  </p>
                </div>
              )}
            </>
          )}

          {tab === "history" && (
            <>
              <div className="modal-sep" />
              <div className="temp-grant-list">
                <div className="temp-grant-list-head">
                  <b>历史会话</b>
                  <span className="temp-grant-count">{grants.length} 条</span>
                </div>
                {grants.length === 0 && (
                  <p className="temp-grant-empty">还没有为这个 agent 生成过临时授权。</p>
                )}
                {grants.map((g) => {
                  const left = remainingSeconds(g.expiresAt, serverNow);
                  const maxLeft = remainingSeconds(g.maxAccessAt, serverNow);
                  const canRevoke =
                    g.status === "unredeemed" ||
                    g.status === "active" ||
                    g.status === "exhausted";
                  return (
                    <div className="temp-grant-row" key={g.id}>
                      <div className="temp-grant-row-main">
                        <span className={`temp-status ${g.status}`}>
                          {statusText(g)}
                        </span>
                        <span className="temp-grant-meta">
                          {g.label ? `${g.label} · ` : ""}
                          {g.maxMessages} 条上限 · 已用 {g.messagesUsed}
                          {canRevoke ? ` · 剩余 ${clock(left)}` : ""}
                          {g.canRenew ? ` · 续期窗口 ${clock(maxLeft)}` : ""}
                        </span>
                        <span className="temp-grant-meta">
                          {new Date(g.createdAt).toLocaleString()} 生成
                          {g.redeemedAt
                            ? ` · ${new Date(g.redeemedAt).toLocaleTimeString()} 核销`
                            : ""}
                          {g.maxAccessAt
                            ? ` · 最晚 ${new Date(g.maxAccessAt).toLocaleString()}`
                            : ""}
                        </span>
                      </div>
                      <div className="temp-grant-row-actions">
                        <button
                          className="temp-btn"
                          onClick={() => void openTranscript(g)}
                          disabled={!g.hasTranscript}
                        >
                          查看对话
                        </button>
                        <button
                          className="temp-btn"
                          disabled={!g.canRenew || renewing === g.id}
                          onClick={() => void renew(g)}
                        >
                          {renewing === g.id ? "续期中…" : "续验证码"}
                        </button>
                        <button
                          className="temp-btn"
                          onClick={() =>
                            void copy(
                              `${window.location.origin}/t/${encodeURIComponent(g.id)}`,
                              `url-${g.id}`,
                            )
                          }
                        >
                          {copied === `url-${g.id}` ? "已复制" : "复制链接"}
                        </button>
                        {canRevoke && (
                          <button
                            className="temp-btn danger"
                            disabled={revoking === g.id}
                            onClick={() => void revoke(g)}
                          >
                            {revoking === g.id ? "撤销中…" : "撤销"}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}

          <div className="modal-actions">
            <button className="ghost" onClick={onClose}>
              关闭
            </button>
          </div>
        </div>
      </div>

      {transcript && (
        <div
          className="modal-mask temp-transcript-mask"
          onClick={() => setTranscript(null)}
        >
          <div
            className="modal-card temp-transcript"
            onClick={(e) => e.stopPropagation()}
          >
            <h3>对话回看 · {agent.name}</h3>
            <div className="temp-transcript-list">
              {transcript.messages.length === 0 && (
                <p className="temp-transcript-empty">还没有消息。</p>
              )}
              {transcript.messages.map((m) => (
                <div className="temp-transcript-row" key={m.seq}>
                  <span className="temp-transcript-author">
                    {m.type === "system"
                      ? "系统"
                      : m.author.guest
                        ? "访客"
                        : m.author.name}
                  </span>
                  <span className="temp-transcript-text">
                    {m.text ?? "（已撤回）"}
                  </span>
                </div>
              ))}
            </div>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setTranscript(null)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
