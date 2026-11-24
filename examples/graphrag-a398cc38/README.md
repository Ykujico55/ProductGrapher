# GraphRAG 全仓业务图 · CodeWikiBench 评分结论

**加权得分为 82.59/100。** 在 GraphRAG 专属量表的 56 项要求中，How2Trust 生成的业务图有 48 项提供了足够的讲解证据，8 项证据不足。覆盖最完整的是配置与工作流：从读取设置文件、应用环境变量和命令行覆盖，到选择索引流程、模型、缓存和向量库，9 项都能在图中找到说明。

评测于 2026 年 11 月 25 日完成。受评版本为 Microsoft GraphRAG 提交 `a398cc38bb75c17ee37894f2f2a751e2231d9347`，业务讲解由 DeepSeek Flash 生成。Codex 只依据图中可见的讲解和关系说明判读，每项记 0 或 1，再用官方函数按层级和权重汇总。该分数衡量这份图的文档覆盖度，0 分表示图中证据不足。[固定版官方量表](https://huggingface.co/datasets/anhnh2002/codewikibench/blob/6d215eb7d50a164e370a9a5703b813f9da345965/raw_data/graphrag.json) · [官方评分规则](https://github.com/FSoft-AI4Code/CodeWikiBench/blob/00f56b656b9fb8f23b87aecbfdcf19510e3e4f36/src/judge/judge.py)

## 分项成绩

| 领域 | 覆盖项 / 总项 | 加权分 / 100 |
| --- | ---: | ---: |
| 索引流水线 | 15/18 | 85.76 |
| 查询引擎 | 9/11 | 82.00 |
| 配置与工作流 | 9/9 | 100.00 |
| 提示词工程 | 8/9 | 76.00 |
| 知识模型与数据结构 | 6/7 | 83.64 |
| 可视化与分析 | 1/2 | 50.00 |

## 8 个未得分项

| 主题 | 图中已有的说明 | 缺少的证据 | 对应评分项 |
| --- | --- | --- | --- |
| 关系语义类型 | 如何抽取关系端点、自由文本描述和数值权重 | 抽取时如何为关系分配语义类别，以及关系表如何表示这个类别 | [关系抽取](scorecard.html#rub-0.0.1.0.1) · [关系表](scorecard.html#rub-0.4.0.1) |
| 外部预建图 | 如何读取内部实体表和关系表来聚类、生成社区报告 | 如何映射并导入用户提供的图，以及如何把它接入社区检测和摘要流程 | [导入](scorecard.html#rub-0.0.5.0) · [接入下游](scorecard.html#rub-0.0.5.1) |
| 查询路径 | 全局查询如何汇总社区报告，DRIFT 如何生成追问、按评分调度 | 全局回答如何从高层报告追到具体原文，以及 DRIFT 如何根据问题特征选择检索模式 | [全局查询](scorecard.html#rub-0.1.1.2) · [DRIFT](scorecard.html#rub-0.1.2.1) |
| 提示词优化 | 如何采样文档、生成示例和组装提示 | 怎样在同一任务上比较多个提示版本的表现，再据此选择或修改提示 | [按效果优化提示](scorecard.html#rub-0.3.1.1) |
| Gephi 集成 | 如何导出 GraphML，以及内置视图如何按社区上色、按节点度数调整大小 | 如何把社区和布局数据交给 Gephi 做网络分析 | [Gephi 集成](scorecard.html#rub-0.5.0.0) |

[生产业务图](workbench.html#view=business) · [模块协作图](workbench.html#view=collaboration) · [逐项评分及源码证据](scorecard.html) · [机器可读评分](scores.json)
