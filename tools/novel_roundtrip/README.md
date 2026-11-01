# 阅读Max：正文改稿往返

**当前首选：[App 内编辑 → 增量回传](APP-EDIT.md)**。点阅读页底栏「编辑」，保存时只发送增删；服务器没变直接写入，AI 改了别的段落自动合并，同段冲突只记录不覆盖。

下面是旧的离线 TXT 往返流程，无需服务器联网编辑时可用：服务器导出 → 手机导入、阅读、编辑 → 「分享改稿 TXT」→ 服务器回收。
原版阅读Max只修改缓存，普通「导出书籍」会格式化段落，不能用于本流程。

## 命令

```sh
novel-roundtrip export                 # 导出整书批次 TXT
novel-roundtrip check <TXT>            # 只校验
novel-roundtrip import <TXT>           # 校验并写回
novel-roundtrip snapshot <章号>        # 取 AI 改稿基准（revision + 原文）
novel-roundtrip annotations <章号>     # 读取独立章节批注
novel-roundtrip annotate <章号> --base <revision> --anchor '<唯一原文片段>' --text '<批注>'
novel-roundtrip annotate <章号> --base <revision> --anchor '<唯一原文片段>' --text '<新批注>' --id <批注ID> --version <版本>
novel-roundtrip unannotate <章号> <批注ID> --version <版本>
novel-roundtrip write <章号> --base <revision> --file <新稿>   # 带基准写回
novel-roundtrip changes                # 最近一次改动，终端逐行对照
novel-roundtrip --json changes         # 结构化记录，含冲突记录
novel-roundtrip --json history
novel-roundtrip undo <记录ID>
```

PC 端 AI 改正文也必须走 `snapshot` → 临时新稿 → `write --base`，禁止直接写文件。
基准没变直接写；基准已变（手机刚回传过）按段落三方合并，冲突时**以手机改动为准**、正文不写、
退出码 3 并打印三版对比。三种情况都会进 history（`ai` / `ai-merge` / `ai-conflict`），可 undo。
发现有人绕过工具直接改文件时，`snapshot`/`write`/`export`/服务端 `GET /manuscript` 会补一条
`kind=external` 记录（before=上次记录的 after），同样可 undo。

项目目录默认取环境变量 `NOVEL_PROJECT`（否则 `~/books/novel`），状态目录默认 `~/.local/state/novel-roundtrip/<项目目录名>`，也可用 `--project` / `--state` 指定。

章节批注保存在独立状态目录的 SQLite `annotations` 表，不改正文、不进入 TXT 导出或书源。
`snapshot` 同时返回 `annotations`；批注包含 `id`、`version`、`anchor`、`text`、创建时正文 `revision`。
查询动态返回 `line`、`location_status`（`matched` / `missing` / `ambiguous`）及 `revision_changed`；
其他段落变动不会让仍唯一匹配的锚点失效，锚点丢失或重复时不猜位置。
新增与修改校验当前正文 `--base`，修改与删除还校验批注 `--version`，失败退出码 2。
批注为非空单行、最多 240 字；锚点最多 4000 字且须唯一匹配当前原文。
接口不自动生成批注或回填历史，AI 的批注使用规则见 `ai-skills/writing-mode.md`。

手机安装改稿版 APK，导入导出的批次 TXT，用「编辑章节内容」修改；全部改好后在编辑菜单点 **分享改稿 TXT**，把文件传回服务器后执行 `import`。
同一附件重复导入（包括 BOM/CRLF 差异）不会再次写入；同批次的另一份不同文件在成功回收之后会被拒绝，以免旧副本覆盖新稿。

## 安全与记录

- 只替换 `正文/第NNNN章-标题.md`，不修改 `.webnovel`、大纲、设定或项目 Git 索引。
- 导出时保存精确原字节快照；回收使用完整批次清单，不按标题相似度猜测章节。
- 接受 UTF-8 BOM 和 LF/CRLF，并保留源文件风格；禁止容错解码和 trim。
- 缺章、乱序、重复、边界损坏、标题被删、空章、未知批次、截断、额外文本都拒绝整批。
- 正文被其他流程修改时整批拒绝；写入前后检查哈希，符号／硬链接拒绝。
- 每次提交先写事务日志和 before/after 快照，再逐文件原子替换；中断后下次命令自动回滚。
- `undo` 生成新历史；目标章已有后续改动则拒绝覆盖。
- JSON 的 `chapters[].edits[]` 含 `old_start/old_count/new_start/new_count/removed/added`，行号从 1 开始且含标题和空行。
- 字数为 Unicode code points，含标点和空格，不含 CR/LF。
- `changes` 读取写回时已生成的记录，不重读小说、不重新 diff。

## 安装与验证

只依赖 Python 3 标准库。可放一个包装脚本到 `~/bin/novel-roundtrip`：

```sh
#!/bin/sh
export NOVEL_PROJECT="${NOVEL_PROJECT:-$HOME/books/你的书}"
exec python3 /path/to/tools/novel_roundtrip/novel_roundtrip.py "$@"
```

```sh
python3 -m unittest discover -s tools/novel_roundtrip -v
./gradlew :app:testAppSReleaseUnitTest :app:assembleAppSRelease
```

测试只用合成文本；小说正文和本机状态不进仓库。
