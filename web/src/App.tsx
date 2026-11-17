import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, adminApi, WebMe, WebGroup, WebChat, WebMember, AdminMe } from "./api";
import { applyTheme, defaultTheme, isTheme } from "./themes";
import { navigate, Route, useRoute } from "./router";
import Chat from "./Chat";
import Login from "./Login";
import Setup from "./Setup";
import Guide from "./Guide";
import TempChat from "./TempChat";
import Friends from "./Friends";
import Agents from "./Agents";
import Account from "./Account";
import AdminConsole from "./AdminConsole";
import {
  Avatar,
  Logo,
  IconChat,
  IconGuide,
  IconFriends,
  IconPlug,
  IconLogout,
  IconChevronRight,
  IconChevronLeft,
  IconPlus,
  IconSettings,
  IconUserPlus,
  PresenceDot,
} from "./ui";

type View = "chat" | "guide" | "friends" | "agents" | "account";
type Session =
  | { kind: "owner"; me: WebMe }
  | { kind: "admin"; admin: AdminMe }
  | null;

export default function App() {
  const route = useRoute();
  const [session, setSession] = useState<Session>(null);
  const [ready, setReady] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);

  useEffect(() => {
    Promise.allSettled([api.me(), adminApi.me()]).then(([o, a]) => {
      if (o.status === "fulfilled") setSession({ kind: "owner", me: o.value });
      else if (a.status === "fulfilled")
        setSession({ kind: "admin", admin: a.value.admin });
      setReady(true);
    });
    api
      .setupStatus()
      .then((d) => setNeedsSetup(d.needsSetup))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!ready) return;
    if (session?.kind === "owner") {
      const t = session.me.owner.uiTheme;
      applyTheme(isTheme(t) ? t : defaultTheme());
    } else if (session?.kind === "admin") {
      applyTheme("classic", false);
    } else {
      applyTheme(defaultTheme(), false);
    }
  }, [ready, session]);

  // 管理端固定挂在 /admin
  useEffect(() => {
    if (route.name === "temp") return;
    if (session?.kind === "admin" && route.name !== "admin") {
      navigate("/admin", { replace: true });
    }
  }, [session, route.name]);

  // 已登录的普通用户不应停留在登录、初始化或管理端路径
  useEffect(() => {
    if (
      session?.kind === "owner" &&
      (route.name === "login" || route.name === "setup" || route.name === "admin")
    ) {
      navigate("/chat", { replace: true });
    }
  }, [session, route.name]);

  // /t 是免登录公开页：不套登录壳、不依赖 session 加载完成
  if (route.name === "temp") return <TempChat sessionId={route.sessionId} />;

  if (!ready) return <div className="center">加载中…</div>;

  if (!session && needsSetup) {
    return (
      <Setup
        onDone={(me) => {
          setSession({ kind: "owner", me });
          setNeedsSetup(false);
          // 通过邀请链接进来时保留原路径，初始化完成后继续入群流程
          if (route.name !== "join") navigate("/chat", { replace: true });
        }}
      />
    );
  }

  if (session?.kind === "admin") {
    return (
      <AdminConsole
        admin={session.admin}
        onLogout={() =>
          adminApi.logout().then(() => {
            setSession(null);
            navigate("/", { replace: true });
          })
        }
      />
    );
  }

  const privateRoute =
    route.name === "chat" ||
    route.name === "friends" ||
    route.name === "agents" ||
    route.name === "account";

  // 未登录访问私有页面（含深链接）时先登录，登录后停留在原路径
  if (
    !session &&
    (route.name === "login" || route.name === "admin" || privateRoute)
  ) {
    return (
      <Login
        onOwner={(me) => {
          setSession({ kind: "owner", me });
          const next = new URLSearchParams(window.location.search).get("next");
          if (next && next.startsWith("/") && !next.startsWith("//")) {
            navigate(next, { replace: true });
          } else if (route.name === "login" || route.name === "admin") {
            navigate("/chat", { replace: true });
          }
        }}
        onAdmin={(admin) => {
          setSession({ kind: "admin", admin });
          navigate("/admin", { replace: true });
        }}
        onBack={() => navigate("/guide")}
      />
    );
  }

  if (!session) return <PublicShell route={route} />;

  return (
    <OwnerApp
      me={session.me}
      route={route}
      onLogout={() =>
        api.logout().then(() => {
          setSession(null);
          navigate("/", { replace: true });
        })
      }
      onLoggedOut={() => {
        setSession(null);
        navigate("/login", { replace: true });
      }}
    />
  );
}

function PublicShell({ route }: { route: Route }) {
  const [navCollapsed, setNavCollapsed] = useState(false);
  const onGuide = route.name === "home" || route.name === "guide";

  return (
    <div className="layout">
      <aside className={"sidebar" + (navCollapsed ? " collapsed" : "")}>
        <div className="sidebar-head">
          <span className="side-brand">
            <Logo size={32} />
            {!navCollapsed && <span className="brand">Martin</span>}
          </span>
          {navCollapsed ? (
            <button
              className="link"
              title="展开导航"
              aria-label="展开导航"
              onClick={() => setNavCollapsed(false)}
            >
              <IconChevronRight />
            </button>
          ) : (
            <button
              className="link"
              title="收起导航"
              aria-label="收起导航"
              onClick={() => setNavCollapsed(true)}
            >
              <IconChevronLeft />
            </button>
          )}
        </div>
        <nav className="side-nav">
          <button
            className={onGuide ? "on" : ""}
            title="指南与流程"
            aria-label="指南与流程"
            onClick={() => {
              navigate("/guide");
              setNavCollapsed(false);
            }}
          >
            <IconGuide />
            {!navCollapsed && "指南与流程"}
          </button>
        </nav>
        {!navCollapsed && (
          <div className="side-login">
            <button
              className="login-cta"
              onClick={() =>
                navigate(
                  `/login?next=${encodeURIComponent(
                    window.location.pathname + window.location.search,
                  )}`,
                )
              }
            >
              登录
            </button>
            <p className="side-login-tip">登录后可管理群聊、好友与私聊</p>
          </div>
        )}
      </aside>
      <main className="chat-pane">
        <Guide />
      </main>
    </div>
  );
}

function OwnerApp({
  me,
  route,
  onLogout,
  onLoggedOut,
}: {
  me: WebMe;
  route: Route;
  onLogout: () => void;
  onLoggedOut: () => void;
}) {
  const [groups, setGroups] = useState<WebGroup[]>([]);
  const [chats, setChats] = useState<WebChat[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [activeKind, setActiveKind] = useState<"group" | "direct">("group");
  const [listTab, setListTab] = useState<"groups" | "direct">("groups");
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [newGroupOpen, setNewGroupOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupTopic, setNewGroupTopic] = useState("");
  const [newGroupBusy, setNewGroupBusy] = useState(false);
  const [newGroupError, setNewGroupError] = useState("");
  const [joinOpen, setJoinOpen] = useState(false);
  const [joinInput, setJoinInput] = useState("");
  const [joinBusy, setJoinBusy] = useState(false);
  const [joinError, setJoinError] = useState("");
  const [openedPeer, setOpenedPeer] = useState<{
    groupId: string;
    peer: WebMember;
  } | null>(null);
  // 入群框是否由 /join/<code> 路由打开的（路由离开时自动关闭）
  const joinFromRoute = useRef(false);
  const expandNav = () => setNavCollapsed(false);

  // 左侧导航当前高亮哪一页，由 URL 决定
  const view: View =
    route.name === "friends"
      ? "friends"
      : route.name === "agents"
        ? "agents"
        : route.name === "account"
          ? "account"
          : route.name === "guide"
            ? "guide"
            : "chat";

  const refresh = useCallback(() => {
    api
      .groups()
      .then((d) => {
        setGroups(d.groups);
        setActive((cur) => cur ?? (d.groups.length ? d.groups[0].id : null));
      })
      .catch(() => {});
    api.chats().then((d) => setChats(d.chats)).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  // 邀请链接 /join/<code> 直达：登录后自动弹出入群框
  useEffect(() => {
    if (route.name === "join") {
      joinFromRoute.current = true;
      setJoinInput(route.code ?? "");
      setJoinError("");
      setJoinOpen(true);
    } else if (joinFromRoute.current) {
      joinFromRoute.current = false;
      setJoinOpen(false);
    }
  }, [route]);

  // /chat/<id> 深链接：数据加载后自动选中对应群聊或私聊
  const routeChatId = route.name === "chat" ? route.groupId : undefined;
  useEffect(() => {
    if (!routeChatId) return;
    if (groups.some((g) => g.id === routeChatId)) {
      setActive(routeChatId);
      setActiveKind("group");
      setListTab("groups");
    } else if (chats.some((c) => c.id === routeChatId)) {
      setActive(routeChatId);
      setActiveKind("direct");
      setListTab("direct");
    }
  }, [routeChatId, groups, chats]);

  const openChat = useCallback(
    (groupId: string, peer: WebMember) => {
      setOpenedPeer({ groupId, peer });
      setActive(groupId);
      setActiveKind("direct");
      setListTab("direct");
      navigate(`/chat/${encodeURIComponent(groupId)}`);
      refresh();
    },
    [refresh],
  );

  const afterCloseChat = useCallback(() => {
    setActive(null);
    setOpenedPeer(null);
    refresh();
    navigate("/chat", { replace: true });
  }, [refresh]);

  function closeJoin() {
    setJoinOpen(false);
    joinFromRoute.current = false;
    if (route.name === "join") navigate("/chat", { replace: true });
  }

  async function submitJoin(e: FormEvent) {
    e.preventDefault();
    const invite = joinInput.trim();
    if (!invite || joinBusy) return;
    setJoinBusy(true);
    setJoinError("");
    try {
      const d = await api.joinGroup(invite);
      setJoinOpen(false);
      joinFromRoute.current = false;
      setJoinInput("");
      setActive(d.group.id);
      setActiveKind("group");
      setListTab("groups");
      navigate(`/chat/${encodeURIComponent(d.group.id)}`, { replace: true });
      refresh();
    } catch (err) {
      setJoinError(err instanceof Error ? err.message : "加入失败");
    } finally {
      setJoinBusy(false);
    }
  }

  async function submitNewGroup(e: FormEvent) {
    e.preventDefault();
    const name = newGroupName.trim();
    if (!name || newGroupBusy) return;
    setNewGroupBusy(true);
    setNewGroupError("");
    try {
      const d = await api.createGroup({ name, topic: newGroupTopic.trim() });
      setNewGroupOpen(false);
      setNewGroupName("");
      setNewGroupTopic("");
      setActive(d.group.id);
      setActiveKind("group");
      setListTab("groups");
      navigate(`/chat/${encodeURIComponent(d.group.id)}`, { replace: true });
      refresh();
    } catch (err) {
      setNewGroupError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setNewGroupBusy(false);
    }
  }

  const activeGroup: WebGroup | undefined =
    activeKind === "direct"
      ? (() => {
          const c = chats.find((x) => x.id === active);
          return c
            ? {
                id: c.id,
                name: c.peer?.name ?? c.name,
                topic: "私聊",
                unread: c.unread,
                kind: "direct",
              }
            : undefined;
        })()
      : groups.find((g) => g.id === active);

  const activePeer =
    activeKind === "direct"
      ? (chats.find((x) => x.id === active)?.peer ??
        (openedPeer?.groupId === active ? openedPeer.peer : null))
      : null;

  return (
    <div className="layout">
      <aside className={"sidebar" + (navCollapsed ? " collapsed" : "")}>
        <div className="sidebar-head">
          <span className="side-brand">
            <Logo size={32} />
            {!navCollapsed && <span className="brand">Martin</span>}
          </span>
          {navCollapsed ? (
            <button
              className="link"
              title="展开导航"
              aria-label="展开导航"
              onClick={expandNav}
            >
              <IconChevronRight />
            </button>
          ) : (
            <button
              className="link"
              title="收起导航"
              aria-label="收起导航"
              onClick={() => setNavCollapsed(true)}
            >
              <IconChevronLeft />
            </button>
          )}
        </div>
        <nav className="side-nav">
          <button
            className={view === "chat" ? "on" : ""}
            title="聊天"
            aria-label="聊天"
            onClick={() => {
              navigate("/chat");
              setNavCollapsed(false);
            }}
          >
            <IconChat />
            {!navCollapsed && "聊天"}
          </button>
          <button
            className={view === "friends" ? "on" : ""}
            title="好友"
            aria-label="好友"
            onClick={() => {
              navigate("/friends");
              setNavCollapsed(false);
            }}
          >
            <IconFriends />
            {!navCollapsed && "好友"}
          </button>
          <button
            className={view === "agents" ? "on" : ""}
            title="协作"
            aria-label="协作"
            onClick={() => {
              navigate("/agents");
              setNavCollapsed(false);
            }}
          >
            <IconPlug />
            {!navCollapsed && "协作"}
          </button>
          <button
            className={view === "account" ? "on" : ""}
            title="设置"
            aria-label="设置"
            onClick={() => {
              navigate("/account");
              setNavCollapsed(false);
            }}
          >
            <IconSettings />
            {!navCollapsed && "设置"}
          </button>
          <button
            className={view === "guide" ? "on" : ""}
            title="指南与流程"
            aria-label="指南与流程"
            onClick={() => {
              navigate("/guide");
              setNavCollapsed(false);
            }}
          >
            <IconGuide />
            {!navCollapsed && "指南与流程"}
          </button>
        </nav>
        {!navCollapsed && view === "chat" && (
          <>
            <div className="side-tabs" role="tablist" aria-label="会话列表">
              <div className="side-tabs-track">
                <button
                  type="button"
                  role="tab"
                  aria-selected={listTab === "groups"}
                  className={"side-tab" + (listTab === "groups" ? " on" : "")}
                  onClick={() => setListTab("groups")}
                >
                  群聊
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={listTab === "direct"}
                  className={"side-tab" + (listTab === "direct" ? " on" : "")}
                  onClick={() => setListTab("direct")}
                >
                  私聊
                </button>
              </div>
              {listTab === "groups" && (
                <span className="side-adds">
                  <button
                    className="side-add"
                    title="加入群聊"
                    aria-label="加入群聊"
                    onClick={() => {
                      joinFromRoute.current = false;
                      setJoinError("");
                      setJoinInput("");
                      setJoinOpen(true);
                    }}
                  >
                    <IconUserPlus size={13} />
                  </button>
                  <button
                    className="side-add"
                    title="新建群聊"
                    aria-label="新建群聊"
                    onClick={() => {
                      setNewGroupError("");
                      setNewGroupOpen(true);
                    }}
                  >
                    <IconPlus size={13} />
                  </button>
                </span>
              )}
            </div>
            {listTab === "groups" ? (
              <ul className="group-list">
                {groups.map((g) => (
                  <li
                    key={g.id}
                    className={g.id === active && activeKind === "group" ? "active" : ""}
                    onClick={() => {
                      setActive(g.id);
                      setActiveKind("group");
                      navigate(`/chat/${encodeURIComponent(g.id)}`);
                    }}
                  >
                    <Avatar name={g.name} size={26} className="sq" />
                    <span className="gname">{g.name}</span>
                    {g.archived && <span className="archived-tag side">已归档</span>}
                    {g.unread > 0 && !(active === g.id && activeKind === "group") && (
                      <span className="badge">{g.unread}</span>
                    )}
                  </li>
                ))}
                {groups.length === 0 && (
                  <li className="empty">暂无群，点右侧图标新建或加入</li>
                )}
              </ul>
            ) : (
              <ul className="group-list">
                {chats.map((c) => (
                  <li
                    key={c.id}
                    className={c.id === active && activeKind === "direct" ? "active" : ""}
                    onClick={() => {
                      setActive(c.id);
                      setActiveKind("direct");
                      navigate(`/chat/${encodeURIComponent(c.id)}`);
                    }}
                  >
                    <Avatar name={c.peer?.name ?? c.name} size={26} human={c.peer?.human} />
                    <span className="gname">{c.peer?.name ?? c.name}</span>
                    {c.peer && !c.peer.human && (
                      <PresenceDot online={c.peer.presence === "online"} size={7} />
                    )}
                    {c.unread > 0 && !(active === c.id && activeKind === "direct") && (
                      <span className="badge">{c.unread}</span>
                    )}
                    {c.peer && (
                      <span className="peer-tag">{c.peer.human ? "用户" : "Agent"}</span>
                    )}
                  </li>
                ))}
                {chats.length === 0 && (
                  <li className="empty">暂无私聊，去好友页发起</li>
                )}
              </ul>
            )}
          </>
        )}
        <div className="side-user">
          <Avatar name={me.owner.displayName} size={24} human />
          {!navCollapsed && (
            <>
              <span className="who">{me.owner.displayName}</span>
              <button className="link" title="退出登录" onClick={onLogout}>
                <IconLogout />
              </button>
            </>
          )}
        </div>
      </aside>
      <main className="chat-pane">
        {view === "friends" ? (
          <Friends onOpenChat={openChat} />
        ) : view === "agents" ? (
          <Agents onOpenChat={openChat} />
        ) : view === "account" ? (
          <Account me={me} onLoggedOut={onLoggedOut} />
        ) : view === "guide" ? (
          <Guide />
        ) : active !== null ? (
          <Chat
            key={active}
            groupId={active}
            group={activeGroup}
            peer={activePeer}
            meId={me.humanMember.id}
            onClosed={afterCloseChat}
            onGroupChanged={refresh}
          />
        ) : (
          <div className="pane-empty">选择左侧的群或私聊开始查看</div>
        )}
      </main>
      {joinOpen && (
        <div
          className="modal-mask"
          onClick={() => {
            if (!joinBusy) closeJoin();
          }}
        >
          <form
            className="modal-card"
            onClick={(e) => e.stopPropagation()}
            onSubmit={submitJoin}
          >
            <h3>加入群聊</h3>
            <label>
              邀请链接或群码
              <input
                value={joinInput}
                onChange={(e) => setJoinInput(e.target.value)}
                placeholder="/join/xxxxxxxx 或 xxxxxxxx"
                autoFocus
              />
            </label>
            <p className="modal-hint">
              让群里的成员在群聊顶部点「邀请」把链接发给你。用户在群里只能收到消息，
              不会被 @。
            </p>
            {joinError && <div className="error">{joinError}</div>}
            <div className="modal-actions">
              <button
                type="button"
                className="ghost"
                disabled={joinBusy}
                onClick={closeJoin}
              >
                取消
              </button>
              <button type="submit" disabled={joinBusy || !joinInput.trim()}>
                {joinBusy ? "加入中…" : "加入"}
              </button>
            </div>
          </form>
        </div>
      )}
      {newGroupOpen && (
        <div
          className="modal-mask"
          onClick={() => {
            if (!newGroupBusy) setNewGroupOpen(false);
          }}
        >
          <form
            className="modal-card"
            onClick={(e) => e.stopPropagation()}
            onSubmit={submitNewGroup}
          >
            <h3>新建群聊</h3>
            <label>
              群名称
              <input
                value={newGroupName}
                onChange={(e) => setNewGroupName(e.target.value)}
                placeholder="例如：周末徒步"
                maxLength={100}
                autoFocus
              />
            </label>
            <label>
              群主题（可选）
              <input
                value={newGroupTopic}
                onChange={(e) => setNewGroupTopic(e.target.value)}
                placeholder="一句话说明这个群聊什么"
                maxLength={500}
              />
            </label>
            {newGroupError && <div className="error">{newGroupError}</div>}
            <div className="modal-actions">
              <button
                type="button"
                className="ghost"
                disabled={newGroupBusy}
                onClick={() => setNewGroupOpen(false)}
              >
                取消
              </button>
              <button type="submit" disabled={newGroupBusy || !newGroupName.trim()}>
                {newGroupBusy ? "创建中…" : "创建"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}