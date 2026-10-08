# 云端 CPU Whisper 生产转录

2026-10-08：维护者在完成 5 分钟样本和约 35 分钟整集试跑后，授权将播客转录切换到 GitHub Actions 云端 CPU Whisper。试跑数据和质量限制保留在 [试跑记录](cloud-whisper-pilot.md)。

## 运行边界

- 仍使用现有 `Generate Daily Feed` 工作流、标准 `ubuntu-latest` runner、原定时点与并发锁。播客常规轮每天 UTC 20:13；工作日 UTC 01:30 的 arXiv 专用刷新不运行播客转录。
- 原 Volc ASR 阶段及 `VOLC_ASR_API_KEY` 注入已移除，不会自动调用付费 ASR。旧手动脚本保留，自动化不再调用它。
- 原来源名单、关键词相关性、最小时长/字节数等筛选不变。只接收原策略允许的直链公开音频，不扩展 a16z、Capital Allocators 或人物 YouTube 搜索结果，也不抓取 YouTube 音频。
- 来源配置为中文时使用多语种 `small`/`zh`，其他原英文来源使用 `small.en`/`en`，均为 CPU int8、4 线程。其他显式语言跳过并记录原因。已完成的性能试跑仅覆盖英文，中文质量还需按实际结果检查。
- 先查配置中的公开字幕；无可用字幕时才运行 Whisper。字幕入口拒绝时停止该入口，不重试或绕过；仍可使用来源已经独立提供并获授权的 RSS 公开音频。音频本身拒绝、非音频 HTML、认证/CAPTCHA 等不会用代理、重建 YouTube 音轨或付费服务绕过。字幕不可用与音频结果分别记录。

## 持久队列与发布

`feeds/whisper-queue.json` 按来源+GUID 保存待处理快照。在刷新 feed 前先保存旧列表中的合格项，刷新后再补新项，因此节目退出 72 小时/原来源窗口后仍能完成处理。首次迁移队列从已审阅的旧快照带入3项待办（Semi034/035与Lenny/Tibo），并保留21项已有全文的去重记录；部署时再读取最新main feed补新项，不覆盖同期正常feed更新。

- 已有索引和正文文件都要核验，不能仅凭 `transcript_available` 认定成功。已完成项留下去重记录，避免全文缓存过期后重复转录。
- 已过期离开当前 feed 的节目成功后写入全文 sidecar 和 14 天索引，不把旧节目重新插入当前 feed。
- 每次最多顺序处理两项。开始外部工作前保存并推送 running/attempt 状态；每项成功或失败后再次独立检查并提交，不依赖整轮全部成功。
- 可重试失败最多 3 次，下一次最早分别在 6/12 小时后等后续调度处理。来源拒绝、无效音频、资源策略等终止性失败不会自动反复试。
- 发布按正文→索引→当前 feed→完成记录的顺序，文件原子写入。临时发布意图保存正文及哈希，允许从中断中恢复，不依赖已销毁的 runner 临时文件。
- 提交助手只提交播客 feed、全文索引、`.txt` 正文及队列，拒绝冲突/进行中的 rebase/预先暂存的其他文件；不提交临时文件或 X/arXiv/博客数据。无效 feed 不会作为成功发布；必要时只保存可恢复的队列意图并将任务标为失败。

## 时间、存储与内存

- 单集最多 30 分钟，含公开字幕探测、网络、模型加载和转录；全 job 仍最多 75 分钟。
- 转录开始前计算剩余工作预算，65 分钟后不再占用 worker，预留提交/上传时间；不足 5 分钟就让待办留到下轮。
- 音频下载上限 128 MiB、已测量时长上限 2 小时、单文件上限 1 GiB；临时目录轮询 2 GiB 上限。
- 生产 worker 及其进程组 RSS 轮询上限 6 GiB。轮询存在短暂超调，不是硬内核内存配额。超过资源预算时终止并记录，不租 GPU 或扩大付费资源。
- 音频、PCM 与模型缓存都在独立临时目录，worker 不继承 API key、用户代理凭证或 Hugging Face 隐式 token，退出清理。

## 手动检查与首轮部署

只读队列状态：

```sh
python scripts/cloud_whisper_queue.py --status
```

维护者在 Actions 的 `Generate Daily Feed` 中选择 `transcribe_only=true`，可只处理现有队列，不刷新 X/arXiv/博客。`transcribe_semianalysis=true` 只缩小到当前策略允许的 SemiAnalysis，不再强制绕过来源策略。

首次部署可由 main 上专用 `.github/whisper-production-request.txt` 的变更和 `[run-whisper-production]` 提交标记共同触发，同样走上述队列路径，仅播客转录。普通本地收件目录推送仍仅导入，普通源码或文档提交不会启动生产转录。

每輪保存 7 天独立 artifact，包含逐项 stats/字幕/错误和队列状态。即使部分项失败，先完成的正文和失败状态仍提交；最后用失败状态标记 workflow，不能把绿灯当成覆盖率证明。

暂停自动转录可将现有 `podcasts.transcription.enabled_by_default` 设为 false（需要维护者授权的配置变更）；队列仍可保留。显式手动 `transcribe_missing=true` / `transcribe_only=true` 可处理一次。不会自动回退到收费后端。

## 质量提示

Whisper 输出是机器转录，专名和领域术语可能出错。35 分钟试跑速度与完整覆盖已验证，但没有人工对齐参考稿或 WER/准确率结论。正文继续标记 `local_whisper` 并记录模型与语言；需要引用关键数字/专名时应回听原音频。
