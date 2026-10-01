# 商家运营报表自动化客户端

Tauri 2.0 + Python 任务内核 + React/TS。需求规格见 `docs/需求规格说明书-v0.1.md`（v0.3），壳内核协议见 `docs/任务协议-NDJSON.md`。

## 结构

```
merchant-report-client/
├── docs/                      # 需求规格 / 协议
├── kernel/                    # Python 任务内核（壳无关，可 CLI 独立运行）
│   └── merchant_report/
│       ├── events.py          # NDJSON 事件流（协议 v1）
│       ├── config.py          # 配置加载（stores/platforms/llm）
│       ├── fill.py            # 填表引擎（插入行/公式保护/边框/天气）
│       ├── weather.py         # 2345天气 + Open-Meteo 营业窗口温度
│       ├── llm.py             # OpenAI 兼容 LLM 客户端
│       ├── orchestrator.py    # 任务编排器（事件流输出）
│       ├── cli.py             # CLI 入口（M1 验证用）
│       └── platforms/
│           ├── base.py        # 适配器接口
│           └── taobao.py      # 淘宝闪购（登录/下载/流量/推广）
├── src-tauri/                 # Tauri 壳（py_runner.rs: spawn 内核 → NDJSON → emit）
├── src/                       # React 前端（7 页）
└── scripts/                   # 打包/资源装配
```

## 开发

```bash
# 内核 CLI（壳无关验证）
python3 -m merchant_report.cli run --store 梅溪湖 --date 2026-09-15   # 在 kernel/ 下
```
