# 中文电商一级意图标签规范 v1

本规范用于 25,000 条训练语料预标注与人工复核。模型只预测一个互斥主意图；多意图由独立检测器处理，低信息输入由置信度门控处理。

| 标签 | 正例判断 | 关键反例 |
|---|---|---|
| `product_search` | 用户已经给出商品、品牌、价位或硬条件，目标是找候选 | 只描述用途并期待方案属于 `product_recommend` |
| `product_recommend` | 结合预算、用途、人群给购买建议 | 指定两款比较属于 `product_compare` |
| `product_compare` | 比较两个及以上明确商品 | 找相似商品属于 `product_search` |
| `product_detail` | 指定商品的静态规格、兼容性、用法 | 实时库存或价格属于 `stock_price` |
| `stock_price` | 实时售价、优惠价、区域库存 | 活动通用规则属于 `policy_faq` |
| `order_status` | 订单是否支付/发货/取消/完成等总体状态 | 已发货后的节点轨迹属于 `logistics_tracking` |
| `logistics_tracking` | 快递公司、物流节点、停滞、预计送达 | 尚未发货属于 `order_status` |
| `cancel_order` | 明确取消未完成订单 | 收货后撤销交易属于 `return_exchange` |
| `return_exchange` | 新发起退款、退货、换货、维修 | 已申请后的到账/进度属于 `refund_progress` |
| `refund_progress` | 查询已经提交的退款进度或到账情况 | 只问能否退属于 `after_sales_eligibility` |
| `after_sales_eligibility` | 询问是否符合退换资格、时限、材料 | 通用政策说明属于 `policy_faq` |
| `policy_faq` | 配送、发票、支付方式、保修、运费、活动规则 | 具体订单事实不属于本类 |
| `complaint` | 明确投诉、严重不满、假货/欺诈指控 | 普通催物流不自动升级投诉 |
| `human_handoff` | 明确要求真人客服或人工处理 | “有人吗”且无人工诉求不直接归本类 |
| `chitchat` | 问候、感谢、结束、轻量闲聊 | 同句带业务诉求时业务意图优先 |
| `out_of_scope` | 明确属于非电商领域或系统不支持的任务 | 电商领域但信息不足应澄清，不是 OOS |

## 外部标签映射原则

- `direct`：文本语义和目标边界一致，可以作为候选。
- `out_of_scope`：只用于明确非电商语义，不能把智能家居控制伪装成商品咨询。
- `discard`：目标体系没有等价类，或映射会改变用户动作。
- 英文样本翻译后必须保留数字、订单号占位符和商品实体，不可新增业务事实。
- 所有自动映射都只是预标注；独立金标集必须人工逐条确认。
