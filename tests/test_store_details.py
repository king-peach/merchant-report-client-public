# -*- coding: utf-8 -*-
"""门店明细表读取器单测(合成样例, 不碰用户真实文件)。"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
import openpyxl  # noqa: E402
from merchant_report.store_details import load_store_details  # noqa: E402


def build_sample(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(["淘宝闪购", "", "美团", "", "", "京东无法用门店编号导航到相关门店，不过现在的数据，不需要单个",
               "美团管家", "", "运营报表模板Sheet名", ""])
    ws.append(["门店名称", "门店唯一编号", "门店名称", "门店唯一编号", "", "", "门店名称", "门店唯一编号", "", ""])
    ws.append(["示例柠檬茶(湘潭岚霞路店)", "4444444444", "示例柠檬茶专门店（湘潭岚霞路店）", "33333333",
               "", "", "湘潭岳塘岚霞路店", "202000000002", "岚霞路", ""])
    ws.append(["示例柠檬茶(绿地之窗店)", "", "示例柠檬茶（绿地之窗店）", 44444444,
               "", "", "长沙绿地之窗店", "", "绿地之窗", ""])
    ws2 = wb.create_sheet("meta")
    ws2.append(["文件名", "门店下载_xx"])
    wb.save(path)
    wb.close()


def main():
    tmp = os.path.join(tempfile.gettempdir(), "test_store_details_sample.xlsx")
    build_sample(tmp)
    d = load_store_details(tmp)
    assert set(d.keys()) == {"taobao", "meituan", "meituan_gj"}, d.keys()

    tb = d["taobao"]
    assert tb["by_id"].get("4444444444") == "岚霞路", tb
    assert tb["by_name"].get("示例柠檬茶湘潭岚霞路店") == "岚霞路", tb  # _tidy: 去括号/符号

    mt = d["meituan"]
    assert mt["by_id"].get("33333333") == "岚霞路", mt
    assert mt["by_id"].get("44444444") == "绿地之窗", mt          # 数字型 id(float 防护)
    assert mt["by_name"].get("示例柠檬茶专门店湘潭岚霞路店") == "岚霞路", mt

    gj = d["meituan_gj"]
    assert gj["by_id"].get("202000000002") == "岚霞路", gj
    assert gj["by_name"].get("湘潭岳塘岚霞路店") == "岚霞路", gj
    assert "202000000002" not in d.get("jd", {}).get("by_id", {}), "京东组应留空"

    # 读不了的文件 → {}
    assert load_store_details(os.path.join(tempfile.gettempdir(), "no_such.xlsx")) == {}
    test_plan_matches()
    print("✅ 门店明细读取器 全绿")
    print("   taobao ids:", tb["by_id"])
    print("   meituan ids:", mt["by_id"])
def test_plan_matches():
    """plan_matches: 编号优先锁定 → 名称映射 → 关键词兜底 的优先级链。"""
    from merchant_report.sheet_match import plan_matches
    sheets = ["Sheet1", "岚霞路", "绿地之窗", "五里牌"]
    names = ["示例柠檬茶专门店（湘潭岚霞路店）", "示例柠檬茶（绿地之窗店）"]
    id_map = {"33333333": "岚霞路", "44444444": "绿地之窗"}
    store_ids = {names[0]: "33333333", names[1]: "44444444"}
    p = plan_matches(sheets, names, store_map={}, id_map=id_map, store_ids=store_ids)
    m = {n: (s, w) for n, s, w in p["matched"]}
    assert m[names[0]][0] == "岚霞路" and "编号锁定" in m[names[0]][1], m
    assert m[names[1]][0] == "绿地之窗", m
    # 编号指向模板里没有的 Sheet → 不落匹配, 原因讲清楚
    id_map2 = {"33333333": "不存在的Sheet"}
    p2 = plan_matches(sheets, ["某店"], store_map={}, id_map=id_map2, store_ids={"某店": "33333333"})
    assert not p2["matched"] and "没这张 Sheet" in p2["unmatched"][0][1], p2
    # 未配编号 → 名称映射照常工作
    p3 = plan_matches(sheets, ["示例（五里牌店）"], store_map={"示例五里牌店": "五里牌"})
    assert p3["matched"][0][1] == "五里牌", p3
    print("✅ plan_matches: 编号优先/失效说明/名称兜底 全绿")


if __name__ == "__main__":
    main()
