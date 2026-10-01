# 任务协议 · Python 内核 → Tauri 壳 事件流（NDJSON）

**版本**: v1 · **用途**: `py_runner.rs` spawn 的每个任务子进程，stdout 逐行输出本协议 JSON；壳解析后 `emit` 到前端。

## 1. 传输约定

- 每行一个独立 JSON 对象（`\n` 分隔，UTF-8，无 BOM）
- stderr 只用于**诊断日志**（壳收集但不解析，任务失败时随错误报告展示）
- 任务的**人类可读进度日志**也走 stdout 事件（`type: "log"`），终端调试时可直接看
- 内核必须保证：任何异常都输出 `type: "error"` 或 `type: "result"` 后再退出，不允许无声死亡

## 2. 事件类型

### 2.1 生命周期
```json
{"type":"task_start","task_id":"run-20260916-153000","store":"梅溪湖店","date":"2026-09-15","platforms":["taobao"]}
{"type":"result","ok":true,"outputs":[".../模板_副本.xlsx"],"summary":{"营业额":205.99,"订单":12,"天气":"阴~多云"},"elapsed_s":213}
{"type":"result","ok":false,"error_code":"E_LOGIN_TIMEOUT","message":"5分钟内未检测到登录","retryable":true}
```

### 2.2 步骤进度
```json
{"type":"step","id":"download","name":"下载报表","state":"running","detail":"订单类型=全部 已提交"}
{"type":"step","id":"download","name":"下载报表","state":"ok","detail":"2 份文件落盘","elapsed_s":96}
{"type":"step","id":"fill","name":"填入模板","state":"failed","detail":"模板被 Excel 占用","error_code":"E_FILE_LOCKED","retryable":true}
```
- `state`: `running | ok | skipped | failed`
- `error_code` 枚举（前端映射为提醒文案）: `E_LOGIN_NEEDED` / `E_LOGIN_TIMEOUT` / `E_CAPTCHA` / `E_TASK_SLOW` / `E_SELECTOR_MISSING`（疑似改版，触发 LLM 自愈）/ `E_FILE_LOCKED` / `E_CHECKSUM_FAIL`（交叉校验）/ `E_NETWORK` / `E_TEMPLATE_INVALID`

### 2.3 人工操作请求（壳必须弹提醒 + 可选模态）
```json
{"type":"need_human","action":"login","platform":"taobao","message":"请在弹出的浏览器中完成登录/滑块验证","timeout_s":300}
{"type":"need_human","action":"confirm","message":"交叉校验失败：营业额≠主站+爆品团，是否强制继续？","choices":["强制继续","终止"]}
```
- `action`: `login | confirm | open_page`（open_page=引导用户去某页面手动操作）
- 壳收到后：Windows 通知 + 窗口横幅 + 浏览器窗口置顶；用户操作完成/超时，壳通过 **stdin 回写**（见 §3）

### 2.4 进度与日志
```json
{"type":"log","level":"info","msg":"📤 已提交任务: 订单类型=全部 (字段87列)"}
{"type":"progress","step":"download","pct":60}
```

### 2.5 LLM 流式（M4）
```json
{"type":"llm_delta","scope":"daily_report","text":"昨日营业额 205.99 元，"}
{"type":"llm_done","scope":"daily_report"}
{"type":"llm_suggestion","scope":"self_heal","step":"download","patch":{"selector":"button:has-text('下载数据')"},"message":"页面改版疑似：按钮文本已变化，建议替换选择器"}
```
- `llm_suggestion` 一律**等用户确认**后才由壳回写 stdin 应用（白名单：selector/参数，无自由代码）

## 3. 壳 → 内核（stdin 回写，单行 JSON）

```json
{"type":"human_response","action":"login","result":"done"}        // 用户完成登录
{"type":"human_response","action":"confirm","choice":"强制继续"}
{"type":"apply_patch","step":"download","patch":{"selector":"..."}}
{"type":"abort"}                                                   // 用户点取消
```

## 4. 错误码 → 提醒文案映射（前端）

| error_code | 横幅文案 | 附加动作 |
|---|---|---|
| E_LOGIN_NEEDED | 请在弹出的浏览器中登录后继续 | 置顶浏览器 |
| E_CAPTCHA | 检测到滑块/验证码，请手动完成 | 同上 |
| E_TASK_SLOW | 报表生成较慢（已等待 X 秒），可继续等待 | 显示取消按钮 |
| E_SELECTOR_MISSING | 页面可能已改版，AI 正在分析原因… | 触发 llm_suggestion 流程 |
| E_FILE_LOCKED | 模板被 Excel 占用，请关闭后点重试 | 重试按钮 |
| E_CHECKSUM_FAIL | 数据校验未通过，请核对后选择 | 强制继续/终止 |

## 5. 设计约束

1. 内核不感知 UI：所有用户交互通过 `need_human` ↔ `human_response` 往返，便于 CLI 调试（M1 用终端 echo 模拟壳）
2. 事件不可回放（壳自己留痕）；任务历史由壳写 SQLite
3. 单任务单进程；壳可同时跑多店任务 = 多个子进程（v1 先串行，进程模型预留并发）
