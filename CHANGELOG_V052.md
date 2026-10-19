# v0.5.2 — Agent Reliability & End-to-End Integration

基于上传的 v0.5.1，保留原有策略、模块和全部 114 项测试，不引入 Multi-Agent Framework。

| 文件 | 修改原因 |
|---|---|
| `ctn/question_classifier.py` | JSON 重复键拒绝；官方 SDK 禁用重试、设定超时；支持候选确定后的延迟准备 |
| `ctn/official_readonly.py` | 失败请求也计数；每次调用重置计数；校验分页形状/游标；预留逐页钩子 |
| `ctn/reliability_state.py`（新增） | 复用 ReviewStore，新增本地阶段、原子认领、跨进程锁、服务端等待、熔断、结构化审计 |
| `ctn/reliable_readonly.py`（新增） | 稳定排序队列、重复保护、一次分类、最终验证、错误状态分离、STOP 检查、零写入流程 |
| `run_v052.py`（新增） | 离线默认 CLI、独立官方确认参数、一轮官方预算、凭证延迟读取、可读摘要及非零失败退出码 |
| `tests/test_v052.py`（新增） | 队列、状态、异常、安全、重启、真正多进程竞争及 100/300/500 轮离线测试 |
| `tests/test_sdk_contract.py`（新增） | 真实安装 SDK 配合合成传输的 Feed/LLM 全链路、分页、429、额度与无隐式重试契约验证 |
| `scripts/test_offline.py`（新增） | 完整测试套件执行期间禁用测试主进程的 socket 连接与 DNS |
| `requirements-official.lock`（新增） | 固定本次验证的全部可选 SDK 依赖版本 |
| `README.md` | v052 使用、验收、Windows PowerShell 命令、迁移及安全范围 |
| `AUDIT_V051.md`（新增） | 修改前 Baseline、实际问题优先级和 SDK 核验记录 |
| `TEST_RESULTS_V052.md`（新增） | 实际测试结果和验证证据边界 |
| `KNOWN_LIMITATIONS.md`（新增） | 未验证的官方服务、Windows、跨团队额度和崩溃边界 |

本地 reviewed、classified、proposed 与真实发布完全分离。没有任何 published/replied 成功记录或官方写入开关。false 分类保留原状态，不被 reviewed 标记覆盖。

旧 run_v051.py 和 ctn/integrated_readonly.py 保留历史行为；可靠性修复集中在新入口，避免破坏旧接口与历史测试。
