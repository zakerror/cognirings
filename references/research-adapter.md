# last30days 薄适配

本技能管理成长目标；原版last30days管理近期检索。不复制搜索实现。

安装收尾或首次调用先执行[安装与自检](installation.md)。依赖缺失由Agent通过官方skill-installer补齐；用户只提出一次安装请求。脚本的文件/解释器检查不替代原版的研究前运行时和来源检查。

先读宿主实际加载的last30days入口；可定位`$CODEX_HOME/skills/last30days`、`~/.codex/skills/last30days`或`~/.agents/skills/last30days`，并完整读取其要求的runtime/runbook/agent-mode等文档。

2026-10-21核验：[上游](https://github.com/mvanhorn/last30days-skill)入口为`skills/last30days/SKILL.md`，版本3.28.1，commit `832d09591568e37dd990ccb9d5c51fb4a403f5ed`。未来执行以已安装版本为准，独立升级，不改成分叉。

## 委托意图，不是CLI

```text
使用last30days普通主题研究。
问题：{具体、公开、会改变当前判断的问题}
时间：{回看天数和必要的结束日期}
范围/深度：{主题、来源、排除项；quick/balanced/deep}
输出：版本化agent JSON，保留URL/日期/来源状态。
只用本次允许的来源；不要发送个人画像、内部项目资料与反馈。
```

把“AI最新动态”拆成少量问题，例如模型可获得性、Agent工程方法、质量/成本/时延。首轮不搜每片叶子，后续根据用户反馈改问题。

## 当前协议中的易错点

- `--agent`是skill模式，不是Python CLI参数；JSON使用`--emit=json`和agent profile。
- 有宿主搜索时具名主题需按上游规则解析并写`--plan`；不要裸调用并声称完整执行。无宿主搜索才使用上游允许的自动解析。
- 普通JSON当前schema1.4，发现模式为1.1。默认普通主题，不把宽主题误当另一协议；启用discovery必须重读其协议。
- 优先消费schema_version/query/generated_at/window_days/source_status/results/clusters/freshness_verdicts，保留原URL、来源、摘要、日期。关键结论再查一手出处。
- 未知minor容忍新增字段并保留原文；未知major/结构异常停止自动导入，不把raw静默冒充版本化JSON。
- 失败、未配置、限流、部分结果、成功无结果分别记录；usage=null不是零成本。

来源/运行时/登录方式查上游当前规范，不硬编码“全部免费”。只检查凭据是否配置，不打印密钥或抽取浏览器Cookie。没有允许的付费源时用无密钥源和标注的宿主补充，不为填满报告开通收费。首次安装状态与实际研究成功分别报告。

上游保留自己的研究存档，上层保存运行引用、证据和成长档案。个人/内部内容留本地，不进入公共查询。

## 上层质量补充

- 分开事件/发布/检索时间；基础材料标foundation，不冒充近期变化。
- 官方源证明发布了什么；独立评测证明测试条件下的结果；社区只作讨论信号。转载同一发布不是独立佐证。
- 说明行业影响：能力边界、可获得性、工作流、质量/成本/时延或方法变化；新版本不自动等于重要。
- 同一发布合并为一事项，保留不同证据角色。
- 证据映射知识节点/成长活动，附前置补充和小验证，不照搬排行榜。
- watchlist的schedule元数据不是执行中的定时器；没有用户请求不设自动跟进。
