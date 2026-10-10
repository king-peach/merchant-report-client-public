// py_runner: spawn Python 任务内核子进程, 逐行消费 NDJSON 事件流 → emit 给前端。
// 协议见 docs/任务协议-NDJSON.md
use serde::Serialize;
use std::io::{BufRead, BufReader, Write};
use std::process::{ChildStdin, Command, Stdio};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_notification::NotificationExt;

#[derive(Serialize, Clone)]
pub struct TaskEvent {
    #[serde(flatten)]
    payload: serde_json::Value,
}

pub struct TaskState {
    // 同一时刻一个任务进程(v1 串行; 进程模型预留并发)
    pub child_stdin: Mutex<Option<ChildStdin>>,
    pub child_pid: Mutex<Option<u32>>,
}

/// 定位内核 python(四级回退): 打包资源 resources/python → PATH → 固定路径探测
/// 注意: Tauri bundle.resources 会保留来源目录名 → 实际布局是 <resource_dir>/resources/python,
/// 旧布局/裸资源是 <resource_dir>/python —— 两种都探(内核目录同款双布局)。
fn resolve_python(app: &AppHandle) -> Result<std::path::PathBuf, String> {
    if let Ok(res) = app.path().resource_dir() {
        let exe = if cfg!(windows) {
            "python.exe"
        } else {
            "bin/python3"
        };
        for cand in [
            res.join("resources").join("python").join(exe),
            res.join("python").join(exe),
        ] {
            if cand.exists() {
                return Ok(cand);
            }
        }
    }
    for name in ["python3", "python"] {
        if let Some(p) = which(name) {
            return Ok(p);
        }
    }
    for fixed in [
        "/usr/bin/python3",
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
    ] {
        let p = std::path::PathBuf::from(fixed);
        if p.exists() {
            return Ok(p);
        }
    }
    Err("未找到可用的 Python (已探测 resources/python、PATH、/usr/bin/python3)".into())
}

fn which(name: &str) -> Option<std::path::PathBuf> {
    let path_env = std::env::var("PATH").ok()?;
    for dir in std::env::split_paths(&path_env) {
        let p = dir.join(name);
        if p.is_file() {
            return Some(p);
        }
    }
    None
}

/// 启动日报任务(商家/平台/模板由前端传入; 模板存在性双保险: Rust 先查, 内核再查)
#[tauri::command]
pub fn run_task(
    app: AppHandle,
    state: tauri::State<TaskState>,
    merchant: String,
    platform: String,
    date: String,
    template: String,
    keyword: String,
    store_id: String,
    scope: String,
    download_dir: String,
    date_from: String,
    username: String,
    password: String,
    store_details: String,
    skip_browser: bool,
) -> Result<String, String> {
    // 模板兜底校验(前端已缓存路径可能失效)
    if !std::path::Path::new(&template).is_file() {
        return Err(format!(
            "模板文件不存在: {template} (可能被移动或改名，请到设置重新选择)"
        ));
    }
    // 门店明细(可选): 配置了就必须存在 —— 否则宁可不跑也不静默降级到模糊匹配
    if !store_details.is_empty() && !std::path::Path::new(&store_details).is_file() {
        return Err(format!(
            "门店明细文件不存在: {store_details} (可能被移动或改名，请到设置重新选择或清除)"
        ));
    }
    let python = resolve_python(&app)?;
    // kernel 目录: 打包资源 → dev 模式从当前目录向上找 kernel/merchant_report
    let kernel_dir = {
        let res = app.path().resource_dir().map_err(|e| e.to_string())?;
        // Tauri bundle 布局差异: resources 数组保留来源目录名 → Resources/resources/kernel;
        // 旧布局/裸资源直放 Resources/kernel。两种都试。
        let candidates = [res.join("kernel"), res.join("resources").join("kernel")];
        let packaged = candidates
            .iter()
            .find(|p| p.join("merchant_report").exists())
            .cloned();
        packaged.unwrap_or_else(|| {
            let mut dir = std::env::current_dir().unwrap_or_default();
            let mut found = None;
            for _ in 0..5 {
                let cand = dir.join("kernel");
                if cand.join("merchant_report").exists() {
                    found = Some(cand);
                    break;
                }
                if !dir.pop() {
                    break;
                }
            }
            found
                .ok_or(
                    "找不到 kernel 目录(dev 模式需从项目根启动; 打包模式下应在 App 资源内)"
                        .to_string(),
                )
                .unwrap_or_default()
        })
    };
    if !kernel_dir.join("merchant_report").exists() {
        return Err(format!(
            "kernel 目录无效: {} (打包资源缺失或 dev 目录不对)",
            kernel_dir.display()
        ));
    }

    let mut cmd = Command::new(&python);
    cmd.current_dir(&kernel_dir)
        .arg("-u") // 无缓冲: 事件实时到达前端
        .arg("-m")
        .arg("merchant_report.task_main")
        .arg("--merchant")
        .arg(&merchant)
        .arg("--platform")
        .arg(&platform)
        .arg("--template")
        .arg(&template)
        .arg("--keyword")
        .arg(&keyword)
        .arg("--store-id")
        .arg(&store_id)
        .arg("--scope")
        .arg(&scope)
        .arg("--download-dir")
        .arg(&download_dir)
        .arg("--date")
        .arg(&date)
        .arg("--date-from")
        .arg(&date_from)
        .arg("--username")
        .arg(&username)
        .arg("--password")
        .arg(&password)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if !store_details.is_empty() {
        cmd.arg("--store-details").arg(&store_details);
    }
    if skip_browser {
        cmd.arg("--no-browser");
    }
    // Windows 安装包内自带 Chromium(与 macOS 同引擎, 行为一致)。
    // 存在则把它指给 Playwright: 客户机不需要系统 Edge/Chrome, 也不下载浏览器。
    if let Ok(res) = app.path().resource_dir() {
        for cand in [
            res.join("resources").join("ms-playwright"),
            res.join("ms-playwright"),
        ] {
            if cand.exists() {
                cmd.env("PLAYWRIGHT_BROWSERS_PATH", cand);
                break;
            }
        }
    }
    // Windows 上内核 stdout 默认走 ANSI 代码页(cp936): 事件流是 UTF-8 JSON, 编码不一致会让
    // 壳侧解码失败(旧实现直接 break → 事件流中断 + 管道读端关闭 → 内核写入报 OSError EINVAL)。
    // 三保险: 内核自己 reconfigure(见 task_main), 壳再显式给 UTF-8 环境变量。
    cmd.env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .env("PYTHONUNBUFFERED", "1");
    #[cfg(windows)]
    {
        // 隐藏 python 子进程控制台窗口
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let mut child = cmd.spawn().map_err(|e| format!("启动内核失败: {e}"))?;
    state.child_pid.lock().unwrap().replace(child.id());

    let stdout = child.stdout.take().ok_or("无法捕获内核输出")?;
    let stderr = child.stderr.take().ok_or("无法捕获内核错误输出")?;
    let stdin = child.stdin.take().ok_or("无法获取内核 stdin")?;
    *state.child_stdin.lock().unwrap() = Some(stdin);

    // 线程A: 逐行读 NDJSON → emit
    let app2 = app.clone();
    let pid = child.id();
    let app3 = app.clone();
    std::thread::spawn(move || {
        // stderr 收集线程(诊断信息, 任务退出时一并回传前端)
        let stderr_handle = std::thread::spawn(move || {
            let mut collected: Vec<String> = Vec::new();
            let r = BufReader::new(stderr);
            for line in r.lines().flatten() {
                if !line.trim().is_empty() {
                    collected.push(line);
                    if collected.len() > 200 {
                        collected.remove(0);
                    }
                }
            }
            collected
        });

        let reader = BufReader::new(stdout);
        // 按字节读 + lossy 解码, 而不是 lines(): lines() 遇到非 UTF-8 会返回 Err, 旧实现
        // 直接 break → 读取线程退出、管道读端关闭 → 内核后续写入报 OSError(EINVAL),
        // 真实错误被顶掉(Windows cp936 输出即触发)。这里解码失败也继续读, 并明确告警。
        for raw in reader.split(b'\n') {
            let bytes = match raw {
                Ok(b) => b,
                Err(_) => break, // 真正的 I/O 错误(进程已退出)
            };
            let l = String::from_utf8_lossy(&bytes)
                .trim_end_matches('\r')
                .to_string();
            if l.trim().is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&l) {
                let _ = app2.emit("task-event", TaskEvent { payload: v.clone() });
                if v.get("type").and_then(|t| t.as_str()) == Some("need_human") {
                    let msg = v
                        .get("message")
                        .and_then(|m| m.as_str())
                        .unwrap_or("需要人工操作");
                    let _ = app2
                        .notification()
                        .builder()
                        .title("商家报表助手")
                        .body(msg)
                        .show();
                }
            } else if l.contains('\u{FFFD}') {
                // 明显的编码错乱(非 UTF-8 字节): 明确告知而不是静默丢弃
                let _ = app2.emit(
                    "task-event",
                    TaskEvent {
                        payload: serde_json::json!({
                            "type": "log",
                            "level": "warn",
                            "msg": "内核输出存在编码异常(疑似非 UTF-8), 该行已丢弃"
                        }),
                    },
                );
            } else {
                // 非 JSON 行也透传到日志区(内核 print 泄漏时不丢信息)
                let _ = app2.emit(
                    "task-event",
                    TaskEvent {
                        payload: serde_json::json!({"type": "log", "level": "info", "msg": l}),
                    },
                );
            }
        }
        // 内核退出: 带 exit 状态与 stderr 尾巴
        let exit_status = std::process::Command::new("sh")
            .arg("-c")
            .arg(format!(
                "ps -p {pid} > /dev/null 2>&1 && echo running || echo done"
            ))
            .output()
            .ok()
            .and_then(|o| {
                String::from_utf8(o.stdout)
                    .ok()
                    .map(|s| s.trim().to_string())
            })
            .unwrap_or_default();
        let _ = exit_status;
        let stderr_tail = stderr_handle.join().unwrap_or_default();
        let _ = app3.emit(
            "task-event",
            TaskEvent {
                payload: serde_json::json!({
                    "type": "task_exit",
                    "stderr_tail": stderr_tail.iter().rev().take(8).rev().collect::<Vec<_>>(),
                }),
            },
        );
    });

    Ok(format!("task started pid={pid}"))
}

/// 人工响应/补丁/中止 → 内核 stdin
#[tauri::command]
pub fn send_to_kernel(
    state: tauri::State<TaskState>,
    payload: serde_json::Value,
) -> Result<(), String> {
    let mut guard = state.child_stdin.lock().unwrap();
    if let Some(stdin) = guard.as_mut() {
        let line = serde_json::to_string(&payload).map_err(|e| e.to_string())?;
        stdin
            .write_all(line.as_bytes())
            .map_err(|e| e.to_string())?;
        stdin.write_all(b"\n").map_err(|e| e.to_string())?;
        stdin.flush().map_err(|e| e.to_string())?;
        Ok(())
    } else {
        Err("没有运行中的任务".to_string())
    }
}

/// 文件存在性校验(模板路径兜底)
#[tauri::command]
pub fn check_file(path: String) -> serde_json::Value {
    let p = std::path::PathBuf::from(&path);
    serde_json::json!({ "exists": p.is_file(), "size": p.metadata().map(|m| m.len()).unwrap_or(0) })
}

/// 中止当前任务
#[tauri::command]
pub fn abort_task(state: tauri::State<TaskState>) -> Result<(), String> {
    {
        let mut guard = state.child_stdin.lock().unwrap();
        if let Some(stdin) = guard.as_mut() {
            let _ = stdin.write_all(b"{\"type\":\"abort\"}\n");
            let _ = stdin.flush();
        }
        *guard = None;
    }
    if let Some(pid) = state.child_pid.lock().unwrap().take() {
        kill_pid(pid);
    }
    Ok(())
}

fn kill_pid(pid: u32) {
    #[cfg(windows)]
    {
        // creation_flags 来自此 trait(Windows 独占); CREATE_NO_WINDOW => 不弹黑窗
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(0x08000000)
            .output();
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("kill").arg(pid.to_string()).output();
    }
}
