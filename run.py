#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ملف تشغيل محسن لمشروع Drkhayal
يقوم بفحص المتطلبات وتشغيل البوت
"""

import sys
import os
import subprocess
import importlib.util

def check_module(module_name, package_name=None):
    """فحص وجود مكتبة معينة"""
    if package_name is None:
        package_name = module_name
    
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        print(f"❌ المكتبة {module_name} غير مثبتة")
        print(f"   لتثبيتها: pip3 install {package_name}")
        return False
    else:
        print(f"✅ المكتبة {module_name} مثبتة")
        return True

def check_python_version():
    """فحص إصدار Python"""
    if sys.version_info < (3, 8):
        print(f"❌ إصدار Python الحالي: {sys.version}")
        print("❌ يتطلب Python 3.8 أو أحدث")
        return False
    else:
        print(f"✅ إصدار Python: {sys.version.split()[0]}")
        return True

def check_files():
    """فحص وجود الملفات المطلوبة"""
    required_files = [
        "khayal.py",
        "config.py", 
        "encryption.py",
        "Email/__init__.py",
        "Telegram/__init__.py"
    ]
    
    all_exist = True
    for file_path in required_files:
        if os.path.exists(file_path):
            print(f"✅ الملف {file_path} موجود")
        else:
            print(f"❌ الملف {file_path} مفقود")
            all_exist = False
    
    return all_exist

def install_missing_packages():
    """تثبيت المكتبات المفقودة"""
    packages = [
        ("telegram", "python-telegram-bot"),
        ("telethon", "telethon"),
        ("cryptography", "cryptography"),
        ("requests", "requests")
    ]
    
    missing_packages = []
    for module, package in packages:
        if not check_module(module, package):
            missing_packages.append(package)
    
    if missing_packages:
        print(f"\n🔄 تثبيت {len(missing_packages)} مكتبة مفقودة...")
        for package in missing_packages:
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", package], 
                             check=True, capture_output=True)
                print(f"✅ تم تثبيت {package}")
            except subprocess.CalledProcessError:
                print(f"❌ فشل في تثبيت {package}")
                return False
    
    return True

def main():
    """الدالة الرئيسية"""
    print("🚀 فحص نظام Drkhayal Bot")
    print("=" * 50)
    
    # فحص إصدار Python
    if not check_python_version():
        sys.exit(1)
    
    print("\n📁 فحص الملفات...")
    if not check_files():
        print("❌ بعض الملفات مفقودة!")
        sys.exit(1)
    
    print("\n📦 فحص المكتبات...")
    if not install_missing_packages():
        print("❌ فشل في تثبيت بعض المكتبات!")
        sys.exit(1)
    
    print("\n🎉 جميع المتطلبات متوفرة!")
    print("🔄 تشغيل البوت...")
    
    try:
        # تشغيل البوت
        subprocess.run([sys.executable, "khayal.py"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ في تشغيل البوت: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⏹️  تم إيقاف البوت بواسطة المستخدم")

if __name__ == "__main__":
    main()