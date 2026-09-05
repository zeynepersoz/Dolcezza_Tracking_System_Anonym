#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Komut satırı arayüzü — Ubuntu sunucuda cron ile de çalıştırılabilir.

Örnekler:
    # Mock sunucuyla uçtan uca test (önce: uvicorn gls_api.mock_server:app --port 8788)
    python3 cli.py demo

    # Gerçek Excel ile (mock/test/prod .env'deki GLS_MODE'a göre)
    python3 cli.py run --excel "SP26 TRACKING LIST.xlsx" --sheet "TESLİMAT KANITI ALINACAKLAR" --limit 3
    python3 cli.py run --excel "..." --sheet "..." --zip
"""
import argparse
import sys

from gls_api import config, providers
from gls_api.pipeline import run_pipeline, make_zip
from gls_api.shipit_client import ShipITClient


def cmd_demo(args):
    """Mock sunucudaki bilinen parçalarla ShipIT istemcisini dener."""
    print(f"Mod: {config.MODE} | ShipIT: {config.SHIPIT_BASE_URL} | NL: {config.NL_BASE_URL}\n")

    shipit = ShipITClient()
    print("— ShipIT parceldetails —")
    print(shipit.parcel_details("21569761233"))
    print("\n— ShipIT parcelpod —")
    pdf = shipit.parcel_pod("21569761233")
    out = config.OUTPUT_DIR / "demo_pod.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pdf)
    print(f"POD PDF kaydedildi: {out} ({len(pdf)} bayt)")



def seed_admin(db, username: str, password: str, force: bool = False) -> str:
    """Admin kullanicisi olusturur/sifreler; sonuc mesajini doner.

    web.main zaten acilista bossa otomatik bir admin olusturuyor (rastgele
    sifreyle, loglara yaziliyor) — bu fonksiyon Docker/CI gibi ortamlarda log
    kazmadan bilinen bir kullanici adi/sifreyle deterministik kurulum ister."""
    from auth.security import hash_password

    if not password:
        raise ValueError("Sifre bos olamaz")

    existing = db.get_user_by_username(username)
    if existing and not force:
        raise FileExistsError(f"'{username}' zaten var. Sifresini degistirmek icin force=True verin.")

    if existing:
        db.update_password(existing["id"], hash_password(password))
        db.revoke_all_for_user(existing["id"])
        return f"'{username}' şifresi güncellendi (tüm oturumlar düşürüldü)."
    db.create_user(username, hash_password(password), role="admin")
    return f"Admin kullanıcısı oluşturuldu: {username}"


def cmd_seed_admin(args):
    from auth.db import AuthDB

    username = args.username or config.AUTH_ADMIN_USERNAME
    password = args.password or config.AUTH_ADMIN_PASSWORD
    if not password:
        print("❌ Şifre gerekli: --password verin veya .env'de AUTH_ADMIN_PASSWORD tanımlayın.")
        sys.exit(1)

    db = AuthDB()
    try:
        message = seed_admin(db, username, password, force=args.force)
    except FileExistsError as e:
        print(f"❌ {e}")
        sys.exit(1)
    print(f"✅ {message}")


def cmd_add_user(args):
    """Ekip hesabi acar. Sifre verilmezse uretilir ve BIR KEZ ekrana basilir."""
    import secrets

    from auth.db import AuthDB
    from auth.security import hash_password

    db = AuthDB()
    if db.get_user_by_username(args.username):
        print(f"❌ '{args.username}' zaten var.")
        sys.exit(1)

    password = args.password or secrets.token_urlsafe(9)
    db.create_user(args.username, hash_password(password), role=args.role,
                   full_name=args.full_name)

    print(f"✅ {args.full_name or args.username} ({args.role}) oluşturuldu.")
    if not args.password:
        print(f"   Kullanıcı adı: {args.username}")
        print(f"   Şifre        : {password}")
        print("   Bu şifre bir daha gösterilmez — ilet ve değiştirilmesini iste.")


def cmd_doctor(args):
    """Yapılandırılmış her GLS sağlayıcısına gerçek bir bağlantı testi yapar."""
    print(f"Mod: {config.MODE}\n")
    results = providers.doctor()

    live = 0
    for r in results:
        if not r["configured"]:
            print(f"⚪ {r['title']}: yapılandırılmamış — {r['detail']}")
        elif r["ok"]:
            print(f"✅ {r['title']}: {r['detail']}")
            live += 1
        else:
            print(f"❌ {r['title']}: {r['detail']}")

    print()
    if live:
        print(f"{live} sağlayıcı canlı.")
    else:
        print("Hiçbir sağlayıcı canlı değil. .env'i doldurun; "
              "GLS'ten ne isteneceği için: docs/IT_TALEP_NOTU.md")
        sys.exit(1)


def cmd_run(args):
    result = run_pipeline(
        excel_path=args.excel,
        sheet_name=args.sheet,
        only_delivered=not args.all_statuses,
        method_filter=args.method,
        limit=args.limit,
    )
    print(f"\n🏁 Başarılı: {result['success']} | Hatalı: {result['failed']} | "
          f"Toplam: {result['total']}")
    if args.zip:
        zip_path = make_zip()
        print(f"📦 ZIP: {zip_path}")


def main():
    p = argparse.ArgumentParser(description="GLS Teslimat Kanıtı API Pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo", help="Mock API'lerle hızlı doğrulama")
    sub.add_parser("doctor", help="Yapılandırılmış GLS sağlayıcılarına canlı bağlantı testi")

    sa = sub.add_parser("seed-admin", help="İlk admin kullanıcısını oluştur/şifresini sıfırla")
    sa.add_argument("--username", default=None, help="Varsayılan: .env AUTH_ADMIN_USERNAME")
    sa.add_argument("--password", default=None, help="Varsayılan: .env AUTH_ADMIN_PASSWORD")
    sa.add_argument("--force", action="store_true", help="Kullanıcı zaten varsa şifresini değiştir")

    au = sub.add_parser("add-user", help="Ekip hesabı aç (şifre verilmezse üretilir)")
    au.add_argument("--username", required=True)
    au.add_argument("--full-name", default="", help="Panelde görünen ad soyad")
    au.add_argument("--role", default="operator", choices=["operator", "admin"])
    au.add_argument("--password", default=None, help="Verilmezse rastgele üretilir")

    r = sub.add_parser("run", help="Excel'den toplu POD indir")
    r.add_argument("--excel", required=True)
    r.add_argument("--sheet", default="TESLİMAT KANITI ALINACAKLAR")
    r.add_argument("--method", default=None, help="Örn: '7th Truck Shipment'")
    r.add_argument("--limit", type=int, default=0, help="Test için ilk N satır")
    r.add_argument("--all-statuses", action="store_true",
                   help="Delivered filtresini kapat")
    r.add_argument("--zip", action="store_true", help="Bitince ZIP oluştur")

    args = p.parse_args()
    if args.cmd == "demo":
        cmd_demo(args)
    elif args.cmd == "doctor":
        cmd_doctor(args)
    elif args.cmd == "seed-admin":
        cmd_seed_admin(args)
    elif args.cmd == "add-user":
        cmd_add_user(args)
    elif args.cmd == "run":
        cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
