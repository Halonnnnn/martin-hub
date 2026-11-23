import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, WebMessage, WebTempState, tempApi } from "./api";
import { Logo } from "./ui";

type Stage = "code" | "chat";

const REACH_TEXT: Record<string, string> = {
  wake: "在线",
  checkin: "稍后查看",
  muted: "已开免打扰",
  away: "可能稍后回复",
};

const CLOSE_TEXT: Record<string, string> = {
  revoked: "邀请者结束了本次临时会话。",
  guest_closed: "你已结束本次临时会话。",
  expired: "本次临时会话已到期。",
};

const MAX_RENDERED_MESSAGES = 500;

function mergeMessages(prev: WebMessage[], incoming: WebMessage[]): WebMessage[] {
  if (incoming.length === 0) return prev;
  const seen = new Set(prev.map((m) => m.seq));
  const merged = [...prev, ...incoming.filter((m) => !seen.has(m.seq))];
  return merged.slice(-MAX_RENDERED_MESSAGES);
}

function clock(total: number): string {
  const s = Math.max(0, total);
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

function newOpKey(): string {
  const c = globalThis.crypto as Crypto & { randomUUID?: () => string };
  if (c?.randomUUID) return c.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** ISO 时刻 → 毫秒；解析不了就返回 0（不拿 NaN 参与倒计时）。 */
function msOf(iso: string | null | undefined): number {
  if (!iso) return 0;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) ? t : 0;
}

/** 服务端时间 - 本机时间；服务器没给或解析失败时退回本机时间。 */
function offsetOf(serverNow: string | null | undefined): number {
  const t = msOf(serverNow);
  return t > 0 ? t - Date.now() : 0;
}

/** 把服务端的 ISO 时刻渲染成浏览器本地时间：截止时间永远按访客的本机时区显示。 */
function localTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function closeText(state: WebTempState | null, fallback: string): string {
  if (!state) return fallback;
  return CLOSE_TEXT[state.closeReason ?? ""] ?? fallback;
}

export default function TempChat({ sessionId }: { sessionId?: string }) {
  const [stage, setStage] = useState<Stage>("code");
  const [code, setCode] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [temp, setTemp] = useState<WebTempState | null>(null);
  const [messages, setMessages] = useState<WebMessage[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [ended, setEnded] = useState(false);
  // 本机时钟 + 服务端偏差：倒计时由服务端下发的绝对时刻驱动，本机时间快/慢或时区不同都不会提前结束会话
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [clockOffset, setClockOffset] = useState(0);
  const cursor = useRef(0);
  const inFlight = useRef(false);
  const streamRef = useRef<HTMLDivElement | null>(null);

  const expiresMs = temp ? msOf(temp.expiresAt) : 0;
  const maxAccessMs = temp ? msOf(temp.maxAccessAt) : 0;
  const deadlineMs = expiresMs > 0 ? Math.min(expiresMs, maxAccessMs || expiresMs) : maxAccessMs;
  const hasDeadline = deadlineMs > 0;
  const remainingMs = hasDeadline
    ? Math.max(0, deadlineMs - (nowMs + clockOffset))
    : 0;
  // 拿不到绝对时刻（老服务端）时退回服务端下发的剩余秒数，由服务端轮询负责结束
  const seconds = hasDeadline
    ? Math.ceil(remainingMs / 1000)
    : temp?.remainingSeconds ?? 0;
  const timeUp = hasDeadline && Boolean(temp) && !ended && seconds <= 0;
  const maxLater = expiresMs > 0 && maxAccessMs - expiresMs > 1000;

  function endSession(message: string) {
    setEnded(true);
    setNotice(message);
    // 输入框会被移除；草稿也一起清掉，避免用新验证码进入下一场时又冒出来
    setText("");
  }

  async function enter(e: FormEvent) {
    e.preventDefault();
    const value = code.trim();
    if (!value || busy || !sessionId) return;
    setBusy(true);
    setError("");
    try {
      const d = await tempApi.redeem(sessionId, value);
      setToken(d.sessionToken);
      setTemp(d.temp);
      setClockOffset(offsetOf(d.temp.serverNow));
      setNowMs(Date.now());
      setMessages([]);
      setText("");
      setNotice("");
      setEnded(false);
      cursor.current = 0;
      setStage("chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "进入失败");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (stage !== "chat" || !token || ended) return;
    let stopped = false;
    const tick = async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      try {
        const list = await tempApi.messages(token, cursor.current);
        if (stopped) return;
        if (list.messages.length > 0) {
          cursor.current = list.cursor;
          setMessages((prev) => mergeMessages(prev, list.messages));
        }
        const s = await tempApi.session(token);
        if (stopped) return;
        setTemp(s.temp);
        setClockOffset(offsetOf(s.temp.serverNow));
        if (
          s.temp.status === "expired" ||
          s.temp.status === "closed" ||
          s.temp.status === "revoked"
        ) {
          endSession(closeText(s.temp, "本次临时会话已结束。"));
        }
      } catch (err) {
        if (stopped) return;
        if (err instanceof ApiError && (err.status === 401 || err.status === 410)) {
          endSession(
            err.status === 401 ? "本次临时会话已结束。" : err.message,
          );
        }
      } finally {
        inFlight.current = false;
      }
    };
    void tick();
    // 到点后不再等 2 秒：用更短的轮询尽快让服务端确认结束
    const id = window.setInterval(() => void tick(), timeUp ? 250 : 2000);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [stage, token, ended, timeUp]);

  useEffect(() => {
    if (stage !== "chat" || ended) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [stage, ended]);

  useEffect(() => {
    if (stage !== "chat" || ended) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [stage, ended]);

  useEffect(() => {
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!token || !temp || busy || stage !== "chat" || ended || timeUp) return;
    const value = text.trim();
    if (!value) return;
    setBusy(true);
    setError("");
    try {
      const d = await tempApi.send(token, value, newOpKey());
      setText("");
      setTemp((prev) =>
        prev
          ? {
              ...prev,
              status: d.status,
              remainingMessages: d.remainingMessages,
              messagesUsed: prev.maxMessages - d.remainingMessages,
            }
          : prev,
      );
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "quota_exhausted") {
          setTemp((prev) =>
            prev
              ? {
                  ...prev,
                  status: "exhausted",
                  messagesUsed: prev.maxMessages,
                  remainingMessages: 0,
                }
              : prev,
          );
        }
        if (err.status === 401 || err.status === 410) {
          endSession(
            err.status === 401 ? "本次临时会话已结束。" : err.message,
          );
        }
      }
      setError(err instanceof Error ? err.message : "发送失败");
    } finally {
      setBusy(false);
    }
  }

  async function leave() {
    if (!token || ended) return;
    if (
      !window.confirm(
        "结束本次临时会话？结束后需要向邀请者索取新的验证码才能继续。",
      )
    ) {
      return;
    }
    try {
      await tempApi.close(token);
    } catch {
      // 关闭失败也进入本地结束态；刷新后本来也就没了
    }
    endSession("你已结束本次临时会话。");
  }

  function restart() {
    setStage("code");
    setCode("");
    setToken(null);
    setTemp(null);
    setMessages([]);
    setText("");
    setNowMs(Date.now());
    setClockOffset(0);
    setEnded(false);
    setNotice("");
    setError("");
    cursor.current = 0;
  }

  if (!sessionId) {
    return (
      <div className="temp-page">
        <div className="temp-card temp-login">
          <div className="temp-brand">
            <Logo size={44} />
            <span>临时会话</span>
          </div>
          <h1>邀请链接不完整</h1>
          <p className="muted">
            请使用邀请者提供的完整链接，链接里需要包含本次会话的 ID。
          </p>
        </div>
      </div>
    );
  }

  if (stage === "code") {
    return (
      <div className="temp-page">
        <form className="temp-card temp-login" onSubmit={enter}>
          <div className="temp-brand">
            <Logo size={44} />
            <span>临时会话</span>
          </div>
          <h1>输入临时验证码</h1>
          <p className="muted">
            邀请者为你开放了一次限时对话。验证码只能用一次，刷新页面后需要重新向邀请者索取。
          </p>
          <div className="temp-session-ref">会话 {sessionId}</div>
          <label className="temp-field">
            验证码
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="TMP-XXXX-XXXX"
              autoComplete="one-time-code"
              spellCheck={false}
              autoFocus
            />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="temp-primary" disabled={busy || !code.trim()}>
            {busy ? "验证中…" : "进入临时会话"}
          </button>
          <p className="temp-foot">
            对话内容对邀请者可见。请不要发送密码、密钥或其他敏感信息。
          </p>
        </form>
      </div>
    );
  }

  if (!temp) {
    return (
      <div className="temp-page">
        <div className="temp-card temp-login">
          <h1>会话加载中…</h1>
          <p className="muted">如果长时间没有反应，请刷新页面后重新输入验证码。</p>
        </div>
      </div>
    );
  }

  const exhausted =
    temp.status === "exhausted" || temp.remainingMessages <= 0;

  return (
    <div className="temp-page">
      <div className="temp-card temp-chat">
        <header className="temp-head">
          <div className="temp-head-main">
            <b className="temp-title">与 {temp.agent.name} 的临时会话</b>
            <span className="temp-sub">
              {ended
                ? "会话已结束 · 历史可查看"
                : `${REACH_TEXT[temp.agent.reach ?? ""] ?? "可能稍后回复"} · 邀请者可查看`}
            </span>
          </div>
          <div className="temp-head-stats">
            <span className={!ended && seconds <= 60 ? "temp-warn" : ""}>
              {ended ? "已结束" : clock(seconds)}
            </span>
            <span>
              已用 {temp.messagesUsed} / {temp.maxMessages} 条
            </span>
          </div>
        </header>
        <div className="temp-deadline">
          <span>访问截止 {localTime(temp.expiresAt)}（本机时间）</span>
          {maxLater && (
            <span>
              · 发起人可在 {localTime(temp.maxAccessAt)} 前为你续期
            </span>
          )}
        </div>

        <div className="temp-stream" ref={streamRef}>
          {messages.length === 0 && (
            <p className="temp-empty">{ended ? "没有可显示的历史消息。" : "等待对方回复…"}</p>
          )}
          {messages.map((m) => {
            if (m.type === "system") {
              return (
                <div className="temp-system" key={m.seq}>
                  {m.text}
                </div>
              );
            }
            const mine = Boolean(m.author.guest);
            return (
              <div className={`temp-msg ${mine ? "mine" : "theirs"}`} key={m.seq}>
                <div className="temp-meta">
                  {mine ? "你" : m.author.name} ·{" "}
                  {new Date(m.createdAt).toLocaleTimeString()}
                </div>
                <div className="temp-text">{m.text ?? "（已撤回）"}</div>
              </div>
            );
          })}
        </div>

        {ended && (
          <div className="temp-limit temp-ended">
            {notice || "本次临时会话已结束。"}
            {" "}继续对话请向邀请者索取新的验证码。
            {expiresMs > 0 && (
              <span className="temp-ended-at">
                会话截止 {localTime(temp.expiresAt)}（本机时间）
              </span>
            )}
          </div>
        )}
        {timeUp && (
          <div className="temp-limit">时间已到，正在结束本次临时会话…</div>
        )}
        {!ended && !timeUp && exhausted && (
          <div className="temp-limit">
            发送条数已用尽，输入框已关闭；仍可查看对方回复，直到会话结束。
          </div>
        )}
        {error && <div className="error temp-error">{error}</div>}

        {ended ? (
          <div className="temp-composer temp-ended-actions">
            <button className="temp-primary" onClick={restart}>
              输入新的验证码
            </button>
          </div>
        ) : exhausted ? null : (
          <form className="temp-composer" onSubmit={send}>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={timeUp ? "时间已到" : "输入消息，回车发送"}
              rows={2}
              maxLength={10000}
              disabled={busy || timeUp}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit?.();
                }
              }}
            />
            <button
              className="temp-primary"
              disabled={busy || timeUp || !text.trim()}
            >
              {busy ? "发送中…" : "发送"}
            </button>
          </form>
        )}

        <footer className="temp-foot temp-chat-foot">
          <span>
            {ended
              ? "已停止调用接口，历史只保留在当前页面。"
              : exhausted
                ? "当前为只读模式。"
                : "刷新页面会结束本次会话。"}
          </span>
          {!ended && (
            <button className="link" onClick={() => void leave()}>
              结束会话
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
