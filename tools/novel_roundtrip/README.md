# 阅读Max：正文改稿往返

**当前首选：[App 内编辑 → AWS 增量回传](AWS.md)**。点底部“编辑”，保存时只发送增删，不再需要 TXT/QQ 往返。下面保留旧流程说明。

VM 导出 → 阅读Max改稿版本地导入、阅读和编辑 → “分享改稿 TXT”选择 QQ → 机器人自动写回。
原版阅读Max只修改缓存，普通“导出书籍”会格式化段落，不能用于本流程。

## 用户操作

```sh
novel-roundtrip export --send-qq
novel-roundtrip changes
novel-roundtrip --json changes
novel-roundtrip --json history
novel-roundtrip undo <记录ID>
```

手机安装本分支的共存版 APK，导入收到的 `dushi-批次.txt`。正常打开章节，使用现有“编辑章节内容”入口。
专用编辑框只显示原始正文，标题和边界由程序保存；不会经过替换净化、简繁转换、缩进重排和空行清理。
点保存后回到阅读界面，改动即时显示。全部改好后打开任一章节编辑菜单的 **分享改稿 TXT**，选择 QQ 机器人。
该动作先保存当前章，再分享包含所有章节改稿的完整 TXT，不能改用普通书籍导出或发最初下载的原文件。
关闭编辑器时用保存或“放弃本次编辑并关闭”，避免退出时静默丢稿。每个批次可以在手机上多次保存，再集中回传一次；下一轮改稿先重新导出。

机器人收到本人 TXT 时自动保存原附件、校验并回收，不需要先输入 `/文件`，不让 LLM 阅读正文。
同一附件重复发送（包括 BOM/CRLF 差异）不会再次写入或新增历史。
同批次的另一份不同文件在成功回收之后会被拒绝，以免旧副本覆盖新稿。
修改后的本地书即时可读；VM 书源立即提供最新正文，另一份在线书的旧缓存需要刷新目录／章节。

## 安全与记录

- 项目默认 `~/books/dushi`，只替换 `正文/第NNNN章-标题.md`。
- 状态默认 `~/.local/state/novel-roundtrip/dushi`，不修改 `.webnovel`、大纲、设定或项目 Git 索引。
- 导出时保存精确原字节快照；回收使用完整批次清单，不按标题相似度猜测章节。
- 接受 UTF-8 开头 BOM 和 LF/CRLF；源文件 BOM/换行风格保留。原件原样归档，禁止容错解码和 trim。
- 缺章、乱序、重复、边界损坏、标题被删、空章、未知批次、截断、额外文本都拒绝整批。
- 正文在 VM 被其他流程修改时整批拒绝。写入前后检查哈希，符号／硬链接拒绝。
- 每次提交先写耐久事务日志和 before/after 字节快照，再逐文件原子替换。进程中断后下次命令自动回滚未完成事务；发现第三方内容会停下保留现场。
- 这不是文件系统级多文件原子提交。外部程序若无视锁、在极短写入窗口同时覆盖相同文件，无法提供跨程序事务保证；并发哈希异常会阻止继续处理，快照仍保留。
- `undo` 会生成新历史；目标章已有后续改动则拒绝覆盖，可按历史倒序恢复。
- JSON 的 `chapters[].edits[]` 含 `old_start/old_count/new_start/new_count/removed/added`，行号从 1 开始且包含 Markdown 标题和空行。数组文本保留换行。
- 字数为 Unicode code points，含标点和空格，不含 CR/LF；记录包含增、删、净增字数和增删行数。
- `changes` 读取 SQLite 中回收时已经生成的记录，不重读小说、不重新 diff。`reports/*.diff` 可供终端工具查看；`rejected/*.json` 是拒收诊断，`blobs` 保存输入原件与快照。

## 安装与验证

`novel_roundtrip.py` 只依赖 Python 3 标准库，可用可执行包装脚本放到 `~/bin/novel-roundtrip`。
`export --send-qq` 调用现有 `~/.local/bin/qq-send-file.py --c2c`，QQ 凭据由该工具自行读取。
VM 的 `astrbot_plugin_qqfile` 已添加本人 TXT 的专用接收分支，本人标识来自私有 `~/.config/novel-roundtrip/qq-owner`。

```sh
python3 -m unittest discover -s tools/novel_roundtrip -v
./gradlew :app:testAppSReleaseUnitTest :app:assembleAppSRelease
```

测试使用合成文本，覆盖精确往返、损坏检测、重复、冲突、恢复与报告不读全文；小说和本机状态不进仓库。
最终设备验收：在已安装改稿版上改一行并分享给 QQ，终端执行 `novel-roundtrip --json changes` 核对，再验证重复发送不生成新记录。
