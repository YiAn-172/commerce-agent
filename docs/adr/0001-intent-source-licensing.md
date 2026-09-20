# ADR-0001：意图数据源许可证与来源门禁

状态：Accepted  
日期：2026-09-15

## 决策

1. Full E-commerce 聚合数据只允许 `synthetic_api_generated`、`bitext_customer_support`、`bitext_retail_ecom` 三种可识别 provenance 进入候选集；ASOS 和 Amazon 派生记录默认隔离。
2. MASSIVE 使用 SetFit 中文镜像下载，但许可证依据上游 Amazon MASSIVE NOTICE/ LICENSE 的 CC BY 4.0，并保留归属信息。
3. SMP2017 仓库只有“开源供研究使用”的 README 声明，没有 SPDX/OSI LICENSE，因此仅用于本地非商业研究，原始数据及其派生文本不随仓库再分发。
4. 直接增加 Bitext Retail 源，许可证为 CDLA Sharing 1.0。其原始文件不提交，模型/数据发布前需重新审查归属和 ShareAlike 义务。
5. 所有外部原始数据均被 `.gitignore` 排除；仓库只保存下载脚本、revision、哈希、统计、许可证决策和少量自有样例。

## 原因

聚合数据集页面上的许可证不能自动消除上游数据条款。逐 provenance 放行和 direct-source 下载让实验可继续，同时避免把来源不明的数据包装成可自由再分发数据。

## 后果

- 25,000 条内部实验集可以构建，但不能直接整体公开上传。
- 发布模型前必须提供数据卡，并重新判断训练权重是否受各数据源条款影响。
- 任何 revision、许可证或来源结构变化都会导致门禁失败并要求人工复核。
