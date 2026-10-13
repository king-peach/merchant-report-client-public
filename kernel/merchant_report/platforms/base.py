# -*- coding: utf-8 -*-
"""平台适配器接口: 新平台实现四方法即接入(美团/京东待账号实测)。"""


class PlatformAdapter:
    platform = "base"

    def ensure_login(self, timeout_s=300) -> bool:
        raise NotImplementedError

    def download_reports(self, shop_id, ddir, timeout_s=300) -> list:
        """下载该平台当日报表文件到 ddir, 返回文件名列表"""
        raise NotImplementedError

    def capture_stats(self) -> dict:
        """网页抓取流量/推广数据, 返回平台自定义 dict"""
        raise NotImplementedError

    def build_write_set(self, date, reports, stats) -> dict:
        """报表+抓取数据 → {模板列: 值} (平台映射表在此实现)"""
        raise NotImplementedError


# 切店路径名(打点用)。含义:
#   搜索ID       = 淘宝: 弹层搜索框输 shop_id 直选(快路径成功)
#   搜索ID回退   = 淘宝: 配了编号但快路径没成(无搜索框/无结果/验收不过) → 回退按名
#   搜索编码     = 管家: 弹窗搜索框输 12 位机构编码直选(快路径成功)
#   搜索编码回退 = 管家: 配了编码但快路径没成 → 回退按店名
#   展开按名     = 淘宝: 未配编号, 展开全部运营组织 + 按店名找(旧路径)
#   搜索按名     = 管家: 未配编码, 搜索框按店名搜(旧路径)
SWITCH_PATHS = ("搜索ID", "搜索ID回退", "搜索编码", "搜索编码回退", "展开按名", "搜索按名")


class ShopSwitchTimer:
    """切店耗时打点: 记每次 (店名, 秒, 路径) → 收尾打一条汇总。

    为什么要它(任务需求「切店耗时打点量化」): 「输编号搜索直选」比「展开全部节点+按名找」
    究竟快多少, 不能靠感觉 —— 每次切店记一行、运行收尾汇总一行, 速度对比就有据可依;
    路径分布还能暴露**回退率**(快路径经常不成立时, 说明 UI 假设要修)。

    路径名见 SWITCH_PATHS。计时口径 = 从进入切店函数到返回(含回退全流程)。
    """

    def __init__(self, label=""):
        self.label = label
        self.records = []          # [(店名, 秒, 路径), ...]
        self.path_counts = {}
        self.summary_logged = False

    def log_line(self, sink, name, seconds, path):
        """记一次并打一行: `⏱ 切店 <店名> <秒数>s (<路径>)`。"""
        try:
            self.records.append((name, float(seconds), path))
            self.path_counts[path] = self.path_counts.get(path, 0) + 1
            sink.log(f"⏱ 切店 {name} {float(seconds):.1f}s ({path})")
        except Exception:
            pass

    def summary(self, sink):
        """收尾汇总(幂等, 只打一次):
        `⏱ 切店统计: N 次 / 平均 X.Xs / 总计 Ys / 路径分布(搜索ID a 次, 展开按名 b 次)`。"""
        if self.summary_logged or not self.records:
            self.summary_logged = True
            return
        self.summary_logged = True
        n = len(self.records)
        total = sum(r[1] for r in self.records)
        dist = "，".join(f"{p} {self.path_counts[p]} 次" for p in SWITCH_PATHS
                         if self.path_counts.get(p))
        sink.log(f"⏱ 切店统计: {n} 次 / 平均 {total / n:.1f}s / 总计 {total:.1f}s / 路径分布({dist})")
