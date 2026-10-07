# Skills：运营 SOP

这里的每个 `.md` 文件都是一份运营 SOP。AI 做分析时会按任务加载对应的文件，把它当成公司规定来遵守：

| 文件 | 用在哪里 |
|---|---|
| `daily-review-sop.md` | 每日复盘 |
| `product-diagnosis-sop.md` | 单品诊断 |
| `amazon-ads-playbook.md` | 每日复盘和单品诊断都会用到 |
| `listing-optimization.md` | 单品诊断 |

**这些文件是你最重要的资产。** 你觉得 AI 的建议不对时，不用改代码，直接把正确的做法写进对应的 SOP，下次分析就会按新规则来。
数值阈值（目标 ACOS、库存预警天数等）在 `.env` 里用 `AXIS_THRESHOLDS__*` 配置，SOP 里写判断逻辑和经验。
