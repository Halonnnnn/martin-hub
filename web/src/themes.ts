export type UiTheme = "classic" | "warm" | "dark" | "glass" | "brutal";

export const UI_THEMES: { id: UiTheme; name: string; desc: string }[] = [
  { id: "glass", name: "极光玻璃", desc: "默认 · 柔和渐变与磨砂面板" },
  { id: "classic", name: "经典", desc: "靛蓝渐变 · 现有风格" },
  { id: "warm", name: "暖调纸感", desc: "米白纸底 · 衬线标题" },
  { id: "dark", name: "深色科技", desc: "近黑底 · 工程感" },
  { id: "brutal", name: "新粗野", desc: "粗边框 · 硬阴影 · 高对比" },
];

const LS_KEY = "martin.ui_theme";

export function isTheme(v: unknown): v is UiTheme {
  return UI_THEMES.some((t) => t.id === v);
}

export function localTheme(): UiTheme | null {
  try {
    const v = localStorage.getItem(LS_KEY);
    return isTheme(v) ? v : null;
  } catch {
    return null;
  }
}

export function defaultTheme(): UiTheme {
  return localTheme() ?? "glass";
}

export function setThemeDom(t: UiTheme) {
  document.documentElement.dataset.theme = t;
}

export function applyTheme(t: UiTheme, persist = true) {
  setThemeDom(t);
  if (persist) {
    try {
      localStorage.setItem(LS_KEY, t);
    } catch {
      // 私密模式等场景写入失败不阻断主题切换
    }
  }
}
