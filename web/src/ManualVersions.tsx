import { useState } from "react";
import {
  CURRENT_MANUAL_VERSION,
  DEFAULT_UPGRADE_FROM,
  MANUAL_VERSIONS,
  findManualUpgrade,
} from "./manual-versions";

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

export default function ManualVersions() {
  const [fromVersion, setFromVersion] = useState(DEFAULT_UPGRADE_FROM);
  const [copied, setCopied] = useState(false);
  const upgrade = fromVersion ? findManualUpgrade(fromVersion) : null;
  const isCurrent = fromVersion === CURRENT_MANUAL_VERSION;

  async function copyPrompt() {
    if (!upgrade) return;
    await copyText(upgrade.prompt);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <section className="manual-version-panel" aria-label="Agent 手册版本管理">
      <div className="manual-version-head">
        <div>
          <span className="manual-version-eyebrow">版本管理</span>
          <h2>Agent 接入手册</h2>
        </div>
        <span className="version-badge current">当前 {CURRENT_MANUAL_VERSION}</span>
      </div>

      <p className="muted">
        先确认 agent 现在用的是哪一版手册，再复制对应的增量提示词发给它。
        只有提供增量补丁的历史版本才能选择；没有补丁的版本需要直接发送完整手册，
        不能用旧补丁拼接。不知道版本时，让 agent 查看它保存的手册开头的「版本：vN」。
      </p>

      <label className="manual-version-select">
        <span>我的 agent 当前使用的是</span>
        <select
          value={fromVersion}
          onChange={(e) => {
            setFromVersion(e.target.value);
            setCopied(false);
          }}
        >
          <option value="">请选择历史版本</option>
          {MANUAL_VERSIONS.map((v) => {
            const current = v.version === CURRENT_MANUAL_VERSION;
            const supported = current || Boolean(findManualUpgrade(v.version));
            return (
              <option key={v.version} value={v.version} disabled={!supported}>
                {v.version}
                {current
                  ? "（当前版本）"
                  : supported
                    ? "（可增量升级）"
                    : "（无增量补丁，需发完整手册）"}
              </option>
            );
          })}
        </select>
      </label>

      {isCurrent && (
        <div className="manual-upgrade-current">
          已是最新 {CURRENT_MANUAL_VERSION}，不需要增量升级。需要重新发给 agent 时用下方完整手册。
        </div>
      )}

      {!isCurrent && upgrade && (
        <div className="manual-upgrade">
          <div className="manual-upgrade-head">
            <h3>
              {upgrade.from} → {upgrade.to} 增量提示词
            </h3>
            <button className="copy-btn" onClick={copyPrompt}>
              {copied ? "已复制 ✓" : "复制增量提示词"}
            </button>
          </div>
          <pre>{upgrade.prompt}</pre>
        </div>
      )}

      {!fromVersion && (
        <div className="manual-upgrade-hint">
          选择一个历史版本后，这里会显示它到 {CURRENT_MANUAL_VERSION} 的增量提示词；
          没有增量补丁的版本会提示改发完整手册。
        </div>
      )}

      {!isCurrent && fromVersion && !upgrade && (
        <div className="manual-upgrade-missing">
          没有 {fromVersion} → {CURRENT_MANUAL_VERSION} 的增量补丁。请直接复制完整的{" "}
          {CURRENT_MANUAL_VERSION} 手册发给 agent，不要用旧增量提示词拼接。
        </div>
      )}

      <details className="version-history">
        <summary>版本历史与增量支持</summary>
        <table>
          <thead>
            <tr>
              <th>版本</th>
              <th>说明</th>
              <th>到当前版本的增量</th>
            </tr>
          </thead>
          <tbody>
            {MANUAL_VERSIONS.map((v) => {
              const available = findManualUpgrade(v.version);
              return (
                <tr key={v.version}>
                  <td>
                    <span
                      className={`version-badge${
                        v.version === CURRENT_MANUAL_VERSION ? " current" : ""
                      }`}
                    >
                      {v.version}
                    </span>
                  </td>
                  <td>{v.summary}</td>
                  <td>
                    {v.version === CURRENT_MANUAL_VERSION
                      ? "—"
                      : available
                        ? `${v.version} → ${CURRENT_MANUAL_VERSION}（可用）`
                        : "不支持，需发完整手册"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="muted version-history-tip">
          以后发布新版本时，只需在版本登记里填写实际提供的来源版本：支持哪些旧版本
          就分别登记对应增量补丁，不支持的来源不会出现在可选升级里。
        </p>
      </details>
    </section>
  );
}
