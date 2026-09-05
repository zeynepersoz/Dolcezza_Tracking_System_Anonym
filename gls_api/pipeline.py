# -*- coding: utf-8 -*-
"""
Uçtan uca pipeline: Excel -> GLS API -> PDF -> Sevkiyat/Ülke/Fatura klasörleri -> ZIP
Selenium sürümüyle AYNI klasör şemasını üretir; sadece indirme katmanı API'dir.
"""
import re
import shutil
from pathlib import Path

import pandas as pd

import timez
from . import config
from .shipit_client import ShipITClient, ShipITError

REQUIRED_COLUMNS = ["PARCEL NUMBER", "SHIPMENT AGENCY", "STORE CODE",
                    "COUNTRY", "INVOICE NUMBER", "SHIPMENT METHOD"]


def clean_cell(value) -> str:
    text = str(value).strip()
    if text.lower() in ("nan", "none"):
        return ""
    if re.match(r"^\d+\.0$", text):
        text = text[:-2]
    return re.sub(r'[\\/*?:"<>|]', "", text)


# Dosya adina girecek her parca bundan geciyor: ad hem HTTP header'ina hem
# DISKE yaziliyor, bu yuzden "/", "\" ve ".." gibi yol karakterleri elenmeli.
_UNSAFE_NAME_RE = re.compile(r"[^\w.\-]", re.UNICODE)


def safe_name_part(value: object, limit: int) -> str:
    """Serbest metni dosya adinda guvenle kullanilacak bir parcaya cevirir."""
    text = clean_cell(value or "")
    if not text or text.lower() == "nan":
        return ""
    return _UNSAFE_NAME_RE.sub("-", text).strip("-.")[:limit]


def normalize_country(name: str) -> str:
    name = clean_cell(name)
    return " ".join(w.capitalize() for w in name.split()) if name else "Unknown"


def ordinal_box_name(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} Box"


def load_rows(excel_path: str, sheet_name: str, only_delivered: bool = True,
              method_filter: str = None, limit: int = 0) -> pd.DataFrame:
    df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=str)
    df.columns = [c.strip().upper() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Eksik sütun(lar): {', '.join(missing)}")
    df = df.fillna("")
    df = df[df["SHIPMENT AGENCY"].str.strip().str.upper() == "GLS"]
    if only_delivered and "EXPLAIN" in df.columns:
        df = df[df["EXPLAIN"].str.strip().str.upper() == "DELIVERED"]
    if method_filter and method_filter != "Tümü":
        df = df[df["SHIPMENT METHOD"].str.strip() == method_filter]
    df = df[df["PARCEL NUMBER"].str.strip() != ""]
    if limit:
        df = df.head(limit)
    return df.reset_index(drop=True)


def run_pipeline(excel_path: str, sheet_name: str, output_dir: Path = None,
                 only_delivered: bool = True, method_filter: str = None,
                 limit: int = 0, log=print) -> dict:
    output_dir = Path(output_dir or config.OUTPUT_DIR)
    client = ShipITClient()
    df = load_rows(excel_path, sheet_name, only_delivered, method_filter, limit)
    log(f"✅ {len(df)} satır işlenecek | Mod: {config.MODE} | Hedef: {output_dir}")

    success, failed = 0, 0
    failed_list = []

    for idx, row in df.iterrows():
        parcel = clean_cell(row["PARCEL NUMBER"])
        method = clean_cell(row["SHIPMENT METHOD"]) or "Genel Sevkiyatlar"
        country = normalize_country(row["COUNTRY"])
        store = clean_cell(row["STORE CODE"])
        invoice = clean_cell(row["INVOICE NUMBER"])

        target_dir = output_dir / method / country / f"{store}-{invoice}"
        target_dir.mkdir(parents=True, exist_ok=True)
        box_num = len(list(target_dir.glob("*.pdf"))) + 1
        target = target_dir / f"{store}-{invoice} {ordinal_box_name(box_num)}.pdf"

        try:
            client.save_pod(parcel, target)
            log(f"[{idx+1}/{len(df)}] ✅ {parcel} -> {target.relative_to(output_dir)}")
            success += 1
        except ShipITError as e:
            log(f"[{idx+1}/{len(df)}] ❌ {parcel}: {e}")
            failed += 1
            failed_list.append(parcel)

    if failed_list:
        report = output_dir / "HATALI_PARCALAR.txt"
        with open(report, "a", encoding="utf-8") as f:
            f.write(f"\n--- {timez.now():%Y-%m-%d %H:%M} ---\n")
            f.write("\n".join(failed_list) + "\n")

    return {"success": success, "failed": failed, "total": len(df),
            "output_dir": str(output_dir)}


def make_zip(output_dir: Path = None) -> str:
    output_dir = Path(output_dir or config.OUTPUT_DIR)
    stamp = timez.now().strftime("%Y%m%d_%H%M")
    zip_base = output_dir.parent / f"{output_dir.name}_TeslimatKanitlari_{stamp}"
    return shutil.make_archive(str(zip_base), "zip", root_dir=str(output_dir))
