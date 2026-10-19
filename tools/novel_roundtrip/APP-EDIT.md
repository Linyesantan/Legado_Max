# App 内编辑与增量回传

流程：打开在线书 → 阅读菜单底栏 **编辑** → 修改正文 → **保存并回传**。底栏原有的搜索、自动翻页、替换和亮度按钮保留。

书源服务提供 `/manuscript/<slug>/<chapter>`，与书源同端口。App 从当前章节 URL 推导同源地址，沿用已导入书源的请求头；APK 和仓库不含账号密码或编辑密钥。
读取时拿原始 Markdown（不经过段落净化），程序保留标题行。

## 回传协议

手机用 Myers 差分算出真正的增删，连同打开时的原文和改后全文一起提交：

```json
{
  "mode": "auto",
  "schema": "novel-delta/1",
  "base_revision": "打开编辑页时原文件的 SHA256",
  "after_sha256": "改后 LF 正文的 SHA256",
  "proof": "服务端签发的章节与版本校验凭据",
  "base_raw": "打开编辑页时的原文",
  "edited_raw": "用户修改后的全文",
  "changes": [
    {"offset": 25, "removed": "旧", "added": "新"}
  ]
}
```

offset 从 0 开始，按 Unicode code points 计数，含标题、空行和换行；emoji 不按 UTF-16 半个字符算。
服务端校验凭据、基准快照、每段被删原文和最终哈希，任何不一致都拒绝。

## 服务端处理

| 情况 | 结果 |
| --- | --- |
| 服务器正文没变 | 直接写入，`status: applied` |
| 服务器已被改（如 AI 润色），双方改的是不同段落 | 按段落三方合并后写入，`status: merged` |
| 双方把同一段改成一样 | 只保留一份 |
| 双方把同一段改得不同 | 不改正文，记录冲突，`status: conflict` |

冲突记录包含基准版、服务器当前版、用户编辑版、每处冲突的 `base/ai/user` 文本，以及「冲突处先采用用户版本」的 `merged_preview`，供 AI 处理。
重复请求按规范化改动包哈希去重，响应丢失后重试不会重复写入。

App 在请求前持久化草稿；失败可重试，关闭也保留草稿。写入或合并成功后刷新当前章缓存；冲突时弹窗说明正文未改动。

## 接入书源服务

`manuscript_api.py` 与 `novel_roundtrip.py` 放同一目录，书源服务把该目录加入 `sys.path`：

```python
from manuscript_api import handle as handle_manuscript

# GET 路由开头、以及 POST 读取 body 之前：
if handle_manuscript(self, path, BOOKS_ROOT, EDIT_TOKEN, expected_authorization_header):
    return
```

完整示例见 [server/](server/)。

## 记录与 AI

```sh
novel-roundtrip --json changes             # 最近一次（含冲突记录）
novel-roundtrip --json changes --limit 20  # 最近多次
novel-roundtrip undo <记录ID>             # 恢复已写入的改动
```

记录类型：`phone` 直接写入，`phone-merge` 自动合并写入，`phone-conflict` 冲突未写入，`undo` 人工恢复；PC 端 AI 走 `snapshot`/`write`，对应 `ai`、`ai-merge`、`ai-conflict`；绕过工具直接改文件会补 `external`。
AI 写作模式接入见 [ai-skills/](ai-skills/)。
