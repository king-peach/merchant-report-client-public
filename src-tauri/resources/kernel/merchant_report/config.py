# -*- coding: utf-8 -*-
"""配置加载: 客户端数据目录下的 config.yaml（壳负责写, 内核只读）。
数据目录: Windows %APPDATA%/MerchantReportClient, macOS ~/Library/Application Support/MerchantReportClient
"""
import os
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def app_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        p = Path(base) / "MerchantReportClient"
    else:
        p = Path.home() / "Library" / "Application Support" / "MerchantReportClient"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_config(config_path=None) -> dict:
    path = Path(config_path) if config_path else app_data_dir() / "config.yaml"
    if not path.exists():
        return _defaults()
    if yaml is None:
        raise SystemExit("!! 需要 pyyaml")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return {**_defaults(), **cfg}


def _defaults() -> dict:
    return {
        "defaults": {"date": "", "fill_mode": "auto", "download_dir": str(app_data_dir() / "reports"),
                     # 模板瘦身(M5): 保存前剪掉"无值但带样式"的越界空格(WPS 整行套格式 →
                     # 输出 21.6MB)。默认开; 置 false 或设环境变量 MRC_SLIM_XLSX=0 可关。
                     "slim_xlsx": True},
        "stores": [],
        "llm": {"base_url": "", "api_key": "", "model": "", "enabled": True},
        "notify": {"windows_toast": True, "webhook": ""},
        "platforms": {
            "taobao": {"enabled": True, "home": "https://melody.shop.ele.me/"},
            "meituan": {"enabled": False, "home": "https://waimai.meituan.com/"},
            "jd": {"enabled": False, "home": "https://store.jddj.com/"},
        },
    }
