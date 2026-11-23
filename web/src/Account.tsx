import { FormEvent, useState } from "react";
import { api, WebMe } from "./api";
import { applyTheme, UiTheme, UI_THEMES } from "./themes";

const SWATCH: Record<UiTheme, { bg: string; side: string; accent: string; ink: string }> = {
  paper: { bg: "#f6f4ef", side: "#1e1e1c", accent: "#b3402f", ink: "rgba(30, 30, 28, 0.35)" },
  classic: { bg: "#f5f6fa", side: "#151922", accent: "#6366f1", ink: "rgba(23, 26, 33, 0.35)" },
  warm: { bg: "#f7f2ea", side: "#f1e9dc", accent: "#b45309", ink: "rgba(51, 41, 29, 0.35)" },
  dark: { bg: "#0b0d12", side: "#0d1017", accent: "#6e79d6", ink: "rgba(231, 234, 243, 0.4)" },
  brutal: { bg: "#fdf3d8", side: "#fffdf5", accent: "#ffd400", ink: "rgba(20, 20, 20, 0.4)" },
};

function ThemeSection() {
  const [active, setActive] = useState<UiTheme>(
    () => ((document.documentElement.dataset.theme as UiTheme) || "paper"),
  );
  const [error, setError] = useState("");

  function pick(t: UiTheme) {
    if (t === active) return;
    const prev = active;
    setActive(t);
    applyTheme(t);
    api.setTheme(t).catch(() => {
      setActive(prev);
      applyTheme(prev);
      setError("保存到服务器失败，已还原本机选择");
    });
  }

  return (
    <section className="friends-section">
      <h2 className="friends-h">外观主题</h2>
      <div className="theme-grid">
        {UI_THEMES.map((t) => {
          const s = SWATCH[t.id];
          return (
            <button
              key={t.id}
              type="button"
              className={"theme-card" + (t.id === active ? " on" : "")}
              onClick={() => pick(t.id)}
            >
              <span className="theme-swatch" style={{ background: s.bg }}>
                <span className="theme-swatch-side" style={{ background: s.side }} />
                <span className="theme-swatch-dot" style={{ background: s.accent }} />
                <span className="theme-swatch-line" style={{ background: s.ink }} />
              </span>
              <b>{t.name}</b>
              <span className="theme-desc">{t.id === active ? "当前使用" : t.desc}</span>
            </button>
          );
        })}
      </div>
      {error && <div className="error">{error}</div>}
      <p className="muted friends-tip">
        主题保存在你的账号里，登录的所有设备都会生效；未登录页面跟随本机最近的选择。
      </p>
    </section>
  );
}

export default function Account({
  me,
  onLoggedOut,
}: {
  me: WebMe;
  onLoggedOut: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [done, setDone] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);

  function openPassword() {
    setCurrent("");
    setNext("");
    setConfirm("");
    setError("");
    setDone(false);
    setPasswordOpen(true);
  }

  function closePassword() {
    if (busy || done) return;
    setPasswordOpen(false);
  }

  async function copyId() {
    try {
      await navigator.clipboard.writeText(me.humanMember.id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      window.prompt("复制你的用户 ID", me.humanMember.id);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || done) return;
    setError("");
    if (next.length < 6) {
      setError("新密码至少 6 位");
      return;
    }
    if (next !== confirm) {
      setError("两次输入的新密码不一致");
      return;
    }
    setBusy(true);
    try {
      await api.changePassword(current, next);
      setDone(true);
      window.setTimeout(onLoggedOut, 1200);
    } catch (err) {
      setError(err instanceof Error ? err.message : "修改失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="doc-page">
      <div className="doc-inner">
        <div className="doc-hero">
          <h1 className="doc-title">设置</h1>
          <p className="doc-lede">
            你的用户 ID 就是别人加你为好友时填的号码。改密码后，所有已登录的浏览器都会退出，
            需要用新密码重新登录。
          </p>
        </div>

        <section className="friends-section">
          <h2 className="friends-h">账号信息</h2>
          <div className="account-row">
            <span className="account-label">用户名</span>
            <b>{me.owner.displayName}</b>
          </div>
          <div className="account-row">
            <span className="account-label">用户 ID</span>
            <code className="account-id">{me.humanMember.id}</code>
            <button type="button" className="mini accept" onClick={copyId}>
              {copied ? "已复制" : "复制"}
            </button>
          </div>
          <div className="account-row">
            <span className="account-label">密码</span>
            <span className="muted">••••••••</span>
            <button type="button" className="mini ghost" onClick={openPassword}>
              修改密码
            </button>
          </div>
          <p className="muted friends-tip">
            把用户 ID 发给朋友，对方在「好友 → 添加好友」里粘贴即可；agent 不参与好友关系。
          </p>
        </section>

        <ThemeSection />
      </div>

      {passwordOpen && (
        <div
          className="modal-mask"
          onClick={closePassword}
        >
          <form
            className="modal-card"
            onClick={(e) => e.stopPropagation()}
            onSubmit={submit}
          >
            <h3>修改密码</h3>
            <p className="modal-hint">
              修改成功后，所有已登录的浏览器都会退出，需要用新密码重新登录。
            </p>
            <label>
              当前密码
              <input
                type="password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                autoComplete="current-password"
                autoFocus
              />
            </label>
            <label>
              新密码（至少 6 位）
              <input
                type="password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                autoComplete="new-password"
              />
            </label>
            <label>
              再输入一次
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
              />
            </label>
            {error && <div className="error">{error}</div>}
            {done && <div className="notice">密码已修改，所有设备即将退出登录…</div>}
            <div className="modal-actions">
              <button
                type="button"
                className="ghost"
                disabled={busy || done}
                onClick={closePassword}
              >
                取消
              </button>
              <button
                type="submit"
                disabled={busy || done || !current || !next || !confirm}
              >
                {done ? "已修改" : busy ? "修改中…" : "修改密码"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
