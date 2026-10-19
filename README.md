# Capture the Narrative 2026 — v0.5.2

Agent Reliability & End-to-End Integration。基于上传的 v0.5.1 增量开发，保留全部原模块和 114 项原测试；推荐新入口 `run_v052.py`。Python 3.12 已验证；Windows PowerShell 命令如下（Windows 实机尚未验证）。

本阶段始终 **Read-only / Dry-run**。默认完全离线，不登录、不注册，不执行真实 Post、Reply、Like、Repost、Follow、Report。`proposed_kind: reply` 只是原有固定合成回复的本地提案，不是发布，也不是由模型生成回复。

## 安装与离线验收（Windows PowerShell）

在解压后的项目根目录运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-official.lock
.\.venv\Scripts\python.exe scripts/test_offline.py
.\.venv\Scripts\python.exe run_v052.py
.\.venv\Scripts\python.exe run_v052.py --cycles 3
```

无需激活虚拟环境或修改 PowerShell ExecutionPolicy。纯离线标准库流程不要求安装 SDK；安装依赖后 SDK 契约测试也会执行。`requirements-official.txt` 保留原 SDK 版本声明，新增 `.lock` 固定本次验证的完整依赖版本。安装会下载依赖；运行与测试默认不联网。

`python scripts/test_offline.py` 执行全部测试并禁止测试主进程建立网络连接/DNS 查询。子进程测试仅运行硬编码 Fixture，不包含网络代码。SDK 契约测试只使用模拟传输，不向官方服务发送请求。

100/300/500 轮可复现检查（自动化测试另外对每种轮数验证排序、20 个候选逐一完成、后续 noop、SQLite 审计数量）：

```powershell
foreach ($n in 100,300,500) {
  .\.venv\Scripts\python.exe run_v052.py --cycles $n --db "data/offline-$n.sqlite3" --log "logs/offline-$n.jsonl"
  if ($LASTEXITCODE -ne 0) { throw "Offline validation failed: $n" }
}
```

这些离线实验数据库可复用；重复运行时已完成候选返回 noop。不要更换真实运行数据库来绕过冷却或重复保护。

## 不变的选帖规则与候选队列

Fixture 或 Official Feed → 规范化 → 验证重复 ID → 仅正文含 ASCII `?` → Python `len(text)` 降序稳定排序 → SQLite 排除已审阅/完成候选 → 每轮最多分类一条 → 结构化决策与验证 → 本地状态及审计。

- 全角 `？` 单独出现不符合条件；相同长度保持原 Feed 顺序。
- 完全相同的重复帖子去重；同 ID 不同正文/作者拒绝整个批次，不悄悄更换正文。
- 模型仅判断是否为真实信息请求，不参与排序。长度规则只是人为指定启发式，不是比赛评分预测。
- 新入口默认自动完成本地审阅，下一轮处理其他候选。继承的 `--record-reviewed` 兼容参数不再改变此行为。旧入口保留历史行为，不推荐用于 v0.5.2 可靠性验收。
- `--model none` 只验证并审阅本地提案；`--model fixture` 为默认合成分类器，可用 `--fixture-model-result false` 验证否决。
- 无候选、已完成或暂不可重试的候选全部跳过时，返回 noop，零模型调用。
- 队列由当前批次稳定排序重建；SQLite 只持久化 ID、长度和阶段，不保存 Feed 正文。消失于 Feed 的候选须再次出现或由同一合成 Fixture 提供后才能处理。

## 可选官方验证：必须分别显式启用

以下命令仅供用户在确认当前规则、团队授权和共享额度后自行执行。本次交付没有执行这些命令。

Team Code 保持环境变量优先于项目根目录 `.env`，不接受命令行密钥：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
.\.venv\Scripts\python.exe run_v052.py --check-config

# 仅官方 Feed；无 LLM
.\.venv\Scripts\python.exe run_v052.py --source official --confirm-public-read --model none --feed trending --limit 10

# 仅合成帖子 + 官方 LLM；消耗团队共享额度
.\.venv\Scripts\python.exe run_v052.py --source mock --model official --confirm-official-llm

# 官方 Feed + 官方 LLM；两项确认参数缺一不可
.\.venv\Scripts\python.exe run_v052.py --source official --confirm-public-read --feed latest --limit 10 --model official --confirm-official-llm
```

官方流程每次 CLI 仅允许一轮、最多一页 10 条、一次 LLM 请求，SDK `max_retries=0`、Feed timeout=10 秒、LLM timeout=20 秒。服务端响应优先于本地 65 秒 Feed / 25 秒 LLM 冷却下限。无候选不读取 Team Code、不初始化 LLM。

若先用 `--model none` 审阅某帖子，它不会在随后一轮被重新分类；请按需要直接选择官方 LLM 模式，勿删除正式状态数据库来制造 E2E 成功。

验收真实 E2E 必须在同一 outcome 中核对：官方 Feed 实际成功、selected_post 存在、`llm_calls=1`、`classification_source=official_llm`、`classification` 为布尔值、结果为 proposed 或 classified_not_question、`real_e2e_classification_success=true`。`false` 也是有效分类；noop、无效 JSON、超时不能算成功。自动化测试中的模拟传输即使产生相同字段，也不能作为真实服务证据。

## 状态与故障处理

默认 `data/v052_review_state.sqlite3`，JSONL 为 `logs/v052_readonly.jsonl`。`--db` 可指向旧 v0.5.1 数据库：新增表，原有带命名空间的 reviewed_posts 自动被尊重，不破坏原表。fixture 与 official ID 隔离，trending/latest 共用官方命名空间。

- `proposed`：本地提案已验证，reviewed=true；classification 可为 true 或 null（未分类）。
- `classified_not_question`：classification=false，reviewed=true，proposed=false，noop。
- `invalid_json` / `input_rejected`：该候选失败终止，不是成功审阅；下次可处理其他候选。
- `timeout` / `model_failed` / `feed_or_validation_failed`：失败；无自动请求重试，候选最多三次尝试，重试需后续用户调用且退避已到期。
- `rate_limited`：持久化等待时间，不在当前进程循环重试。
- `quota_exhausted` / `access_denied`：阻断相应服务，等待人工核验。
- `cooldown` / `circuit_open` / `service_blocked` / `worker_busy`：未发送被阻止的请求；无成功回复含义。
- `configuration_failed`：所选模型需要的本地凭证不可用，零 LLM 请求。

同一数据库的所有 v052 worker 由 OS 文件锁串行化整个流程；进程退出/崩溃释放锁，无过期租约允许在途请求重叠。SQLite WAL + BEGIN IMMEDIATE + 唯一键保护认领和完成；网络期间不持有 SQLite 写事务。相同 DB 锁不覆盖队友、独立数据库或旧入口，团队仍需集中调度。

每服务连续三次请求失败触发至少 300 秒熔断，跨重启保留；成功请求清零失败计数，noop 不解除模型熔断。阻断/冷却不消耗候选尝试次数。Feed 429 的原 SDK 异常截断等待信息，故持久阻断 Feed；不能自动恢复。确认官方等待时间/额度/权限后，维护者可在关闭所有进程时仅解除 service_guard 的对应服务 blocked 标记；不得删除历史记录或缩短 next_at。此版本不提供一键清空或强制重试 CLI。

Kill Switch：项目根目录创建 `STOP`（或指定 `--stop-path`）：

```powershell
New-Item -ItemType File STOP -Force
# 维护结束、确认可运行后再移除
Remove-Item STOP
```

检查点位于读取前、读取后、模型前、完成前。不能撤回已经在途的请求；请求完成后检测 STOP，不完成审阅或提案。所有正常结果首先写 SQLite reliability_audit，随后导出 JSONL；JSONL 失败不丢失 SQLite 记录，CLI 非零退出。worker_busy 只写 SQLite，避免与正在写 JSONL 的进程竞争。

日志仅含固定状态、ID、长度、布尔值和数值元数据；不写正文、回复、Prompt、原始异常或凭证。fixture 中只存合成内容；不要上传 `.env`、数据目录或受限赛事数据。

## 验收记录与范围

见 [AUDIT_V051.md](AUDIT_V051.md)、[TEST_RESULTS_V052.md](TEST_RESULTS_V052.md)、[CHANGELOG_V052.md](CHANGELOG_V052.md)、[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)。

官方参考：
- https://github.com/capturethenarrative/pypi-capturethenarrative
- https://capturethenarrative.com/info/competitions/2026/getting-started/
- https://capturethenarrative.com/info/competitions/2026/rules/
- https://capturethenarrative.com/info/competitions/2026/scoring/

当前可读取公开 SDK 文档，不代表受限赛事资料获准共享。未访问、导入或处理受限资料。历史指南仅供版本历史参考。
