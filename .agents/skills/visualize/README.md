# visualize（内置副本）

上游：<https://github.com/careerhackeralex/visualize> · MIT License（见同目录 `LICENSE`）

| 项 | 值 |
| --- | --- |
| 来源路径 | `skills/visualize/` |
| 引入版本 | commit `3010770`（2026-03-06，skill v0.3.0） |
| 引入时间 | 2026-11-02 |

## 为什么内置

第五步「可视化看板」直接用它出**单文件静态 HTML**，不部署、不起服务。
内置副本保证换机器 / 断网也能复现同一套版式，不与上游漂移。

## 本地改动（仅两处）

1. `SKILL.md` 开头加了「本项目（scm-ana）的收口约定」引用块：产物路径、数据内嵌、不部署、中文字体。
2. 其余文件与上游一致，未改。

## 升级

```bash
git clone --depth 1 https://github.com/careerhackeralex/visualize /tmp/visualize
cp -R /tmp/visualize/skills/visualize/. .agents/skills/visualize/
```

覆盖后把上面第 1 处的约定块补回 `SKILL.md`。
