# AI 写作模式接入

改稿回传的另一半在 AI 侧：AI 写作前先读你在手机上的改动，并把它当作最高事实。

本目录给出两份 skill（opencode / codex / pi 等 agent 通用的 `SKILL.md` 格式）：

- `novel-roundtrip/SKILL.md`：查询手机回传记录、按记录撤销。用户问“手机上改了什么”时触发。
- `writing-mode.md`：接入写作模式需要加的规则与步骤，基于 [lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) 的 `webnovel-write` skill 修改。

## 安装

```sh
cp -r novel-roundtrip ~/.config/opencode/skill/        # 或你的 agent skill 目录
ln -s "$PWD/../novel_roundtrip.py" ~/bin/novel-roundtrip
```

再把 `writing-mode.md` 中的规则和“写章前读取改稿记录”一段合入你的 `webnovel-write/SKILL.md`。
