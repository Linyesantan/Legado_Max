---
name: novel-roundtrip
description: 查看阅读Max在手机上回传的小说增删记录、合并与冲突记录，或恢复正文。当用户问“手机上改了什么”“删了什么、加了什么”“刚才改了哪几行”时使用。
---

链路：阅读Max在线书源 → 底栏「编辑」→「保存并回传」→ 书源服务 `/manuscript/<slug>/<章号>`。只上传修改位置、删掉和加上的文本及校验值。

查询手机改动：

```bash
novel-roundtrip --json changes
novel-roundtrip --json changes --limit 20
```

这是写回时已经存储的记录，包含章节、原/新行号、removed/added 原文、增删行数和字数。不得读全文自行 diff 或猜测修改。空记录如实说没有。

记录类型：
- `phone`：服务器未变，直接写入。
- `phone-merge`：AI 改过别的段落，已自动三方合并写入；`merge` 字段含基准版、服务器版、用户版。
- `phone-conflict`（status `conflict`）：用户和 AI 改了同一段且不同，正文未改。`conflicts[]` 给出每处 `base/ai/user`，`merged_preview` 在冲突处先采用用户版本。先把冲突呈现给用户，按指示处理；默认以用户版本为准，不得自行丢弃用户改动。
- `undo`：人工恢复。

- `novel-roundtrip --json history`：列历史；`changes <id>`：指定记录。
- 多本书用 `--project <项目目录>` 或环境变量 `NOVEL_PROJECT`，不要串读其他书。
- 用户明确要求恢复：`novel-roundtrip undo <id>`；有后续改动会拒绝覆盖，不要绕过。

回传只改 `正文/`，不动设定、大纲、审查报告和 `.webnovel/`。凭据不写入 skill、仓库或 APK。

## 未处理冲突（必须最先看）

`novel-roundtrip --json changes` 返回含 `open_conflicts` 时，说明手机改稿和 AI 改了同一段，正文未写入。AI 必须在做任何别的事之前主动呈现每处 base/ai/user 三版对比，默认采用用户版本；写入后执行 `novel-roundtrip resolve <id>` 标记已处理。AI 只改了段首缩进的情况会自动合并，不算冲突。
