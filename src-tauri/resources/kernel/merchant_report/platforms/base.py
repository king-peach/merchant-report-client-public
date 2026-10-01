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
