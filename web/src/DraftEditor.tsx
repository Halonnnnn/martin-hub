import { useEffect, useMemo, useRef, useState } from "react";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import {
  defaultHighlightStyle,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { IconX } from "./ui";

export type DraftLang = "md" | "json";

const LANGS: { id: DraftLang; label: string }[] = [
  { id: "md", label: "Markdown" },
  { id: "json", label: "JSON" },
];

function detectLang(value: string): DraftLang {
  return /^\s*[[{]/.test(value) ? "json" : "md";
}

function langExtension(lang: DraftLang): Extension {
  return lang === "json" ? json() : markdown();
}

// 编辑器配色跟随主题 token，不写死颜色；CodeMirror 的 theme 支持 CSS 变量
const editorTheme = EditorView.theme({
  "&": { height: "100%", fontSize: "13.5px", background: "var(--card)", color: "var(--ink)" },
  ".cm-scroller": {
    fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
    lineHeight: "1.65",
  },
  ".cm-content": { padding: "14px 0", caretColor: "var(--brand-1)" },
  ".cm-gutters": {
    background: "var(--inset)",
    color: "var(--faint-2)",
    border: "none",
    borderRight: "var(--bw) solid var(--line)",
  },
  ".cm-activeLine": { background: "rgba(var(--brand-rgb), 0.05)" },
  ".cm-activeLineGutter": { background: "rgba(var(--brand-rgb), 0.08)" },
  "&.cm-focused": { outline: "none" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
    background: "rgba(var(--brand-rgb), 0.18)",
  },
});

/**
 * 放大编辑：全屏草稿编辑器。Markdown / JSON 语法高亮，
 * 移动端（粗指针）自动降级为纯 textarea。
 */
export default function DraftEditor({
  value,
  onChange,
  onSend,
  onClose,
  title,
}: {
  value: string;
  onChange: (next: string) => void;
  onSend: (lang: DraftLang) => void;
  onClose: () => void;
  title: string;
}) {
  const [lang, setLang] = useState<DraftLang>(() => detectLang(value));
  const [touch] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches,
  );
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const langCompartment = useRef(new Compartment());
  const langRef = useRef<DraftLang>(lang);
  const cbs = useRef({ onChange, onSend, onClose });
  cbs.current = { onChange, onSend, onClose };
  langRef.current = lang;

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // 编辑器只建一次；语言切换走 Compartment 重配置。
  useEffect(() => {
    if (touch) return;
    const host = hostRef.current;
    if (!host) return;
    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          highlightActiveLine(),
          highlightActiveLineGutter(),
          drawSelection(),
          history(),
          indentOnInput(),
          EditorView.lineWrapping,
          keymap.of([
            {
              key: "Mod-Enter",
              run: () => {
                cbs.current.onSend(langRef.current);
                return true;
              },
            },
            {
              key: "Escape",
              run: () => {
                cbs.current.onClose();
                return true;
              },
            },
            ...defaultKeymap,
            ...historyKeymap,
          ]),
          langCompartment.current.of(langExtension(langRef.current)),
          syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
          editorTheme,
          EditorView.updateListener.of((update) => {
            if (update.docChanged) cbs.current.onChange(update.state.doc.toString());
          }),
        ],
      }),
    });
    viewRef.current = view;
    view.focus();
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // 只在挂载时建编辑器；初始值与语言取自挂载瞬间的值
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [touch]);

  // 外部改动（例如发送后清空、撤回提及）同步回编辑器
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current !== value) {
      view.dispatch({ changes: { from: 0, to: current.length, insert: value } });
    }
  }, [value]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ effects: langCompartment.current.reconfigure(langExtension(lang)) });
  }, [lang]);

  const stats = useMemo(() => {
    const lines = value ? value.split("\n").length : 0;
    return { lines, chars: value.length };
  }, [value]);

  const jsonError = useMemo(() => {
    if (lang !== "json" || !value.trim()) return "";
    try {
      JSON.parse(value);
      return "";
    } catch (err) {
      return `JSON 语法错误：${err instanceof Error ? err.message : "无法解析"}`;
    }
  }, [lang, value]);

  return (
    <div
      className="draft-editor"
      role="dialog"
      aria-modal="true"
      aria-label="放大编辑"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="de-card">
        <div className="de-head">
          <span className="de-title">{title}</span>
          <div className="de-lang" role="group" aria-label="高亮语言">
            {LANGS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`de-lang-btn${lang === item.id ? " on" : ""}`}
                aria-pressed={lang === item.id}
                onClick={() => setLang(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="de-close"
            onClick={onClose}
            title="收起（Esc）"
            aria-label="收起"
          >
            <IconX size={16} />
          </button>
        </div>
        <div className="de-body">
          {touch ? (
            <textarea
              className="de-textarea"
              value={value}
              autoFocus
              spellCheck={false}
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  onClose();
                }
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  onSend(lang);
                }
              }}
            />
          ) : (
            <div className="de-host" ref={hostRef} />
          )}
        </div>
        <div className="de-foot">
          <span className="de-stat">
            {stats.lines} 行 · {stats.chars} 字符
          </span>
          {jsonError ? (
            <span className="de-warn">{jsonError}</span>
          ) : (
            <span className="de-hint">
              {lang === "json"
                ? "发送时自动包成 ```json 代码块 · ⌘↵ 发送"
                : "⌘↵ 发送 · Esc 收起"}
            </span>
          )}
          <button type="button" className="de-send" onClick={() => onSend(lang)}>
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
