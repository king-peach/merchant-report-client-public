# -*- coding: utf-8 -*-
"""下载暂存目录捡回链 单测 (2026-10-13 客户机"下载完成后浏览器断开"实证)。

链条: download_reports 清场 → launch downloads_path=down_stage/ → 下载完成的文件直落暂存 →
      save_as 撞上"浏览器断开"时 _pickup_staged 纯文件系统捡回;
      等待循环里 _sweep_staged 实时搬运(不碰浏览器/连接)。

本测试钉:
  ① _sweep_staged: 稳定的完整文件搬进目标目录; 太小/刚写(<min_age)不放行;
  ② _pickup_staged: 按名字找到并搬入; 名字不符不误搬、超时返回空;
  ③ 接线标记: 清场 / launch 暂存 / 实时搬运 / save 失败捡回 全在位。
"""
import inspect
import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import taobao as T     # noqa: E402


class _Sink:
    def __init__(self):
        self.lines = []

    def log(self, msg, level="info"):
        self.lines.append(f"[{level}] {msg}")


def _adapter(stage):
    a = T.TaobaoAdapter.__new__(T.TaobaoAdapter)
    a.sink = _Sink()
    old = T.DOWNLOAD_STAGE
    T.DOWNLOAD_STAGE = stage
    return a, old


def test_sweep_moves_stable():
    with tempfile.TemporaryDirectory() as td:
        stage = os.path.join(td, "stage")
        ddir = os.path.join(td, "out")
        os.makedirs(stage)
        os.makedirs(ddir)
        p = os.path.join(stage, "门店下载_20260925至20260925_全部.xlsx")
        with open(p, "wb") as f:
            f.write(b"x" * 4096)
        old_mt = time.time() - 60
        os.utime(p, (old_mt, old_mt))          # mtime 冷却通过
        a, old_stage = _adapter(stage)
        try:
            moved = T.TaobaoAdapter._sweep_staged(a, ddir)
            assert moved == ["门店下载_20260925至20260925_全部.xlsx"], moved
            assert os.path.isfile(os.path.join(ddir, moved[0]))
            assert not os.path.exists(p), "搬走 = 移动而非复制残留"
        finally:
            T.DOWNLOAD_STAGE = old_stage
    print("✅ sweep: 完整文件被搬进目标目录")


def test_sweep_guards():
    with tempfile.TemporaryDirectory() as td:
        stage = os.path.join(td, "stage")
        ddir = os.path.join(td, "out")
        os.makedirs(stage)
        os.makedirs(ddir)
        small = os.path.join(stage, "tiny.xlsx")
        with open(small, "wb") as f:
            f.write(b"x" * 100)                # <1KB → 不放行
        fresh = os.path.join(stage, "fresh.xlsx")
        with open(fresh, "wb") as f:
            f.write(b"x" * 4096)               # 刚写(mtime=now) → 冷却闸不放行(min_age=8)
        a, old_stage = _adapter(stage)
        try:
            moved = T.TaobaoAdapter._sweep_staged(a, ddir)
            assert moved == [], moved
            assert os.path.isfile(small) and os.path.isfile(fresh), "护栏期内不许动文件"
        finally:
            T.DOWNLOAD_STAGE = old_stage
    print("✅ sweep 护栏: 太小/刚写完不放行")


def test_pickup_by_name():
    with tempfile.TemporaryDirectory() as td:
        stage = os.path.join(td, "stage")
        ddir = os.path.join(td, "out")
        os.makedirs(stage)
        os.makedirs(ddir)
        want = "门店下载_20260925至20260925_全部_20261013124437.xlsx"
        other = "别的文件.xlsx"
        for nm in (other, want):
            with open(os.path.join(stage, nm), "wb") as f:
                f.write(b"x" * 8192)
        a, old_stage = _adapter(stage)
        try:
            t0 = time.time()
            got = T.TaobaoAdapter._pickup_staged(a, ddir, want, timeout_s=8)
            dt = time.time() - t0
            assert got == want, got
            assert os.path.isfile(os.path.join(ddir, want)), "必须搬进目标目录"
            assert os.path.isfile(os.path.join(stage, other)), "名字不符的不该被搬"
            assert dt < 7, f"捡回耗时 {dt:.1f}s 过长"
        finally:
            T.DOWNLOAD_STAGE = old_stage
    print("✅ pickup: 按名捡回 ✓ 不误搬 ✓")


def test_pickup_no_match_timeout():
    with tempfile.TemporaryDirectory() as td:
        stage = os.path.join(td, "stage")
        ddir = os.path.join(td, "out")
        os.makedirs(stage)
        os.makedirs(ddir)
        with open(os.path.join(stage, "根本没关系的.xlsx"), "wb") as f:
            f.write(b"x" * 4096)
        a, old_stage = _adapter(stage)
        try:
            got = T.TaobaoAdapter._pickup_staged(a, ddir, "门店下载_不存在.xlsx", timeout_s=3)
            assert got == "", got
        finally:
            T.DOWNLOAD_STAGE = old_stage
    print("✅ pickup: 名字完全不符 → 不搬(超时返回空)")


def test_wiring():
    full = open(pathlib.Path(T.__file__), encoding="utf-8").read()
    assert "downloads_path" in full and "down_stage" in full
    dr = inspect.getsource(T.TaobaoAdapter.download_reports)
    assert "暂存目录清场" in dr, "跑前清场(防捡旧文件)"
    assert "_sweep_staged(ddir" in inspect.getsource(T.TaobaoAdapter._wait_files), "等待循环实时搬运"
    assert "已从下载暂存目录捡回" in inspect.getsource(T.TaobaoAdapter._save_download), "save失败捡回"
    print("✅ 接线: 清场/launch暂存/实时搬运/save失败捡回 全在位")


def main():
    test_sweep_moves_stable()
    test_sweep_guards()
    test_pickup_by_name()
    test_pickup_no_match_timeout()
    test_wiring()
    print("\n✅ 下载暂存目录捡回链 全绿")


if __name__ == "__main__":
    main()
