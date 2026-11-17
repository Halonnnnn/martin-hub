import {
  ChangeEvent,
  Children,
  KeyboardEvent as ReactKeyboardEvent,
  ReactNode,
  Suspense,
  lazy,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { DEFAULT_TEMPLATE, TEMPLATES } from "./templates";
import { api, WebAgent, WebMessage, WebGroup, WebMember } from "./api";
import type { DraftLang } from "./DraftEditor";
import { Avatar, IconBolt, IconExpand, IconSend, IconTemplate, IconUserPlus, PresenceDot } from "./ui";

// 编辑器（CodeMirror）只在第一次放大时加载，不进首屏
const DraftEditor = lazy(() => import("./DraftEditor"));

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const mentionTokenRe = (name: string) =>
  new RegExp(`(^|[\\s(（\\[，,。:：])@${escapeRe(name)}(?=$|[\\s)）\\]，,。.!?：:；;])`);

const URGENT_ACK_KEY = "martin.urgent.ack";
const draftKey = (groupId: string) => `martin.draft.${groupId}`;

function pushLabel(m: WebMember): { text: string; state: string } {
  switch (m.pushReason) {
    case "muted":
      return { text: "免打扰", state: "muted" };
    case "no_channel":
      return { text: "未接入推送", state: "off" };
    case "disabled":
      return { text: "推送已停用", state: "off" };
    case "listener_offline":
      return { text: "通道未连接", state: "off" };
    default:
      return m.reach === "wake"
        ? { text: "可即时叫醒", state: "wake" }
        : { text: "靠定时检查", state: "off" };
  }
}

type MentionSpan = { start: number; end: number; name: string };

function mentionSpans(text: string, names: string[]): MentionSpan[] {
  const spans: MentionSpan[] = [];
  for (const name of names) {
    if (!name) continue;
    const re = new RegExp(
      `(^|[\\s(（\\[，,。:：])@${escapeRe(name)}(?=$|[\\s)）\\]，,。.!?：:；;])`,
      "g",
    );
    let match: RegExpExecArray | null;
    while ((match = re.exec(text)) !== null) {
      const start = match.index + match[1].length;
      spans.push({ start, end: start + name.length + 1, name });
      if (match.index === re.lastIndex) re.lastIndex += 1;
    }
  }
  return spans.sort((a, b) => a.start - b.start);
}

function renderMentionText(text: string, names: string[]): ReactNode {
  const pattern = names
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)
    .map(escapeRe)
    .join("|");
  if (!pattern) return text;
  const re = new RegExp(
    `(^|[\\s(（\\[，,。:：])(@(?:${pattern}))(?=$|[\\s)）\\]，,。.!?：:；;])`,
    "g",
  );
  const out: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const start = match.index + match[1].length;
    out.push(text.slice(last, start));
    out.push(
      <span className="mention-pill" key={`${start}-${match[2]}`}>
        {match[2]}
      </span>,
    );
    last = start + match[2].length;
    if (match.index === re.lastIndex) re.lastIndex += 1;
  }
  if (last === 0) return text;
  out.push(text.slice(last));
  return out;
}

function highlightChildren(children: ReactNode, names: string[]): ReactNode {
  return Children.map(children, (child) =>
    typeof child === "string" ? renderMentionText(child, names) : child,
  );
}

export default function Chat({
  groupId,
  group,
  peer,
  meId,
  onClosed,
  onGroupChanged,
}: {
  groupId: string;
  group?: WebGroup;
  peer?: WebMember | null;
  meId?: string;
  onClosed?: () => void;
  onGroupChanged?: () => void;
}) {
  const [messages, setMessages] = useState<WebMessage[]>([]);
  const [text, setText] = useState(() => window.localStorage.getItem(draftKey(groupId)) ?? "");
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [members, setMembers] = useState<WebMember[]>([]);
  const [mentions, setMentions] = useState<string[]>([]);
  const [urgent, setUrgent] = useState(false);
  const [urgentIntro, setUrgentIntro] = useState(false);
  const [pushHint, setPushHint] = useState("");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [invited, setInvited] = useState(false);
  const [inviteAgents, setInviteAgents] = useState<WebAgent[]>([]);
  const [inviteBusy, setInviteBusy] = useState("");
  const [inviteNotice, setInviteNotice] = useState("");
  const [memberOpen, setMemberOpen] = useState(false);
  const [memberBusy, setMemberBusy] = useState("");
  const [memberError, setMemberError] = useState("");
  const [kickConfirm, setKickConfirm] = useState<{
    member: WebMember;
    agents: number;
  } | null>(null);

  const [template, setTemplate] = useState(DEFAULT_TEMPLATE);
  const [tplOpen, setTplOpen] = useState(false);
  const [menu, setMenu] = useState<{ start: number; query: string; index: number } | null>(
    null,
  );
  const isDirect = group?.kind === "direct" || Boolean(peer);
  const archived = Boolean(!isDirect && group?.archived);
  const peerOnline = peer?.presence === "online";
  // 用户不展示在线状态；离线提示只对 agent 生效
  const peerOffline = isDirect && peer != null && !peer.human && !peerOnline;
  const bottomRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const inviteRef = useRef<HTMLInputElement>(null);
  const tplRef = useRef<HTMLDivElement>(null);
  const composing = useRef(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .messages(groupId)
        .then((d) => {
          if (alive) setMessages(d.messages);
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [groupId]);

  useEffect(() => {
    let alive = true;
    setMembers([]);
    setMentions([]);
    setMenu(null);
    setUrgent(false);
    setUrgentIntro(false);
    setPushHint("");
    setInviteOpen(false);
    setInvited(false);
    setInviteAgents([]);
    setInviteBusy("");
    setInviteNotice("");
    setMemberOpen(false);
    setMemberBusy("");
    setMemberError("");
    api
      .members(groupId)
      .then((d) => {
        if (alive) setMembers(d.members);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [groupId]);

  // 草稿落 localStorage：刷新、切群再回来都不丢
  useEffect(() => {
    const key = draftKey(groupId);
    if (!text) {
      window.localStorage.removeItem(key);
      return;
    }
    const timer = window.setTimeout(() => window.localStorage.setItem(key, text), 250);
    return () => window.clearTimeout(timer);
  }, [text, groupId]);

  useEffect(() => {
    if (!tplOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!tplRef.current?.contains(e.target as Node)) setTplOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [tplOpen]);

  // 用户（human）不可被 @：联想、正文高亮、发送都只看 agent
  const mentionable = useMemo(() => members.filter((m) => !m.human), [members]);

  const me = members.find((m) => m.id === meId) ?? null;
  const amOwner = me?.role === "owner";
  const humans = members.filter((m) => m.human);
  const agentMembers = members.filter((m) => !m.human);
  const canRemove = (m: WebMember) =>
    m.id !== meId &&
    m.role !== "owner" &&
    (amOwner || (!m.human && Boolean(m.yourOwner)));

  const refreshMembers = () => {
    api
      .members(groupId)
      .then((d) => setMembers(d.members))
      .catch(() => {});
  };

  function carriedAgents(m: WebMember) {
    if (m.ownerId == null) return [];
    return agentMembers.filter((a) => a.ownerId === m.ownerId);
  }

  function startKick(m: WebMember) {
    const carried = carriedAgents(m);
    if (m.human && carried.length > 0) {
      setKickConfirm({ member: m, agents: carried.length });
      return;
    }
    void doKick(m, false);
  }

  async function doKick(m: WebMember, withAgents: boolean) {
    setMemberError("");
    setMemberBusy(m.id);
    setKickConfirm(null);
    try {
      await api.removeMember(groupId, m.id, withAgents);
      refreshMembers();
      onGroupChanged?.();
    } catch (err) {
      setMemberError(err instanceof Error ? err.message : "移出失败");
    } finally {
      setMemberBusy("");
    }
  }

  async function toggleArchive() {
    const next = !archived;
    if (next && !window.confirm("归档后成员只能查看历史消息，确定归档？")) return;
    setMemberError("");
    try {
      await api.archiveGroup(groupId, next);
      setMemberOpen(false);
      onGroupChanged?.();
    } catch (err) {
      setMemberError(err instanceof Error ? err.message : "操作失败");
    }
  }

  async function dissolveGroup() {
    if (!window.confirm(`解散「${group?.name ?? "群聊"}」后所有成员都会失去访问，确定解散？`)) return;
    setMemberError("");
    try {
      await api.dissolveGroup(groupId);
      setMemberOpen(false);
      onGroupChanged?.();
      onClosed?.();
    } catch (err) {
      setMemberError(err instanceof Error ? err.message : "解散失败");
    }
  }

  async function leaveGroup() {
    if (!window.confirm(`退出「${group?.name ?? "群聊"}」？退出后需要重新邀请才能回来。`)) return;
    setMemberError("");
    try {
      await api.leaveGroup(groupId);
      setMemberOpen(false);
      onClosed?.();
    } catch (err) {
      setMemberError(err instanceof Error ? err.message : "退出失败");
    }
  }

  const candidates = useMemo(() => {
    if (!menu) return [];
    const q = menu.query.trim().toLowerCase();
    return mentionable
      .filter((m) => !q || m.name.toLowerCase().includes(q))
      .slice(0, 8);
  }, [menu, mentionable]);

  const memberNames = useMemo(
    () => mentionable.map((m) => m.name).filter(Boolean),
    [mentionable],
  );

  const inviteCandidates = useMemo(
    () => inviteAgents.filter((a) => !members.some((m) => m.id === a.id)),
    [inviteAgents, members],
  );

  const inviteUrl = useMemo(() => {
    if (!group?.inviteLink) return "";
    try {
      return new URL(group.inviteLink, window.location.origin).toString();
    } catch {
      return group.inviteLink;
    }
  }, [group?.inviteLink]);

  const mdComponents = useMemo<Components>(() => {
    const hi = (children: ReactNode) => highlightChildren(children, memberNames);
    return {
      p: ({ children }) => <p>{hi(children)}</p>,
      li: ({ children }) => <li>{hi(children)}</li>,
      strong: ({ children }) => <strong>{hi(children)}</strong>,
      em: ({ children }) => <em>{hi(children)}</em>,
      td: ({ children }) => <td>{hi(children)}</td>,
      th: ({ children }) => <th>{hi(children)}</th>,
      h1: ({ children }) => <h1>{hi(children)}</h1>,
      h2: ({ children }) => <h2>{hi(children)}</h2>,
      h3: ({ children }) => <h3>{hi(children)}</h3>,
    };
  }, [memberNames]);

  const unmatched = useMemo(() => {
    if (!members.length || menu) return "";
    const re = /(^|[\s(（\[，,。:：])@([^\s@，,。:：；;]{1,30})/g;
    let match: RegExpExecArray | null;
    while ((match = re.exec(text)) !== null) {
      const start = match.index + match[1].length;
      const rest = text.slice(start + 1);
      if (members.some((m) => m.human && rest.startsWith(m.name))) {
        return "用户不能被 @，发送不会通知对方";
      }
      if (!mentionable.some((m) => rest.startsWith(m.name))) {
        return `@${match[2]} 未匹配到成员，发送不会通知对方`;
      }
    }
    return "";
  }, [text, members, mentionable, menu]);

  useEffect(() => {
    if (stickToBottom.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  function detectMenu(value: string, caret: number | null) {
    if (composing.current) return null;
    const pos = caret ?? value.length;
    const before = value.slice(0, pos);
    const at = before.lastIndexOf("@");
    if (at < 0) return null;
    if (at > 0 && !/[\s(（\[，,。:：]/.test(before[at - 1])) return null;
    const query = before.slice(at + 1);
    if (query.length > 30 || /[\n\r]/.test(query)) return null;
    return { start: at, query, index: 0 };
  }

  function menuFor(value: string, caret: number | null) {
    const next = detectMenu(value, caret);
    if (!next) return null;
    const q = next.query.trim().toLowerCase();
    const hasMatch = mentionable.some((m) => m.name.toLowerCase().includes(q));
    return hasMatch ? next : null;
  }

  function handleChange(e: ChangeEvent<HTMLTextAreaElement>) {
    const value = e.target.value;
    setText(value);
    setMentions((prev) => prev.filter((name) => mentionTokenRe(name).test(value)));
    setMenu(menuFor(value, e.target.selectionStart));
  }

  function choose(member: WebMember) {
    if (!menu) return;
    const caret = textRef.current?.selectionStart ?? text.length;
    const insert = `@${member.name} `;
    const next = text.slice(0, menu.start) + insert + text.slice(caret);
    setText(next);
    setMentions((prev) => (prev.includes(member.name) ? prev : [...prev, member.name]));
    setMenu(null);
    requestAnimationFrame(() => {
      const node = textRef.current;
      if (!node) return;
      const pos = menu.start + insert.length;
      node.focus();
      node.setSelectionRange(pos, pos);
    });
  }

  function removeMention(name: string) {
    setMentions((prev) => prev.filter((n) => n !== name));
    setText((prev) => prev.replace(mentionTokenRe(name), (_all, lead: string) => lead));
  }

  function deleteMention(dir: "back" | "forward") {
    const node = textRef.current;
    if (!node || node.selectionStart !== node.selectionEnd) return false;
    const caret = node.selectionStart;
    const span = mentionSpans(text, memberNames).find((s) => {
      const end = s.end + (text[s.end] === " " ? 1 : 0);
      return dir === "back" ? caret > s.start && caret <= end : caret >= s.start && caret < s.end;
    });
    if (!span) return false;
    const end = span.end + (text[span.end] === " " ? 1 : 0);
    const next = text.slice(0, span.start) + text.slice(end);
    setText(next);
    setMentions((prev) =>
      prev.filter((name) => name !== span.name && mentionTokenRe(name).test(next)),
    );
    setMenu(null);
    requestAnimationFrame(() => {
      node.focus();
      node.setSelectionRange(span.start, span.start);
    });
    return true;
  }

  function handleKeyDown(e: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (menu && candidates.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMenu({ ...menu, index: (menu.index + 1) % candidates.length });
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMenu({ ...menu, index: (menu.index - 1 + candidates.length) % candidates.length });
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        choose(candidates[menu.index] ?? candidates[0]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMenu(null);
        return;
      }
    }
    if (!menu && (e.key === "Backspace" || e.key === "Delete")) {
      if (deleteMention(e.key === "Backspace" ? "back" : "forward")) {
        e.preventDefault();
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      if (e.nativeEvent.isComposing || composing.current) return;
      e.preventDefault();
      void submit();
    }
  }

  function toggleUrgent() {
    if (urgent) {
      setUrgent(false);
      return;
    }
    if (window.localStorage.getItem(URGENT_ACK_KEY) === "1") {
      setUrgent(true);
      return;
    }
    setUrgentIntro(true);
  }

  function ackUrgent() {
    window.localStorage.setItem(URGENT_ACK_KEY, "1");
    setUrgentIntro(false);
    setUrgent(true);
  }

  async function submit(override?: string): Promise<boolean> {
    const trimmed = (override ?? text).trim();
    if (!trimmed || sending) return false;
    const effective = new Set(mentions);
    for (const m of mentionable) {
      if (mentionTokenRe(m.name).test(trimmed)) effective.add(m.name);
    }
    setSending(true);
    setError("");
    try {
      const res = await api.send(
        groupId,
        trimmed,
        [...effective],
        urgent ? "urgent" : "normal",
      );
      setText("");
      setMentions([]);
      setMenu(null);
      setUrgent(false);
      window.localStorage.removeItem(draftKey(groupId));
      const hints: string[] = [];
      if (res.urgentDowngraded) hints.push("紧急额度已用完，本条按普通消息发送");
      if (res.pushHint) hints.push(res.pushHint);
      setPushHint(hints.join("；"));
      const d = await api.messages(groupId);
      setMessages(d.messages);
      stickToBottom.current = true;
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败");
      return false;
    } finally {
      setSending(false);
    }
  }

  async function openInvite() {
    setInviteOpen(true);
    setInviteNotice("");
    setInvited(false);
    try {
      const d = await api.agents();
      setInviteAgents(d.agents);
    } catch {
      setInviteAgents([]);
    }
    try {
      const d = await api.members(groupId);
      setMembers(d.members);
    } catch {
      // 成员刷新失败不影响邀请链接
    }
  }

  async function inviteAgent(agent: WebAgent) {
    setInviteBusy(agent.id);
    setInviteNotice("");
    try {
      const r = await api.addAgentToGroup(groupId, agent.id);
      setInviteNotice(r.joined ? `已邀请 ${agent.name} 进群` : `${agent.name} 已经在群里`);
      const d = await api.members(groupId);
      setMembers(d.members);
    } catch (err) {
      setInviteNotice(err instanceof Error ? err.message : "邀请失败");
    } finally {
      setInviteBusy("");
    }
  }

  async function copyInvite() {
    try {
      await navigator.clipboard.writeText(inviteUrl);
      setInvited(true);
      window.setTimeout(() => setInvited(false), 1600);
    } catch {
      inviteRef.current?.select();
    }
  }

  function applyTemplate(name: string) {
    setTplOpen(false);
    const t = TEMPLATES.find((x) => x.name === name);
    if (!t) return;
    if (text.trim() && !window.confirm("输入框已有内容，用模板覆盖？")) return;
    setTemplate(name);
    setText(t.body);
    requestAnimationFrame(() => textRef.current?.focus());
  }

  // 放大编辑器发出的消息：JSON 自动包成代码块，会话里才有排版
  async function submitFromEditor(lang: DraftLang) {
    const draft = text.trim();
    if (!draft) return;
    const payload =
      lang === "json" && !draft.startsWith("```")
        ? `\`\`\`json\n${draft}\n\`\`\``
        : draft;
    if (payload !== text) setText(payload);
    const ok = await submit(payload);
    if (ok) setExpanded(false);
  }

  return (
    <div className="chat">
      <div className="chat-head">
        <Avatar name={group?.name ?? String(groupId)} size={38} className="sq" />
        <div className="chat-head-txt">
          <div className="chat-title-row">
            <h2 className="chat-title">{group?.name ?? `群 ${groupId}`}</h2>
            {archived && <span className="archived-tag">已归档</span>}
            {isDirect && peer && (
              <span className="peer-type">{peer.human ? "用户" : "Agent"}</span>
            )}
            {isDirect && peer && !peer.human && (
              <span className={`chat-presence ${peerOnline ? "online" : "offline"}`}>
                <PresenceDot online={peerOnline} />
                {peerOnline ? "在线" : "离线"}
              </span>
            )}
          </div>
          {group?.topic && <p className="chat-topic">{group.topic}</p>}
        </div>
        {isDirect && (
          <button
            type="button"
            className="head-btn"
            title="结束这个会话"
            onClick={() => {
              if (!window.confirm("结束这个会话？双方都会从私聊列表移除。")) return;
              void (async () => {
                try {
                  await api.closeChat(groupId);
                  onClosed?.();
                } catch (err) {
                  setError(err instanceof Error ? err.message : "结束失败");
                }
              })();
            }}
          >
            结束
          </button>
        )}
        {!isDirect && (
          <button
            type="button"
            className="head-btn"
            title="群成员与管理"
            onClick={() => {
              setMemberError("");
              refreshMembers();
              setMemberOpen(true);
            }}
          >
            成员 {members.length}
          </button>
        )}
        {!isDirect && inviteUrl && (
          <button
            type="button"
            className="head-btn"
            title="邀请成员加入"
            onClick={() => void openInvite()}
          >
            <IconUserPlus size={14} />
            邀请
          </button>
        )}
        <span className="chat-id">#{groupId}</span>
      </div>
      <div
        className="messages"
        onScroll={(e) => {
          const el = e.currentTarget;
          stickToBottom.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        {messages.map((m) => {
          const mine = Boolean(m.author.human && m.author.yourOwner);
          const plain = m.recalled || m.type === "system";
          return (
            <div
              key={m.seq}
              className={`msg ${plain ? "plain" : mine ? "mine" : "theirs"}`}
            >
              {!plain && (
                <Avatar
                  name={m.author.name}
                  size={34}
                  human={m.author.human}
                  className="msg-ava"
                />
              )}
              {plain ? (
                <div className="plain-line">
                  <span>{m.author.name}</span>
                  <span>{m.recalled ? "消息已撤回" : m.text}</span>
                </div>
              ) : (
                <div className="msg-body">
                  <div className="msg-meta">
                    <span className="author">
                      {m.author.name}
                      {mine && <span className="tag">用户</span>}
                    </span>
                    {m.priority === "urgent" && (
                      <span className="tag urgent" title="紧急推送">
                        <IconBolt size={11} />
                        紧急
                      </span>
                    )}
                    <span className="seq">#{m.seq}</span>
                    <span className="time">
                      {new Date(m.createdAt).toLocaleTimeString()}
                    </span>
                  </div>
                  <div className="bubble markdown">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                      {m.text ?? ""}
                    </ReactMarkdown>
                    {m.type === "question" && m.question && (() => {
                      const q = m.question;
                      return (
                        <div className="question-box">
                          {q.options.map((o) => (
                            <button
                              key={o.id}
                              type="button"
                              className={`q-opt${q.recommended?.id === o.id ? " rec" : ""}`}
                              title={
                                q.recommended?.id === o.id
                                  ? `建议选 ${o.id}${q.recommended.reason ? `：${q.recommended.reason}` : ""}`
                                  : o.consequence || undefined
                              }
                              onClick={() => {
                                setText(`回复 #${m.seq}：${o.id} `);
                                textRef.current?.focus();
                              }}
                            >
                              <b>{o.id}</b> {o.label}
                              {q.recommended?.id === o.id && (
                                <span className="tag rec-tag">建议</span>
                              )}
                            </button>
                          ))}
                        </div>
                      );
                    })()}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {messages.length === 0 && <div className="empty">还没有消息</div>}
        <div ref={bottomRef} />
      </div>
      {archived ? (
        <div className="archived-banner">
          <span>群已归档，成员只能查看历史消息。</span>
          {amOwner && (
            <button type="button" className="mini ghost" onClick={() => void toggleArchive()}>
              取消归档
            </button>
          )}
        </div>
      ) : (
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="composer-card">
          {menu && candidates.length > 0 && (
            <div className="mention-menu" role="listbox">
              {candidates.map((m, i) => (
                <div
                  key={m.id}
                  role="option"
                  aria-selected={i === menu.index}
                  className={`mention-item${i === menu.index ? " active" : ""}`}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    choose(m);
                  }}
                >
                  <span className="mention-item-name">{m.name}</span>
                  <span className="mention-item-tag">
                    {m.human ? "用户" : m.platform || "agent"}
                  </span>
                  {!m.human && (
                    <span className={`mention-item-push ${pushLabel(m).state}`}>
                      {pushLabel(m).text}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
          {error && <div className="error">{error}</div>}
          {peerOffline && (
            <div className="composer-hint peer-offline">
              <PresenceDot online={false} size={7} />
              该 agent 已下线，消息会保留，等其上线后可读取
            </div>
          )}
          {mentions.length > 0 && (
            <div className="mention-chips">
              {mentions.map((name) => (
                <span className="mention-chip" key={name}>
                  @{name}
                  <span
                    className="mention-chip-x"
                    role="button"
                    aria-label={`移除 ${name}`}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      removeMention(name);
                    }}
                  >
                    ×
                  </span>
                </span>
              ))}
            </div>
          )}
          {unmatched && <div className="composer-hint">{unmatched}</div>}
          {urgentIntro && (
            <div className="urgent-intro">
              <strong>
                <IconBolt size={14} /> 紧急推送
              </strong>
              <p>
                立刻叫醒被 @ 的 agent（绕过合并去重与每日限额）。对方开了免打扰、没接入推送或通道未连接时叫不醒，
                发送后会告诉你原因。每群每小时 5 条、每人每天 20 条，超出自动按普通消息发送。
              </p>
              <div className="urgent-intro-actions">
                <button type="button" onClick={ackUrgent}>
                  知道了，开启紧急
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => setUrgentIntro(false)}
                >
                  取消
                </button>
              </div>
            </div>
          )}
          {urgent && !urgentIntro && (
            <div className="composer-hint urgent-on">
              <IconBolt size={12} /> 紧急模式：发送后立刻叫醒被提及的 agent（免打扰除外）
            </div>
          )}
          {pushHint && <div className="composer-hint push-hint">{pushHint}</div>}
          <div className="composer-input">
            <textarea
              ref={textRef}
              value={text}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              onCompositionStart={() => {
                composing.current = true;
              }}
              onCompositionEnd={(e) => {
                composing.current = false;
                setMenu(menuFor(e.currentTarget.value, e.currentTarget.selectionStart));
              }}
              onBlur={() => {
                window.setTimeout(() => setMenu(null), 120);
              }}
              placeholder={
                peerOffline
                  ? "该 agent 已下线"
                  : "以用户身份发言（@ 提及成员，Enter 发送，Shift+Enter 换行）"
              }
              rows={2}
            />
          </div>
          <div className="composer-toolbar">
            <div className="composer-tools">
              <div className="tpl-wrap" ref={tplRef}>
                <button
                  type="button"
                  className="tool-btn"
                  aria-haspopup="menu"
                  aria-expanded={tplOpen}
                  title="消息模板：选中后填充输入框"
                  onClick={() => setTplOpen((open) => !open)}
                >
                  <IconTemplate size={14} />
                  模板
                  <span className="caret">▾</span>
                </button>
                {tplOpen && (
                  <div className="tpl-pop" role="menu">
                    {TEMPLATES.map((t) => (
                      <button
                        key={t.name}
                        type="button"
                        role="menuitem"
                        className={`tpl-item${template === t.name ? " on" : ""}`}
                        onClick={() => applyTemplate(t.name)}
                      >
                        {t.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button
                type="button"
                className={`tool-btn${urgent ? " on" : ""}`}
                aria-pressed={urgent}
                title="紧急推送：立刻叫醒被 @ 的 agent"
                onClick={toggleUrgent}
              >
                <IconBolt size={14} />
                紧急
              </button>
            </div>
            <div className="composer-send-group">
              <button
                type="button"
                className="editor-btn"
                onClick={() => setExpanded(true)}
                title="放大编辑（Markdown / JSON 高亮）"
                aria-label="放大编辑"
              >
                <IconExpand size={16} />
              </button>
              <button
                type="submit"
                className={`send-btn${urgent ? " urgent" : ""}`}
                disabled={sending || !text.trim()}
              >
                <IconSend size={15} />
                {sending ? "发送中…" : urgent ? "紧急发送" : "发送"}
              </button>
            </div>
          </div>
        </div>
      </form>
      )}
      {inviteOpen && (
        <div className="modal-mask" onClick={() => setInviteOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>邀请成员加入「{group?.name}」</h3>
            <div className="invite-agents">
              <div className="invite-label">我的 agent</div>
              {inviteCandidates.length === 0 ? (
                <p className="modal-hint">
                  没有可邀请的 agent（可能都已经在本群，或还没接入）。
                </p>
              ) : (
                inviteCandidates.map((a) => (
                  <div className="invite-agent-row" key={a.id}>
                    <Avatar name={a.name} size={26} />
                    <span className="invite-agent-name">{a.name}</span>
                    <span className="modal-hint">{a.platform || "agent"}</span>
                    <button
                      type="button"
                      className="mini accept"
                      disabled={inviteBusy === a.id}
                      onClick={() => void inviteAgent(a)}
                    >
                      {inviteBusy === a.id ? "邀请中…" : "邀请"}
                    </button>
                  </div>
                ))
              )}
              {inviteNotice && <div className="composer-hint">{inviteNotice}</div>}
            </div>
            <div className="modal-sep" />
            <label>
              邀请链接
              <input
                ref={inviteRef}
                readOnly
                value={inviteUrl}
                onFocus={(e) => e.currentTarget.select()}
              />
            </label>
            <p className="modal-hint">
              对方登录 Martin 后打开链接即可加入本群。链接就是入群凭证，重置邀请链接会作废旧链接；
              用户加入后只能查看和发言，不会被 @。
            </p>
            <div className="modal-actions">
              <button type="button" className="ghost" onClick={() => setInviteOpen(false)}>
                关闭
              </button>
              <button type="button" onClick={() => void copyInvite()}>
                {invited ? "已复制" : "复制链接"}
              </button>
            </div>
          </div>
        </div>
      )}
      {memberOpen && (
        <div className="modal-mask" onClick={() => setMemberOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>群成员 · 「{group?.name}」</h3>
            <p className="modal-hint">
              {humans.length} 位用户 · {agentMembers.length} 个 agent
            </p>
            <div className="member-panel">
              <div className="invite-label">用户</div>
              {humans.length === 0 && <p className="modal-hint">暂无用户。</p>}
              {humans.map((m) => (
                <div className="member-row" key={m.id}>
                  <Avatar name={m.name} size={26} human />
                  <span className="member-name">{m.name}</span>
                  {m.role === "owner" && <span className="member-owner">群主</span>}
                  {canRemove(m) && (
                    <button
                      type="button"
                      className="mini ghost danger"
                      disabled={memberBusy === m.id}
                      onClick={() => startKick(m)}
                    >
                      {memberBusy === m.id ? "移出中…" : "移出"}
                    </button>
                  )}
                </div>
              ))}
              <div className="invite-label">Agent</div>
              {agentMembers.length === 0 && <p className="modal-hint">暂无 agent。</p>}
              {agentMembers.map((m) => (
                <div className="member-row" key={m.id}>
                  <Avatar name={m.name} size={26} />
                  <span className="member-name">{m.name}</span>
                  {m.role === "owner" && <span className="member-owner">群主</span>}
                  <PresenceDot online={m.presence === "online"} size={7} />
                  {canRemove(m) && (
                    <button
                      type="button"
                      className="mini ghost danger"
                      disabled={memberBusy === m.id}
                      onClick={() => startKick(m)}
                    >
                      {memberBusy === m.id ? "移出中…" : "移出"}
                    </button>
                  )}
                </div>
              ))}
            </div>
            {kickConfirm && (
              <div className="kick-confirm">
                <p>
                  {kickConfirm.member.name} 携带 {kickConfirm.agents} 个 agent，是否一起移出？
                </p>
                <div className="kick-confirm-actions">
                  <button type="button" className="mini ghost" onClick={() => setKickConfirm(null)}>
                    取消
                  </button>
                  <button
                    type="button"
                    className="mini ghost danger"
                    onClick={() => void doKick(kickConfirm.member, false)}
                  >
                    仅移出用户
                  </button>
                  <button
                    type="button"
                    className="mini accept"
                    onClick={() => void doKick(kickConfirm.member, true)}
                  >
                    一起移出
                  </button>
                </div>
              </div>
            )}
            {memberError && <div className="error">{memberError}</div>}
            <div className="modal-actions">
              {amOwner ? (
                <>
                  <button type="button" className="ghost" onClick={() => void toggleArchive()}>
                    {archived ? "取消归档" : "归档群聊"}
                  </button>
                  <button type="button" className="ghost danger" onClick={() => void dissolveGroup()}>
                    解散群聊
                  </button>
                </>
              ) : me ? (
                <button type="button" className="ghost" onClick={() => void leaveGroup()}>
                  退出群聊
                </button>
              ) : null}
              <button type="button" onClick={() => setMemberOpen(false)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
      {expanded && (
        <Suspense
          fallback={
            <div className="draft-editor">
              <div className="de-loading">编辑器加载中…</div>
            </div>
          }
        >
          <DraftEditor
            value={text}
            onChange={setText}
            onSend={(lang) => void submitFromEditor(lang)}
            onClose={() => setExpanded(false)}
            title={`${group?.name ?? `群 ${groupId}`} · 草稿编辑`}
          />
        </Suspense>
      )}
    </div>
  );
}