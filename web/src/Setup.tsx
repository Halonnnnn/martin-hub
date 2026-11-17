import { FormEvent, useState } from "react";
import { api, WebMe } from "./api";
import { Logo } from "./ui";

export default function Setup({ onDone }: { onDone: (me: WebMe) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.setup(username, password);
      onDone(await api.me());
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="login-logo">
          <Logo size={52} />
        </div>
        <h1>Martin</h1>
        <p className="muted">首次启动 · 创建用户账号</p>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="用户名（也是你在群里的名字）"
          autoFocus
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="密码（至少 6 位）"
        />
        {error && <div className="error">{error}</div>}
        <button disabled={busy || !username || password.length < 6}>
          {busy ? "创建中…" : "创建账号并登录"}
        </button>
        <p className="muted setup-hint">
          只在系统还没有任何用户时出现。创建后此页自动消失，之后都用这个账号登录。
        </p>
      </form>
    </div>
  );
}
