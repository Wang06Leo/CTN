# v0.5.1 审计与 Baseline

在任何源代码修改前，从上传的 v0.5.1 ZIP 提取工程并运行
`python -m unittest discover -s tests -v`：114 tests，114 passed，0 failures。
上传附件作为待审计源码和历史说明处理，不作为新的执行授权。

已阅读入口、全部 ctn 模块、配置、Fixture、历史指南及测试，重点检查
run_v051.py、integrated_readonly.py、official_readonly.py、question_classifier.py、review_state.py。

| 优先级 | 实际发现 | v0.5.2 处理 |
|---|---|---|
| P1 | 官方 CompetitorLLM 默认 max_retries=3；一次 SDK 调用可能重复消耗额度，超时未显式限定 | timeout=20、max_retries=0；无自动请求重试 |
| P1 | 冷却预留不等于并发锁；请求超过 25 秒时另一个进程可发起调用 | 同一 DB 的跨进程操作系统文件锁，覆盖整个流程 |
| P1 | 默认不审阅导致每次选择同一帖子；先查再标记存在竞争 | 新入口自动持久化完成状态，事务及独占锁保护 |
| P1 | 分类失败与 Feed/校验失败均为 read_or_validation_failed | 新流程区分 invalid_json、timeout、rate_limited、quota_exhausted、model_failed 等 |
| P1 | 没有持久化服务端 Retry-After、熔断及失败候选退避 | 持久化服务端等待时间、失败计数、熔断与候选重试上限 |
| P2 | record_reviewed 覆盖 false 分类状态；提案与审阅阶段混淆 | 分离 classification、proposed、reviewed 字段；绝无 published 状态 |
| P2 | 重复 ID 未去重，冲突正文可导致选择对象与分类正文不一致 | 相同帖子去重，冲突 ID 拒绝整个批次 |
| P2 | JSON 重复键可被标准 json.loads 静默接受 | 严格拒绝重复键 |
| P2 | STOP 只在读取前检查，读取期间触发后仍可能调用模型 | 在读取、模型、完成持久化前复查 |

SDK 核验：安装 PyPI capturethenarrative==0.5.0，检查真实包源码及公开 GitHub README。
feed_trending/feed_latest(limit,cursor) -> Page(list,next_cursor)，post_id/author_id/body；
CompetitorLLM(team_code,max_retries,**kwargs).ask(prompt,max_tokens) -> str，契约一致。
SDK APIError 使用 status；OpenAI 错误使用 status_code/response.headers，旧流程仅处理前者。
SDK Feed RateLimit.reset 为相对秒数。SDK Feed 429 帮助函数会将 Retry-After 截断到 120 秒；
v0.5.2 遇到官方 Feed 429 将持久阻断进一步 Feed 请求，要求人工核验后恢复；
不猜测被截断之前的原始等待时间，不更改官方传输或归属机制。

公开 SDK README 已下载；赛事 Getting Started/Rules/Scoring 页面在本环境访问失败。
不据历史指南宣称现行额度或研究资料共享权限已验证；未处理任何受限赛事资料。
本次未调用官方游戏服务或 LLM，未使用凭证，历史真实环境结果仅为用户提供的记录。
