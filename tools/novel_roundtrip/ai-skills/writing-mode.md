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
