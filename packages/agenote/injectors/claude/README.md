# agenote → claude-code 注入器（成品级）

把 agenote 记忆简报/召回实时注入 claude-code 会话。协议形状依据官方 hooks
文档（code.claude.com/docs/en/hooks，2026-09 核对；hookSpecificOutput.
additionalContext；UserPromptSubmit 注入上限 10000 字符）。宿主版本漂移
（R2）时先核对文档再改脚本。

宿主版本（验证时）：协议参照公开 hooks 文档 2026-09 版；验证机装有
claude-code 2.1.278（脚本为标准 hooks 协议，不依赖特定版本特性）。

## 挂点

`~/.claude/settings.json` 的 `hooks` 节（路径按实际安装位置改）：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/home/you/Projects/agenote/agenote/injectors/claude/session-start.sh",
            "timeout": 15
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/home/you/Projects/agenote/agenote/injectors/claude/user-prompt-submit.sh",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

- SessionStart matcher 不填 = 覆盖 `startup/resume/clear/compact` 四源；
  compact 自动重跑即免费获得重注入（简报缓存使重触发零 spawn）。
- 两个脚本三件套共享状态文件 `~/.cache/agenote/injectors/claude-<session_id>.json`
  （session_id 缺失退化为 `claude.json`；整目录可清理无副作用）。

## 预算与三件套

| 项 | 值 | 落点 |
|---|---|---|
| 简报单次预算 | 8000 字符 | session-start.sh 经 `../lib.sh` |
| recall 单次预算 | 4000 字符 | user-prompt-submit.sh（宿主上限 10000） |
| 简报指纹未变 | 缓存重放/不重注 | `../lib.sh`（MEMORY.org+memories/ 的 mtime+size） |
| recall 门槛 | prompt 有效长度 <6 跳过；全被 `recall_min_score` 滤空则记键不再 spawn | `../lib.sh` |
| 单会话累计 | 24000 字符触顶停 recall（SessionStart/compact 重置） | 状态文件 |

## 安装前置（写侧禁用，防双真相源）

settings.json 加 `"autoMemoryEnabled": false` + `"autoDreamEnabled": false`
（或 env `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`）。开关状态可由 `agenote doctor` 检测。

## 验收

```bash
# 1. 离线 dry-run（不进宿主）：输出应为合法 JSON 且 additionalContext 以
#    <!-- agenote-context v1 ... --> 开头
printf '%s\n' '{"hook_event_name":"SessionStart","session_id":"dry1","cwd":"'$PWD'","source":"startup"}' \
  | bash injectors/claude/session-start.sh | python3 -m json.tool
printf '%s\n' '{"hook_event_name":"UserPromptSubmit","session_id":"dry1","cwd":"'$PWD'","prompt":"一个足够长的真实问题"}' \
  | bash injectors/claude/user-prompt-submit.sh | python3 -m json.tool
# 同一 dry-run 第二次调用应零字节输出（指纹+query 未变不重注）

# 2. 全量自测
bash injectors/selftest.sh

# 3. 宿主内验收：claude --debug 启动看 hook 执行日志；会话内 /context 查看
#    注入占用；提问记忆相关问题验证 recall 内容在场
```

## 可调 env（均可不设）

`AGENOTE_INJECTION_BRIEF_BUDGET` / `AGENOTE_INJECTION_RECALL_BUDGET` /
`AGENOTE_INJECTION_SESSION_CUMULATIVE_BUDGET` / `AGENOTE_INJECTION_MIN_QUERY` /
`AGENOTE_INJECTOR_STATE_DIR`；语义开关在 agenote 侧
（`AGENOTE_INJECTION_ENABLED=false` 一键全关，输出零字节即自然不注入）。

## 待实装验证项

- stdin 字段名（`session_id`/`prompt`/`cwd`/`source`）以实际版本下发为准；
  脚本对缺失字段静默降级（无 session_id 用 host 级状态文件，无 prompt 不注）。
- claude 版本更新若调整 hook 事件名/预算上限，以 `--debug` 日志为准回改。
