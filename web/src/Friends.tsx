import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, WebMember, WebFriendRequest } from "./api";
import { Avatar, IconChat, IconCheck, IconPlus, IconTrash, IconX } from "./ui";

export default function Friends({
  onOpenChat,
}: {
  onOpenChat: (groupId: string, peer: WebMember) => void;
}) {
  const [friends, setFriends] = useState<WebMember[]>([]);
  const [requests, setRequests] = useState<WebFriendRequest[]>([]);
  const [addId, setAddId] = useState("");
  const [addNote, setAddNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.friends().then((d) => setFriends(d.friends)).catch(() => {});
    api.friendRequests().then((d) => setRequests(d.requests)).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, [load]);

  async function act(id: number, action: "accept" | "ignore") {
    setError("");
    try {
      await api.actFriendRequest(id, action);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
  }

  async function remove(id: string) {
    setError("");
    try {
      await api.deleteFriend(id);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  }

  async function chat(friend: WebMember) {
    setError("");
    try {
      const d = await api.openChat(friend.id);
      onOpenChat(d.group.id, d.peer);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法发起私聊");
    }
  }

  async function addFriend(e: FormEvent) {
    e.preventDefault();
    const to = addId.trim();
    if (!to || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.addFriend(to, addNote.trim());
      setAddId("");
      setAddNote("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="doc-page">
      <div className="doc-inner">
        <div className="doc-hero">
          <h1 className="doc-title">好友</h1>
          <p className="doc-lede">
            好友只连接用户：处理好友请求、添加用户、与好友发起一对一私聊。自己的 agent 不在
            好友里，去「协作」页管理或私聊。
          </p>
        </div>

        {error && <div className="error friends-error">{error}</div>}

        {requests.length > 0 && (
          <section className="friends-section">
            <h2 className="friends-h">好友请求 · {requests.length}</h2>
            {requests.map((r) => (
              <div className="friend-req" key={r.id}>
                <Avatar name={r.from?.name ?? "?"} size={36} human={r.from?.human} />
                <div className="friend-req-txt">
                  <b>{r.from?.name ?? `成员 ${r.from?.id}`}</b>
                  {r.note && <span className="muted">{r.note}</span>}
                </div>
                <div className="friend-req-actions">
                  <button className="mini accept" onClick={() => act(r.id, "accept")}>
                    <IconCheck size={15} />
                    接受
                  </button>
                  <button className="mini ghost" onClick={() => act(r.id, "ignore")}>
                    <IconX size={15} />
                    忽略
                  </button>
                </div>
              </div>
            ))}
          </section>
        )}

        <section className="friends-section">
          <h2 className="friends-h">添加好友</h2>
          <form className="friend-add" onSubmit={addFriend}>
            <input
              value={addId}
              onChange={(e) => setAddId(e.target.value)}
              placeholder="用户 ID（u…）或用户名"
            />
            <input
              value={addNote}
              onChange={(e) => setAddNote(e.target.value)}
              placeholder="附言（可选）"
              maxLength={300}
            />
            <button disabled={busy || !addId.trim()}>
              <IconPlus size={16} />
              {busy ? "发送中…" : "发送请求"}
            </button>
          </form>
          <p className="muted friends-tip">
            对方可以在「设置」里复制自己的用户 ID；也可以直接填用户名。agent 不能加好友。
          </p>
        </section>

        <section className="friends-section">
          <h2 className="friends-h">我的好友 · {friends.length}</h2>
          {friends.length === 0 && (
            <div className="empty">还没有好友，先发送一个好友请求吧</div>
          )}
          {friends.map((f) => (
            <div className="friend-card" key={f.id}>
              <Avatar name={f.name} size={40} human />
              <div className="friend-txt">
                <b className="friend-name">{f.name}</b>
                <span className="friend-meta">Martin 用户</span>
                {f.intro && <span className="friend-intro">{f.intro}</span>}
              </div>
              <div className="friend-actions">
                <button className="mini accept" onClick={() => chat(f)}>
                  <IconChat size={15} />
                  私聊
                </button>
                <button className="mini ghost danger" onClick={() => remove(f.id)}>
                  <IconTrash size={15} />
                  删除
                </button>
              </div>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}