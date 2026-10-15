# -*- coding: utf-8 -*-
"""前端「失败原因进汇总 + 可读错误文案 + 诊断输出标签」源码守卫 (2026-10-15, 任务 M3)。

用户实证:
 ①时间段汇总只写 `9/14: ❌❌❌❌（淘宝、美团、管家、京东失败）`——不含原因; 单日汇总 `❌ ${r.err}`
   里 err = error_code || message → 显示 `E_TEMPLATE_LOCKED` 这种机器码。用户要"一眼看到为什么失败"。
 ②ERROR_TEXT 缺 E_TEMPLATE_LOCKED / E_FILL_FAIL → 步骤面板显示裸码。
 ③「内核诊断输出」块跨任务串台(上一任务 stderr 尾巴在下一任务期间才刷出)→ 给块加平台+日期标签。

tsx 无单测框架 → 与 test_frontend_oneclick.py 同款源码断言兜底。
"""
import pathlib


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "src" / "App.tsx").read_text(encoding="utf-8")
    rust = (root / "src-tauri" / "src" / "py_runner.rs").read_text(encoding="utf-8")

    # ---------- A. 失败原因进汇总 ----------
    assert "function shortFail(" in src, "要有 shortFail(把 message 压成短原因, 退回 error_code)"
    assert "function dayFailNotes(" in src, "要有 dayFailNotes(按原因分组去重的当天失败说明)"

    # shortFail 语义: 压空白 + 超 40 字截断 + 退回 error_code
    short_fail = src.split("function shortFail(", 1)[1].split("function dayFailNotes(", 1)[0]
    assert ".replace(/\\s+/g, \" \")" in short_fail, "shortFail 要压缩空白(内核 message 常带换行)"
    assert "length > 40" in short_fail and "slice(0, 40)" in short_fail, "超 40 字要截断(汇总一屏可读)"
    assert "error_code" in short_fail, "没有 message 时退回 error_code"

    # dayFailNotes 语义: 按 r.err 分组(Map 保插入序) + 平台短名 join("/") + 组间 `；`
    notes = src.split("function dayFailNotes(", 1)[1].split("\n}", 1)[0]
    assert "new Map" in notes, "要按原因分组(Map 保持出现顺序)"
    assert "r.err ||" in notes, "分组键是 r.err(error_code/message 已在入账时压成短原因)"
    assert "未明原因" in notes, "err 为空要有兜底文案"
    assert 'join("/")' in notes, "同原因多平台要 join('/') 合并"
    assert 'join("；")' in notes, "多组原因用全角分号连接"

    # allResults push: err 由机器码改为可读短原因
    assert "allResults.current.push({" in src, "结果入账不能消失"
    push = src.split("allResults.current.push({", 1)[1].split("});", 1)[0]
    assert "err: e.ok ? \"\" : shortFail(e)," in push, "入账 err 要改用 shortFail(e)(可读原因)"

    # 即时失败日志行: 出结果就打出原因(一键模式两种都走这条)
    assert "`  ❌ ${PLATFORM_LABEL[platform] ?? platform} 失败: ${shortFail(e)}`" in src, \
        "失败结果要即时打一行 ❌ <平台> 失败: <短原因>"

    # 时间段汇总行: 括注改为原因说明(结构/其它文案不动)
    assert "`（${dayFailNotes(d, allResults.current) || `${failsByDay[i]}失败`}）`" in src, \
        "时间段汇总每天的括注要用 dayFailNotes(原因分组)"

    # ---------- B. 错误文案 ----------
    for key, frag in (("E_TEMPLATE_LOCKED", "模板被其他程序占用"),
                      ("E_FILL_FAIL", "写入失败")):
        assert f"{key}:" in src, f"ERROR_TEXT 要补 {key}(步骤面板不再显示裸码)"
        assert frag in src, f"{key} 的文案要说明人话({frag})"
    assert "E_FILE_LOCKED:" in src, "现有键不许删(保留向后兼容)"

    # ---------- C. 诊断输出标签 + 壳侧字段 ----------
    assert '"platform"' in rust and '"date"' in rust, "任务书 B7: 字段名以字符串形态存在于壳"
    exit_block = rust.split('"type": "task_exit"', 1)[1].split("}),", 1)[0]
    assert '"platform": exit_platform' in exit_block, "task_exit payload 要带 platform"
    assert '"date": exit_date' in exit_block, "task_exit payload 要带 date"
    assert "let exit_platform = platform.clone();" in rust, "平台要在 spawn/线程外先存副本(供 move 闭包)"
    assert "let exit_date = date.clone();" in rust, "日期同上"
    assert "── 内核诊断输出（${who}）──" in src, "诊断块要按 平台·日期 标签化(消除跨任务串台歧义)"
    assert 'PLATFORM_SHORT[e.platform]' in src, "标签要用现有 PLATFORM_SHORT"
    assert '"── 内核诊断输出 ──"' in src, "平台/日期都缺失时要退回原无括号标签"
    print("✅ 失败原因进汇总 + 错误文案 + 诊断输出标签 · 守卫 全绿")


if __name__ == "__main__":
    main()
