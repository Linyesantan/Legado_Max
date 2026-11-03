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

## 电脑宵禁门禁

个人构建可设置 `CURFEW_STATUS_URL=https://YOUR_DOMAIN/novel/curfew/status`（Gradle 属性或环境变量）。
`manuscript.yml` 从同名 GitHub Actions repository variable 读取，缺失时拒绝构建，避免误发无门禁的个人安装包。
未配置的通用开源构建保持原行为；个人版本没有应用内关闭入口。

状态接口只公开是否锁定，不需要也不能携带书源口令。服务读取现有电脑宵禁真源：
`CURFEW_POLICY_LIB` 默认 `~/opt/workenv/curfew/curfew_policy.py`，`CURFEW_CONFIG` 默认同目录 `policy.json`，
`CURFEW_STATE` 默认 `~/.local/state/curfew/state.json`。暂停和提前解除沿用电脑 `curfew` 入口，不另设 App 暂停状态。
策略或状态缺失/损坏一律不放行，部署时必须保证文件可读。

协议 `curfew/1` 使用最长 4 秒长轮询和 200ms 状态变更检测，服务器正常时提前解除无须等原宵禁结束。
App 冷启动、重新进入前台先锁定，收到有效响应才放行；网络失败立即锁定，失去响应最多 6 秒后租约失效。
租约只用单调时钟，修改手机日期不能延长；随机 nonce 防旧响应被误用。锁定覆盖所有 Activity 并停止后台朗读，
不销毁原阅读/编辑页面；联网核验放行后自动恢复页面。断网时不能离线阅读是有意的严格策略。
开源应用内门禁不是 Android 设备所有者策略，无法阻止用户另装未限制版本或修改源码。
