# App 内编辑，通过 AWS 只回传增删

当前首选流程：打开在线书 → 点阅读菜单底部快捷栏的 **编辑** → 修改正文 → **保存并回传**。
不需要导出 TXT、计算章节编号、复制补丁或通过 QQ 发送文件。底部编辑按钮保留原来的搜索、自动翻页、替换和亮度按钮。

新增的 `/manuscript/<slug>/<chapter>` 路由复用书源服务和 AWS 网关，不新增公网端口。
App 从当前章节 URL 推导同源路由，沿用已导入书源的请求头；APK 和仓库不包含账号密码或固定编辑密钥。
读取时获取原始 Markdown（不经过段落净化）；编辑框展示原始正文，程序保留标题行。

保存时手机使用 Myers 差分计算真正的增删，只上传：

```json
{
  "schema": "novel-delta/1",
  "base_revision": "原文件SHA256",
  "after_sha256": "改后LF正文的SHA256",
  "proof": "服务端签发的当前章节与版本校验凭据",
  "changes": [
    {"offset": 25, "removed": "旧", "added": "新"}
  ]
}
```

offset 从 0 开始，按 Unicode code points 计数，含标题、空行和换行；emoji 不按 UTF-16 半个字符计算。
未修改的段落不包含在 POST 中。VM 验证版本、每一段删除原文、操作顺序和最终哈希，任何不一致都拒绝写回。
重复请求以规范化改动包哈希去重，即使保存成功后的响应丢失，重试也不会生成重复记录。

App 在请求前持久化草稿；失败可重试，关闭也可保留草稿。冲突时不强行覆盖，可以重新加载 VM 原文，原草稿另存备份。
仅在 VM 确认后更新阅读缓存，因此保存成功后回到阅读页即可看到改稿。

## VM 部署

`aws_delta.py` 和 `novel_roundtrip.py` 放在同一目录。现有书源服务需把该目录加入 `sys.path`，导入：

```python
from aws_delta import handle as handle_manuscript
```

在 GET 路由开始处及 POST 的读取 body **之前** 调用：

```python
if handle_manuscript(self, path, BOOKS_ROOT, EDIT_TOKEN,
                     json.loads(source_headers()).get('Authorization', '')):
    return
```

`source_headers()` 沿用现有私有配置生成认证头。AWS 保留 `/novel/` 转发到 VM 8790，POST 也转发到同一服务。
本机 `~/bin/novel-source-server.py` 已完成集成，`novel-source.service` 和 `novel-tunnel-aws.service` 继续管理现有进程。

## 写作 skill 与历史

```sh
novel-roundtrip --json changes             # 最近一次，精确原/新行号和增删文本
novel-roundtrip --json changes --limit 20  # 最近多次，一条命令读取
novel-roundtrip undo <记录ID>             # 检查后续冲突后恢复
```

`phone-aws` 为 AWS 回传记录。写回时已经生成逐行记录，AI 查询时不读全文、不运行 diff。
本机 `webnovel-write` 的查询分支直接读取以上命令；开始写新章时，会把已有人工改稿记录传给写作上下文。
历史和恢复机制仍使用独立的 `~/.local/state/novel-roundtrip/<slug>/`，回传不修改 `.webnovel/`、设定、大纲或审查报告。

旧 TXT/QQ 流程保留兼容，详见 [README.md](README.md)，默认应使用本页的新流程。
