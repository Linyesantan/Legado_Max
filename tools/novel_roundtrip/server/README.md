# 服务端部署

书源服务 `novel_source_server.py` 只依赖 Python 3 标准库，同时提供：

- Legado 书源：`/sources.json`、`/source/<slug>.json`、`/book/<slug>.json`、`/c/<slug>/<num>.json`、`/search?key=`
- App 改稿回传：`/manuscript/<slug>/<章号>`（由上级目录的 `manuscript_api.py` 处理：未变直接写入，不重叠自动合并，同段冲突只记录）
- 浏览器改稿页与历史（旧流程）：`/edit/<slug>`、`/changelog/<slug>`

## 书稿目录

`NOVEL_BOOKS_ROOT` 下每本书一个目录，章节放在 `正文/第NNNN章-标题.md`。

## 步骤

1. `mkdir -p ~/.config/novel && cp source.env.example ~/.config/novel/source.env && chmod 600 ~/.config/novel/source.env`，填好口令和认证。
2. `cp novel-source.service.example ~/.config/systemd/user/novel-source.service`，改 ExecStart 路径后 `systemctl --user enable --now novel-source`。
3. 自测：`curl --noproxy '*' http://127.0.0.1:8790/health`。
4. 手机要能访问这台服务器：有公网地址就按 `nginx-novel.conf.example` 反代并上 HTTPS；没有公网地址可用 `novel-tunnel.service.example` 反向隧道到一台有公网的服务器。
5. 手机阅读Max导入 `https://YOUR_DOMAIN/novel/sources.json`，打开书 → 底栏「编辑」→「保存并回传」。

`manuscript_api.py` 默认从本脚本上一级目录加载，可用 `NOVEL_ROUNDTRIP_LIB` 改路径。
凭据只放 `source.env` / `gateway-auth`，不进仓库、不进 APK。
