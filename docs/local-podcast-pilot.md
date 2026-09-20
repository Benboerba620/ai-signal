# 本地播客复用试点

2026-09-20 启用，2026-10-04 起复核；复核前不关闭云端发现与转录回退。

## 分工

- 本地研究系统维护采集名单，读取已有的原始转录；不重复下载或转录。
- `config/podcast-publication.json` 是独立发布名单。目前只允许 Dwarkesh Patel、Latent Space、SemiAnalysis，滚动窗口为 7 天。
- 导出器从采集名单读取 RSS 地址，只接受能在发布者 RSS 中确认身份的节目。URL 优先；无共同 URL 时，必须标题规范化一致、日期相差不超过一天、时长差不超过 5%（至少容许 60 秒），并且匹配唯一。
- 只读取指定 transcripts 目录的标准原始转录文件。私人研究、摘要、投资判断、路径、凭证不进入公开数据。
- Mac mini 仅写 `feeds/podcast-inbox/*.json`；GitHub Actions 合并正式列表、生成全文文件、维护过期索引。两端不写同一个列表。
- 公开摘要继续由用户 Agent 按现有官方 prompt 生成。中央摘要仍是手动调试选项，不上传个人投研摘要。

## 维护者使用

需要 `requirements.txt`。先只读预览：

```sh
python scripts/export_local_podcasts.py \
  --registry /path/to/workspace/scripts/podcast_feed_registry.py \
  --transcript-dir /path/to/workspace/output/podcast/ai/_pipeline/transcripts
```

预览报告包含 matched、unmatched、missing_local、errors。加 `--write` 写入发布收件目录；支持 `--proxy`，不需要内容 API key。任何 RSS 获取失败，本轮不写入或删除已有结果。

提交收件目录会触发同一个 GitHub 工作流的导入分支，仅合并播客，不重新抓取 X、论文或博客，也不运行 ASR。定时云端抓取在构建转录缓存之前加载本地结果，最终再合并一次，避免 RSS 当轮失败导致本地单集消失。

已有完整云端文本优先保留；本地补缺失/过短文本。不改已有单集的 guid，重复导入不产生第二条。导入不会更新整个云端 feed 的 generated_at，避免把旧扫描伪装为新扫描。来源以 transcript_source 标记，正文有 SHA-256 校验。

Mac mini 的发布器是独立任务，使用同步目录之外的专用 Git checkout；原本地播客任务的“不上传、不 Git push”边界不变。任务重复运行无新内容不会产生提交。

## 验收与复核

两周内查看：RSS 中有而本地没有的单集、未能匹配的转录、重复单集、同一期多次 ASR、上传失败与延迟。`local_import` 记录本轮新增、补齐、复用数；Mac mini 发布报告记录逐期缺失项及远端提交。

仅在实际定时运行证明覆盖和可靠性后，才考虑减少试点三档的云端 ASR。此试点不自动扩大名单、不自动取消云端抓取。云端在本地稿尚未到达时仍可能先做 ASR，这是过渡期的保留代价。

回退：把 publication 配置的 enabled 设为 false 并停用 Mac mini 发布任务；云端发现/转录保持可用。已进入正式列表的节目按原滚动窗口和 14 天全文缓存规则退出，不批量删除旧材料。
