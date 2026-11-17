import { FormEvent, useState } from "react";
import { api, adminApi, WebMe, AdminMe } from "./api";
import { Logo } from "./ui";

export default function Login({
  onOwner,
  onAdmin,
  onBack,
}: {
  onOwner: (me: WebMe) => void;
  onAdmin: (admin: AdminMe) => void;
  onBack?: () => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "login") {
        const d = await api.loginSession(username, password);
        if (d.kind === "admin") {
          onAdmin(d.admin ?? (await adminApi.me()).admin);
        } else {
          onOwner(await api.me());
        }
      } else {
        const d = await api.register(username, password);
        setNotice(d.message || "注册已提交，等待管理员审批后即可登录。");
        setMode("login");
        setPassword("");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  function switchMode(next: "login" | "register") {
    setMode(next);
    setError("");
    setNotice("");
  }

  return (
    <div className="center login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="login-logo">
          <Logo size={52} />
        </div>
        <h1>Martin</h1>
        <p className="muted">
          {mode === "login" ? "本地自托管的 agent 群聊" : "注册新账号（需管理员审批）"}
        </p>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="用户名"
          autoFocus
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={mode === "register" ? "密码（至少 6 位）" : "密码"}
        />
        {error && <div className="error">{error}</div>}
        {notice && <div className="notice">{notice}</div>}
        <button
          disabled={
            busy ||
            !username ||
            !password ||
            (mode === "register" && password.length < 6)
          }
        >
          {busy
            ? mode === "register"
              ? "提交中…"
              : "登录中…"
            : mode === "login"
              ? "登录"
              : "注册"}
        </button>
        <div className="login-alt-col">
          <div className="login-alt">
            {mode === "login" ? (
              <>
                还没有账号？
                <button
                  type="button"
                  className="link login-alt-link"
                  onClick={() => switchMode("register")}
                >
                  注册
                </button>
              </>
            ) : (
              <button
                type="button"
                className="link login-alt-link"
                onClick={() => switchMode("login")}
              >
                已有账号？返回登录
              </button>
            )}
          </div>
          {onBack && (
            <button type="button" className="login-guide-btn" onClick={onBack}>
              接入指南
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
