# agenote → codex 注入器（recipe 级模板）

codex 内置 Claude 兼容 hooks 引擎（12 事件；SessionStart matcher
startup/resume/clear/compact/fork），`additionalContext` flatten 后进
developer 消息并**进 rollout 持久化**。本目录是模板：协议形状按 Claude
兼容引擎写，`[hooks]` config.toml 键名以你本版 codex 文档为准（recipe 级
不设验收门，R2 漂移自担）。

## 挂接（示例，待实装核对）

`~/.codex/config.toml`：

```toml
[hooks]
# Claude 兼容事件表（内联）；也可打包进插件 manifest 携带 hooks 走信任体系
SessionStart = [
  { hooks = [
    { type = "command", command = "/home/you/Projects/agenote/agenote/injectors/codex/session-start.sh", timeout = 15 },
  ] },
]
UserPromptSubmit = [
  { hooks = [
    { type = "command", command = "/home/you/Projects/agenote/agenote/injectors/codex/user-prompt-submit.sh", timeout = 15 },
  ] },
]
```

- **首次信任确认**：codex 的 hook 需 trust（trusted_hash）后才生效，首次
  启动会提示确认；自动部署需走托管（managed）模式——不要绕过信任机制。
- 预算超限行为：per-hook `additional_context_limit`（默认 2500 **tokens**）
  超限自动 spill 到 `<temp>/hook_outputs/<thread_id>/` 并以头尾预览+路径回引。

## 预算与三件套

| 项 | 值 | 说明 |
|---|---|---|
| 简报/recall 单次预算 | 2800 字符 | CJK ≈1–1.5 字符/token 的保守折算，保不 spill；英文占比高时可能仍触 spill，可下调或调 `additional_context_limit`（设计 R5/Q2） |
| 三件套 | 同 claude | `../lib.sh`：指纹未变不重注 / query 门槛+滤空跳过 / 累计 24000 触顶停 recall |
| 状态文件 | `~/.cache/agenote/injectors/codex-<session_id>.json` | 与其他宿主同款布局 |

## 安装前置

codex 自带记忆**默认已关**（`[features] memories` 默认 false）——保持关闭即
可；若曾手动开启需关回（`codex features disable memories`），防双真相源。
`agenote doctor` 可检测。

## 验收

```bash
# 离线 dry-run（协议形状同 claude）
printf '%s\n' '{"hook_event_name":"SessionStart","session_id":"dry1","cwd":"'$PWD'","source":"startup"}' \
  | bash injectors/codex/session-start.sh | python3 -m json.tool

# 宿主内验收（调研已验证的观测面）：
codex debug prompt-input "测试问题"
# 渲染单轮完整模型可见输入（AGENTS.md、hook 注入结果全在内），核对
# developer 消息里是否出现 agenote-context 块与预算是否触发 spill
```

## 待实装验证项（recipe 级全部标注）

- `[hooks]` config.toml 的确切键名/结构（模板按 Claude 兼容假设写）。
- stdin JSON 字段名、SessionStart source 取值（模板假设与 claude 相同）。
- 输出封包用 `hookSpecificOutput.additionalContext`（claude 形状）；codex
  是否也接受裸 `additionalContext` 字段待实测。
- 2800 字符折算以 `codex debug prompt-input` 实测 spill 率为准。
