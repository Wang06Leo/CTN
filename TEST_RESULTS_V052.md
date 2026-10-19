# v0.5.2 实测结果

日期：2026-10-19；Linux / Python 3.12；capturethenarrative 0.5.0。

| 验证 | 结果 |
|---|---|
| 修改前原始 Baseline | 114 tests，全部通过 |
| 保留原始 tests 文件 | 与上传 ZIP 逐字节比较，全部原测试文件未改动 |
| 最终完整测试 | 158 tests，158 passed，0 failures，0 errors，0 skipped |
| 新增测试 | 44 项（其中 6 项真实安装 SDK + 模拟传输契约测试） |
| 禁止 socket/DNS 后执行完整套件 | 158 项全部通过；完整输出见 verification/unittest-v052.txt |
| 新增单元/集成测试 100/300/500 轮 | 每组 20 个合成候选按长度逐一处理，随后 80/280/480 次 noop；每组分类 20 次，官方 LLM 0 次，写入 0 次；SQLite 审计分别 100/300/500 行 |
| CLI 默认 Fixture 100 轮 | 100 completed：2 proposed，98 noop；官方 LLM=0，写入=0 |
| CLI 默认 Fixture 300 轮 | 300 completed：2 proposed，298 noop；官方 LLM=0，写入=0 |
| CLI 默认 Fixture 500 轮 | 500 completed：2 proposed，498 noop；官方 LLM=0，写入=0 |
| CLI 三轮 smoke | 2 proposed，1 noop；官方 LLM=0，写入=0 |
| 两个真实进程竞争 | 同一候选仅完成一次；另一个返回 noop 或 worker_busy |
| 持锁进程终止 | 锁自动释放，后续进程恢复处理 |
| 重启/旧审阅标记/processing 恢复 | 通过 |
| 分类 true/false/无效 JSON/重复键/超长输入 | 通过；失败不标成功、不变更排序 |
| 超时/网络失败/429/Retry-After/额度不足 | 通过（全部合成故障） |
| 冷却、熔断跨重启、STOP 各检查点 | 通过 |
| JSONL 故障 | SQLite 审计和完成记录保留；不重复已完成候选 |
| SDK 安装、锁文件重复安装、pip check | 通过；No broken requirements found |
| 保存的云安装脚本 | 本机执行成功，完整套件再次 158 项通过 |
| 官方真实 Feed | 未运行，未显式授权 |
| 官方真实 LLM | 未运行，未显式授权 |
| 真实 Feed→真实 LLM 同次 E2E | 未验证；模拟测试不等于真实成功 |
| 真实平台写入 | 0；全部禁用机制保留 |
| Windows / 新任务恢复 | 未实测 |

开发过程中首次新增测试发现 2 个错误：未知 adapter.read_calls 元数据（Mock 对象）不能序列化。
已修复为仅接受非负整数，并补充阻断周期零新增请求计数测试。后续全部测试通过，无遗留失败。

未伪造官方服务响应作为真实证据。SDK 契约测试在传输层明确使用合成 Fixture，生产代码没有测试响应替代分支。
固定的 real_e2e_classification_success 字段用于运行时契约判定；在模拟测试中同名字段不构成远端服务证据。

云配置已保存 install_script 和 start_skill（未修改网络/凭证配置），保存不等于发布。
需在环境设置中审阅并保存，再发布；GitHub 仓库在开发开始前无提交；GitHub 源码发布不代表云环境已发布，未验证新任务恢复。
