#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
سكريبت إعداد مشروع Drkhayal
يقوم بتثبيت جميع المتطلبات وإعداد البيئة
"""

import os
import sys
import subprocess
import shutil

def run_command(command, description):
    """تشغيل أمر مع عرض الوصف"""
    print(f"🔄 {description}")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} - تم بنجاح")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ في {description}: {e}")
        print(f"Output: {e.stdout}")
        print(f"Error: {e.stderr}")
        return False

def check_python_version():
    """التحقق من إصدار Python"""
    if sys.version_info < (3, 8):
        print("❌ يتطلب Python 3.8 أو أحدث")
        return False
    print(f"✅ Python {sys.version.split()[0]} متوفر")
    return True

def install_requirements():
    """تثبيت المتطلبات"""
    requirements = [
        "python-telegram-bot>=20.0",
        "telethon>=1.24.0", 
        "cryptography>=3.4.8",
        "requests>=2.25.1",
        "aiofiles>=0.7.0"
    ]
    
    for req in requirements:
        if not run_command(f"pip3 install {req}", f"تثبيت {req}"):
            return False
    return True

def setup_config():
    """إعداد ملف الإعدادات"""
    if not os.path.exists(".env"):
        if os.path.exists(".env.example"):
            shutil.copy(".env.example", ".env")
            print("✅ تم إنشاء ملف .env من .env.example")
        else:
            print("⚠️  يرجى إنشاء ملف .env يدوياً")
    else:
        print("✅ ملف .env موجود بالفعل")

def main():
    """الدالة الرئيسية للإعداد"""
    print("🚀 بدء إعداد مشروع Drkhayal")
    print("=" * 50)
    
    # التحقق من إصدار Python
    if not check_python_version():
        sys.exit(1)
    
    # تثبيت المتطلبات
    print("\n📦 تثبيت المكتبات المطلوبة...")
    if not install_requirements():
        print("❌ فشل في تثبيت بعض المتطلبات")
        sys.exit(1)
    
    # إعداد ملف الإعدادات
    print("\n⚙️  إعداد ملفات الإعدادات...")
    setup_config()
    
    print("\n🎉 تم إعداد المشروع بنجاح!")
    print("\n📋 الخطوات التالية:")
    print("1. قم بتحرير ملف .env وأضف بياناتك")
    print("2. شغل البوت باستخدام: python3 khayal.py")

if __name__ == "__main__":
    main()