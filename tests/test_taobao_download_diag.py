# -*- coding: utf-8 -*-
"""淘宝下载路径 vs 美团(可跑通路径) 对齐守卫单测 (2026-10-12 客户机)。

现场: 同台客户机, 美团外卖可跑通, 淘宝闪购"下载一开始浏览器就断连"(日志:
"浏览器连接已断开" 紧跟 "已提交任务")。代码对比结论:
  - 美团: ①接口直取优先(ctx.request 纯 HTTP) ②兜底下载隔离在一次性页; **从不**发
    Browser.setDownloadBehavior。
  - 淘宝(旧): 主 context 挂下载事件 + 额外发 Browser.setDownloadBehavior(allow+downloadPath)
    —— 四个平台里唯一这么做的; 且在客户机上该调用"被接受"(与开发机"被拒绝"不同)。
本测试锁:
  ① download_reports 不再出现 setDownloadBehavior / new_cdp_session(与美团对齐);
  ② 保留 download 事件接管;
  ③ 新增"下载 URL 嗅探"(为把淘宝改成美团式直取收集 URL 结构)。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import taobao as T     # noqa: E402


def main():
    src = inspect.getsource(T.TaobaoAdapter.download_reports)
    assert 'cdp.send("Browser.setDownloadBehavior"' not in src, "CDP 下载设置必须移除(与美团对齐)"
    assert "new_cdp_session" not in src, "别再建 CDP 会话"
    assert "已挂载下载接管" in src, "download 事件接管必须保留"
    assert "疑似下载请求" in src and '_req_hook' in src, "要有下载 URL 嗅探(记录仪)"
    assert "已挂载下载 URL 嗅探" in src
    # 其他位置也不许悄悄调用(注释提及无所谓, 只卡"真调用"形态)
    full = open(pathlib.Path(T.__file__), encoding="utf-8").read()
    assert 'cdp.send("Browser.setDownloadBehavior"' not in full, "不许真调用 CDP 下载设置"
    assert "new_cdp_session" not in full, "全文件不许再出现 new_cdp_session"
    print("✅ 淘宝下载路径: CDP 移除 ✓ 事件接管保留 ✓ URL 嗅探在位 ✓")
    print("\n✅ 淘宝-美团下载路径对齐守卫 全绿")


if __name__ == "__main__":
    main()
