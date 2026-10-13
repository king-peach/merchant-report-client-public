# -*- coding: utf-8 -*-
"""门店明细「有数据的平台」探测单测(需求 A1; 合成样例, 不碰用户真实文件)。

前端「一键跑所有平台」的队列由门店明细驱动: 明细里有门店数据的平台才跑/才需要模板。
真实样例表只有 淘宝/美团/管家(没有京东) → 不能再要求 4 个平台全配模板。
内核提供 platforms_in() 与 `python -m merchant_report.store_details --platforms <path>`
(壳 read_store_platforms 调它; 壳里不解析 xlsx)。
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

KERNEL = str(pathlib.Path(__file__).resolve().parents[1] / "kernel")
sys.path.insert(0, KERNEL)
import openpyxl                                                    # noqa: E402
from merchant_report.store_details import PLATFORM_ORDER, platforms_in  # noqa: E402


def build_sample(path):
    """与 test_store_details.build_sample 同构: 只有 淘宝/美团/管家 三组(京东组留空)。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(["淘宝闪购", "", "美团", "", "", "京东无法用门店编号导航，当前数据不需要",
               "美团管家", "", "运营报表模板Sheet名", ""])
    ws.append(["门店名称", "门店唯一编号", "门店名称", "门店唯一编号", "", "", "门店名称", "门店唯一编号", "", ""])
    ws.append(["示例柠檬茶(湘潭岚霞路店)", "4444444444", "示例·柠檬茶专门店（湘潭岚霞路店）", "33333333",
               "", "", "湘潭岳塘岚霞路店", "202000000002", "岚霞路", ""])
    ws.append(["示例柠檬茶(绿地之窗店)", "", "示例?柠檬茶（绿地之窗店）", 44444444,
               "", "", "长沙绿地之窗店", "", "绿地之窗", ""])
    wb.save(path)
    wb.close()


def test_platforms_in():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "门店明细.xlsx")
        build_sample(f)
        got = platforms_in(f)
        assert got == ["taobao", "meituan", "meituan_gj"], got
        print(f"✅ platforms_in: {got}（京东组留空 → 不在队列里）")

        # 相对顺序永远按 PLATFORM_ORDER(淘宝→美团→管家→京东), 不受表内列序影响
        assert got == [p for p in PLATFORM_ORDER if p in set(got)], got
        print("✅ 顺序与 PLATFORM_ORDER 一致(淘宝→美团→管家→京东)")

    # 读不了的文件 → [](调用方回退旧行为), 不抛异常
    assert platforms_in(os.path.join(tempfile.gettempdir(), "no_such_details.xlsx")) == []
    print("✅ 文件不存在 → [](不抛异常)")


def test_cli():
    """壳调用的口径: stdout 一行 JSON 数组; 读失败 → [] + 非零退出码。"""
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "门店明细.xlsx")
        build_sample(f)
        p = subprocess.run([sys.executable, "-m", "merchant_report.store_details", "--platforms", f],
                           cwd=KERNEL, capture_output=True, text=True)
        assert p.returncode == 0, (p.returncode, p.stderr)
        got = json.loads(p.stdout.strip().splitlines()[-1])
        assert got == ["taobao", "meituan", "meituan_gj"], got
        print(f"✅ CLI 正常: stdout={p.stdout.strip()} rc=0")

        p2 = subprocess.run([sys.executable, "-m", "merchant_report.store_details", "--platforms",
                             os.path.join(d, "不存在.xlsx")],
                            cwd=KERNEL, capture_output=True, text=True)
        assert p2.returncode != 0, "读失败必须非零退出(壳据此回退旧行为)"
        assert json.loads(p2.stdout.strip().splitlines()[-1]) == []
        print(f"✅ CLI 读失败: stdout=[] rc={p2.returncode}(壳回退旧行为)")

        p3 = subprocess.run([sys.executable, "-m", "merchant_report.store_details", "--platforms"],
                            cwd=KERNEL, capture_output=True, text=True)
        assert p3.returncode != 0 and json.loads(p3.stdout.strip().splitlines()[-1]) == []
        print("✅ CLI 缺参数: stdout=[] + 非零退出")


def main():
    test_platforms_in()
    test_cli()
    print("✅ 门店明细平台探测(platforms_in / --platforms)全绿")


if __name__ == "__main__":
    main()
