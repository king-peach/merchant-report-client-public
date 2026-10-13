#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod py_runner;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

// 退出清理只做一次(防止 prevent_exit → cleanup → exit(0) 再触发的循环)
static EXIT_CLEANED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn main() {
    tauri::Builder::default()
        // 单实例锁(2026-10-11 双开互杀根治): 重复打开时不再开第二份窗口,
        // 直接唤起已有主窗口。插件按注册顺序执行, 此插件必须最先注册。
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_http::init())
        .manage(py_runner::TaskState {
            child_stdin: std::sync::Mutex::new(None),
            child_pid: std::sync::Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            py_runner::run_task,
            py_runner::read_store_platforms,
            py_runner::send_to_kernel,
            py_runner::abort_task,
            py_runner::check_file
        ])
        .setup(|app| {
            // 系统托盘
            let show = MenuItem::with_id(app, "show", "打开主界面", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::with_id("main-tray")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            // 退出前清理(2026-10-13 客户机"残留链"根因修复):
            // 关窗/托盘退出 → 先把内核与自动化浏览器送走, 再放行退出。
            // 否则 Windows 上子进程不随父进程退出, 任务跑着时关助手必留孤儿进程(残留),
            // 下次启动带着脏状态跑就会出各种怪问题(客户机事故链路)。
            if let tauri::RunEvent::ExitRequested { api, .. } = event {
                if !EXIT_CLEANED.swap(true, std::sync::atomic::Ordering::SeqCst) {
                    api.prevent_exit();
                    let h = app.clone();
                    std::thread::spawn(move || {
                        // 清理过程吞掉一切异常: 再怎么样也要把应用退掉,
                        // 绝不因为清理自身出错把用户卡在"窗口关不掉"的状态。
                        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            py_runner::cleanup_on_exit(&h);
                        }));
                        h.exit(0);
                    });
                }
            }
        });
}
