import { FormEvent, useCallback, useEffect, useState } from "react";
import { adminApi, AdminMe, AdminStats, AdminUser } from "./api";
import {
  Logo,
  IconAdmin,
  IconLogout,
  IconPlus,
  IconKey,
  IconBan,
  IconCheck,
  IconX,
} from "./ui";

export default function AdminConsole({
  admin,
  onLogout,
}: {
  admin: AdminMe;
  onLogout: () => void;
}) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [newName, setNewName] = useState("");
  const [newPw, setNewPw] = useState("");
  const [resetPw, setResetPw] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    adminApi.users().then((d) => {
      setUsers(d.users);
      setSelected((cur) =>
        cur ? d.users.find((u) => u.id === cur.id) ?? null : null,
      );
    }).catch(() => {});
    adminApi.stats().then((d) => setStats(d.stats)).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  function flash(msg: string) {
    setNotice(msg);
    setError("");
    window.setTimeout(() => setNotice(""), 2500);
  }

  async function createUser(e: FormEvent) {
    e.preventDefault();
    if (busy || !newName.trim() || !newPw) return;
    setBusy(true);
    setError("");
    try {
      await adminApi.createUser(newName.trim(), newPw);
      setNewName("");
      setNewPw("");
      flash("已创建用户");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function doReset() {
    if (!selected || !resetPw) return;
    setError("");
    try {
      await adminApi.resetPassword(selected.id, resetPw);
      setResetPw("");
      flash("已重置密码");
    } catch (err) {
      setError(err instanceof Error ? err.message : "重置失败");
    }
  }

  async function toggleDisabled() {
    if (!selected) return;
    setError("");
    try {
      if (selected.disabled) {
        await adminApi.enable(selected.id);
        flash("已启用");
      } else {
        await adminApi.disable(selected.id);
        flash("已停用");
      }
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
  }

  async function approve() {
    if (!selected) return;
    setError("");
    try {
      await adminApi.approve(selected.id);
      flash("已通过审批");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "审批失败");
    }
  }

  async function revoke() {
    if (!selected) return;
    setError("");
    try {
      const d = await adminApi.revokeTokens(selected.id);
      flash(`已吊销 ${d.revoked} 个 agent token`);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "吊销失败");
    }
  }

  const statCards: { label: string; key: keyof AdminStats }[] = [
    { label: "用户", key: "owners" },
    { label: "待审批", key: "pendingOwners" },
    { label: "管理员", key: "admins" },
    { label: "Agent", key: "agents" },
    { label: "群", key: "groups" },
    { label: "私聊", key: "directChats" },
    { label: "消息", key: "messages" },
    { label: "好友关系", key: "friendships" },
    { label: "待处理请求", key: "pendingFriendRequests" },
  ];

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-head">
          <span className="side-brand">
            <Logo size={32} />
            <span className="brand">Martin</span>
          </span>
          <button className="link" title="退出登录" onClick={onLogout}>
            <IconLogout />
          </button>
        </div>
        <div className="side-owner">
          <span className="admin-chip">
            <IconAdmin size={16} />
          </span>
          <span className="who">
            管理员<b>{admin.username}</b>
          </span>
        </div>
        <div className="admin-side-note">
          系统管理员只维护系统信息，不参与群聊、不拥有 agent。
        </div>
      </aside>

      <main className="chat-pane">
        <div className="doc-page">
          <div className="doc-inner admin-inner">
            <div className="doc-hero">
              <h1 className="doc-title">系统管理</h1>
              <p className="doc-lede">
                维护用户账号与查看系统概览。停用用户会同时使其名下 agent 的 token
                失效；不做物理删除，停用即可。
              </p>
            </div>

            {notice && <div className="notice">{notice}</div>}
            {error && <div className="error friends-error">{error}</div>}

            {stats && (
              <div className="admin-stats">
                {statCards.map((c) => (
                  <div className="stat-card" key={c.key}>
                    <span className="stat-num">{stats[c.key]}</span>
                    <span className="stat-label">{c.label}</span>
                  </div>
                ))}
              </div>
            )}

            <section className="friends-section">
              <h2 className="friends-h">新建用户</h2>
              <form className="friend-add" onSubmit={createUser}>
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="用户名"
                  maxLength={100}
                />
                <input
                  type="password"
                  value={newPw}
                  onChange={(e) => setNewPw(e.target.value)}
                  placeholder="初始密码（≥6 位）"
                />
                <button disabled={busy || !newName.trim() || newPw.length < 6}>
                  <IconPlus size={16} />
                  {busy ? "创建中…" : "创建"}
                </button>
              </form>
            </section>

            <section className="friends-section">
              <h2 className="friends-h">用户列表 · {users.length}</h2>
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>用户</th>
                      <th>human 成员</th>
                      <th>Agent 数</th>
                      <th>状态</th>
                      <th>创建时间</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr
                        key={u.id}
                        className={selected?.id === u.id ? "sel" : ""}
                        onClick={() => {
                          setSelected(u);
                          setResetPw("");
                        }}
                      >
                        <td><b>{u.displayName}</b></td>
                        <td>{u.humanMember ?? "—"}</td>
                        <td>{u.agentCount}</td>
                        <td>
                          <span
                            className={`pill ${
                              u.disabled ? "off" : u.approved ? "on" : "pending"
                            }`}
                          >
                            {u.disabled ? "已停用" : u.approved ? "启用" : "待审批"}
                          </span>
                        </td>
                        <td className="muted">
                          {u.createdAt ? new Date(u.createdAt).toLocaleDateString() : "—"}
                        </td>
                        <td>
                          <button
                            className="mini ghost"
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelected(u);
                              setResetPw("");
                            }}
                          >
                            管理
                          </button>
                        </td>
                      </tr>
                    ))}
                    {users.length === 0 && (
                      <tr>
                        <td colSpan={6} className="empty">暂无用户</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        </div>

        {selected && (
          <div className="admin-panel">
            <div className="admin-panel-head">
              <b>{selected.displayName}</b>
              <button className="link" onClick={() => setSelected(null)} title="关闭">
                <IconX size={16} />
              </button>
            </div>
            <div className="admin-panel-meta muted">
              human 成员：{selected.humanMember ?? "—"} · Agent {selected.agentCount} ·{" "}
              {selected.disabled
                ? "已停用"
                : selected.approved
                  ? "启用中"
                  : "待审批"}
            </div>
            <div className="admin-panel-row">
              <input
                type="password"
                value={resetPw}
                onChange={(e) => setResetPw(e.target.value)}
                placeholder="新密码（≥6 位）"
              />
              <button className="mini accept" disabled={resetPw.length < 6} onClick={doReset}>
                <IconKey size={15} />
                重置密码
              </button>
            </div>
            <div className="admin-panel-actions">
              {!selected.approved && (
                <button className="mini accept" onClick={approve}>
                  <IconCheck size={15} />
                  通过审批
                </button>
              )}
              <button className="mini ghost" onClick={toggleDisabled}>
                {selected.disabled ? <IconCheck size={15} /> : <IconBan size={15} />}
                {selected.disabled ? "启用账号" : "停用账号"}
              </button>
              <button className="mini ghost danger" onClick={revoke}>
                <IconBan size={15} />
                吊销全部 agent token
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}