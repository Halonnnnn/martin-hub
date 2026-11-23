import { useEffect, useState } from "react";

/**
 * 极简前端路由：用 History API 把页面状态映射到 URL，不引入路由库。
 *
 * URL 约定（相对站点根路径，也就是部署后的 baseUrl）：
 *   /              首页（已登录进聊天，未登录看接入指南）
 *   /chat          聊天
 *   /chat/<id>     聊天并选中某个群聊或私聊
 *   /friends       好友
 *   /agents        协作
 *   /account       设置（/info 为别名）
 *   /guide         指南与流程
 *   /login         登录 / 注册
 *   /admin         管理控制台
 *   /setup         首次初始化
 *   /join/<code>   邀请链接，登录后自动弹出入群框
 *
 * 新增页面时：在 RouteName / SIMPLE_ROUTES 里加一项，再在 App 里渲染对应组件即可。
 */

export type RouteName =
  | "home"
  | "chat"
  | "friends"
  | "agents"
  | "account"
  | "guide"
  | "temp"
  | "login"
  | "admin"
  | "setup"
  | "join";

export interface Route {
  name: RouteName;
  /** 仅 /chat/<id>：群聊或私聊的 id */
  groupId?: string;
  /** 仅 /join/<code>：邀请码 */
  code?: string;
  /** 仅 /t/<id>：临时会话的公开 id */
  sessionId?: string;
}

const SIMPLE_ROUTES: Record<string, RouteName> = {
  "/chat": "chat",
  "/friends": "friends",
  "/agents": "agents",
  "/account": "account",
  "/info": "account", // 别名：账号信息
  "/guide": "guide",
  "/t": "temp",
  "/page": "guide", // 别名：公开说明页
  "/login": "login",
  "/admin": "admin",
  "/setup": "setup",
  "/join": "join",
};

function normalize(pathname: string): string {
  const p = pathname.replace(/\/+$/, "");
  return p === "" ? "/" : p;
}

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function parseRoute(pathname: string): Route {
  const path = normalize(pathname);
  if (path === "/") return { name: "home" };

  const join = /^\/join\/([^/]+)$/.exec(path);
  if (join) return { name: "join", code: safeDecode(join[1]) };

  const temp = /^\/t\/([^/]+)$/.exec(path);
  if (temp) return { name: "temp", sessionId: safeDecode(temp[1]) };

  const chat = /^\/chat\/([^/]+)$/.exec(path);
  if (chat) return { name: "chat", groupId: safeDecode(chat[1]) };

  const name = SIMPLE_ROUTES[path];
  if (name) return { name };

  // 未知路径回落到首页，避免直接展示空白页
  return { name: "home" };
}

export function routePath(route: Route): string {
  switch (route.name) {
    case "home":
      return "/";
    case "chat":
      return route.groupId ? `/chat/${encodeURIComponent(route.groupId)}` : "/chat";
    case "join":
      return route.code ? `/join/${encodeURIComponent(route.code)}` : "/join";
    case "friends":
      return "/friends";
    case "agents":
      return "/agents";
    case "account":
      return "/account";
    case "guide":
      return "/guide";
    case "temp":
      return route.sessionId
        ? `/t/${encodeURIComponent(route.sessionId)}`
        : "/t";
    case "login":
      return "/login";
    case "admin":
      return "/admin";
    case "setup":
      return "/setup";
  }
}

/** 跳转并在浏览器历史里留下记录；replace=true 时替换当前记录（用于登录/退出后的归位）。 */
export function navigate(path: string, opts: { replace?: boolean } = {}): void {
  const url = new URL(path, window.location.origin);
  const next = url.pathname + url.search + url.hash;
  const current =
    window.location.pathname + window.location.search + window.location.hash;
  if (next === current) return;
  if (opts.replace) window.history.replaceState(null, "", next);
  else window.history.pushState(null, "", next);
  // pushState / replaceState 不会触发 popstate，这里主动通知订阅者
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** 当前 URL 对应的路由，并跟随浏览器前进/后退自动更新。 */
export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() =>
    parseRoute(window.location.pathname),
  );

  useEffect(() => {
    const sync = () => setRoute(parseRoute(window.location.pathname));
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  return route;
}
