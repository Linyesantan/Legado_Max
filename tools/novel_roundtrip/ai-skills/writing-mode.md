# 写作模式：接入手机改稿回传

以下内容加入 webnovel-writer 的 `webnovel-write/SKILL.md`（上游：https://github.com/lingfengQAQ/webnovel-writer）。

## 硬规则（加到「硬规则」节）

- **用户没要求严禁擅自行动**：只是进入写作模式、询问进度或查设定时，不得擅自续写、起草新章或推进剧情；开始写新章必须有明确指令。
- **优先查看书源回传内容**：进入写作模式或响应小说任务时，先运行 `novel-roundtrip --json changes` 检查手机回传；有未处理的回传（尤其 `phone-conflict` 冲突记录），先呈现给用户并听取处理指令，不能绕过直接写新内容。
- **可以润色，严禁擅自扩写**：润色只限语句通顺、去 AI 味、排版和错漏字；不脑补设定、不加情节、不拉长篇幅，保留用户原句骨架与节奏。
- **严格尊重用户改动**：用户删除、替换、精简掉的内容，后续润色或起草时绝不加回，以用户最新改稿为最高事实。
- **回传换行分段必须保留**：回传中的换行表示用户要分段，不得合并回一段，只规范段首全角缩进（`　　`）和空行。
- **不越界**：只处理正在改动的目标章或用户指定范围，不主动翻看、扫描或改动其他章节。

## 写章前读取改稿记录（加到上下文准备步骤）

```bash
MANUSCRIPT_STATE="$HOME/.local/state/novel-roundtrip/$(basename "$PROJECT_ROOT")"
if [ -f "$MANUSCRIPT_STATE/history.sqlite3" ]; then
  novel-roundtrip --project "$PROJECT_ROOT" --state "$MANUSCRIPT_STATE" --json changes --limit 20
fi
```

把这些人工改稿作为 `human_revision_records` 传给 context-agent，尊重用户改过的正文，不因旧摘要或旧缓存把它改回。
发现故事事实冲突时指出冲突，不自行撤销人工改稿；不能从个别修改推断永久风格偏好。

## 章节批注（独立于正文）

改稿前读取 `novel-roundtrip snapshot <章号>` 的 `annotations`，或运行 `novel-roundtrip --json annotations <章号>`。批注只存独立状态库，不进入正文、书源或 TXT。

AI 处理实际回传或冲突时，简记用户最终采用的改动，如 `双眸→双眼（用户修改）`。尊重用户最新修改，不擅自改回、不推导全书偏好；这些约束只放在 skill，不在各条批注里重复。用户再次修改时更新原批注，避免堆叠旧记录。

- 新增：`novel-roundtrip annotate <章号> --base <当前revision> --anchor '<当前原文唯一片段>' --text '<简洁事实>'`。
- 修改：相同命令加 `--id <批注ID> --version <当前版本>`，可同时更换锚点与内容。
- 删除：`novel-roundtrip unannotate <章号> <批注ID> --version <当前版本>`。

锚点状态 `missing` / `ambiguous` 时核对回传后更新，不猜位置、不恢复旧文。正文基准或批注版本冲突时重新读取。仅开发接口时不为真实章节补批注。

## 未处理冲突（必须最先看）

`novel-roundtrip --json changes` 返回含 `open_conflicts` 时，说明手机改稿和 AI 改了同一段，正文未写入。AI 必须在做任何别的事之前主动呈现每处 base/ai/user 三版对比，默认采用用户版本；写入后执行 `novel-roundtrip resolve <id>` 标记已处理。AI 只改了段首缩进的情况会自动合并，不算冲突。
