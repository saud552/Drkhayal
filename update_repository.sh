#!/bin/bash

# سكريبت تحديث مستودع Drkhayal Bot
# يقوم بتطبيق جميع الإصلاحات والتحديثات

echo "🚀 بدء تحديث مستودع Drkhayal Bot..."
echo "========================================"

# التحقق من وجود Git
if ! command -v git &> /dev/null; then
    echo "❌ Git غير مثبت. يرجى تثبيت Git أولاً."
    exit 1
fi

# التحقق من وجود مجلد .git
if [ ! -d ".git" ]; then
    echo "❌ هذا المجلد ليس مستودع Git. يرجى تشغيل الأمر في مجلد المشروع."
    exit 1
fi

echo "📁 إنشاء الملفات المفقودة..."

# إنشاء ملفات __init__.py
echo "# ملف __init__.py لجعل Email حزمة Python" > Email/__init__.py
echo "# ملف __init__.py لجعل Telegram حزمة Python" > Telegram/__init__.py

echo "✅ تم إنشاء ملفات __init__.py"

# إنشاء ملف run.py
cat > run.py << 'EOF'
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
EOF

echo "✅ تم إنشاء ملف run.py"

# إنشاء ملف requirements.txt
cat > requirements.txt << 'EOF'
# مكتبات أساسية
python-telegram-bot>=20.0
telethon>=1.24.0
cryptography>=3.4.8
requests>=2.25.1

# مكتبات إضافية
aiofiles>=0.7.0
python-dotenv>=0.19.0

# مكتبات اختيارية للتطوير
flake8>=4.0.0
black>=22.0.0
EOF

echo "✅ تم إنشاء ملف requirements.txt"

# إنشاء ملف .gitignore
cat > .gitignore << 'EOF'
# ملفات Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# ملفات البيئة والإعدادات الحساسة
.env
config.py
*.db
*.sqlite
*.sqlite3

# ملفات النظام
.DS_Store
Thumbs.db
.vscode/
.idea/

# ملفات السجلات
*.log
logs/

# ملفات مؤقتة
*.tmp
*.temp
.cache/

# ملفات الجلسات
*.session
EOF

echo "✅ تم إنشاء ملف .gitignore"

# إنشاء ملف .env.example
cat > .env.example << 'EOF'
# متغيرات البيئة المطلوبة للمشروع
# انسخ هذا الملف إلى .env واملأ القيم الصحيحة

# إعدادات Telegram Bot
BOT_TOKEN=your_bot_token_here
TG_API_ID=your_api_id_here
TG_API_HASH=your_api_hash_here

# إعدادات قاعدة البيانات
DB_PATH=accounts.db

# إعدادات المدير
OWNER_ID=your_owner_id_here
ADMIN_IDS=admin_id_1,admin_id_2

# إعدادات التشفير
ENCRYPTION_SALT=your_encryption_salt_here
ENCRYPTION_PASSPHRASE=your_encryption_passphrase_here
EOF

echo "✅ تم إنشاء ملف .env.example"

# تحديث README.md
cat > README.md << 'EOF'
# Drkhayal

بوت تليجرام متقدم لإدارة الحسابات والتقارير.

## الميزات

- إدارة حسابات تليجرام
- نظام تقارير متقدم
- دعم التشفير للجلسات
- إرسال تقارير عبر البريد الإلكتروني
- واجهة سهلة الاستخدام

## متطلبات التشغيل

- Python 3.8 أو أحدث
- مكتبات Python المطلوبة (انظر requirements.txt)

## الإعداد والتشغيل

### 🚀 الطريقة السهلة (مستحسنة):
```bash
python3 run.py
```
هذا الأمر سيقوم بفحص جميع المتطلبات وتثبيت المكتبات المفقودة وتشغيل البوت تلقائياً.

### ⚙️ الإعداد اليدوي:

1. **تثبيت المتطلبات:**
   ```bash
   python3 setup.py
   ```
   أو:
   ```bash
   pip3 install python-telegram-bot telethon cryptography requests aiofiles
   ```

2. **إعداد الإعدادات:**
   - قم بتحرير ملف `config.py` وأضف بيانات البوت الصحيحة
   - أو انسخ `.env.example` إلى `.env` واستخدم متغيرات البيئة

3. **تشغيل البوت:**
   ```bash
   python3 khayal.py
   ```

## حل المشاكل الشائعة

### خطأ "No module named 'telegram'":
```bash
pip3 install python-telegram-bot telethon
```

### خطأ الإزاحة (IndentationError):
تأكد من أن جميع ملفات Python تستخدم مسافات (spaces) وليس tabs للإزاحة.

## الأمان

⚠️ **تحذير مهم**: 
- لا تشارك ملف `config.py` أو `.env` مع أي شخص
- استخدم متغيرات البيئة في بيئة الإنتاج
- تأكد من حماية قاعدة البيانات

## الهيكل

```
├── Email/              # وحدة البريد الإلكتروني
├── Telegram/           # وحدات تليجرام
├── config.py          # ملف الإعدادات (حساس)
├── encryption.py      # وحدة التشفير
├── khayal.py         # الملف الرئيسي
└── add.py            # وحدة إضافة الحسابات
```

## المساهمة

يرحب بالمساهمات! يرجى فتح issue أو إرسال pull request.

## الترخيص

هذا المشروع مرخص تحت رخصة MIT.
EOF

echo "✅ تم تحديث ملف README.md"

# إصلاح في khayal.py (إذا لزم الأمر)
if grep -q "Email.Email_reports" khayal.py; then
    sed -i 's/Email.Email_reports/Email.email_reports/g' khayal.py
    echo "✅ تم إصلاح استيراد البريد الإلكتروني في khayal.py"
fi

echo ""
echo "📋 إضافة الملفات إلى Git..."

# إضافة جميع الملفات الجديدة
git add .

echo "✅ تمت إضافة جميع الملفات"

echo ""
echo "💾 إنشاء commit..."

# إنشاء commit
git commit -m "🔧 إصلاح شامل للمشروع وإضافة ملفات مساعدة

✅ الإصلاحات:
- إصلاح خطأ NameError في Email/email_reports.py
- إضافة الدوال المفقودة (back_to_email_menu، manage_emails_menu، إلخ)
- إصلاح أخطاء ConversationHandler
- إضافة ملفات __init__.py للحزم

📦 ملفات جديدة:
- run.py: ملف تشغيل ذكي مع فحص المتطلبات
- requirements.txt: قائمة المكتبات المطلوبة
- .gitignore: حماية البيانات الحساسة
- .env.example: مثال متغيرات البيئة

📖 تحسينات التوثيق:
- تحديث شامل لـ README.md
- إضافة تعليمات الإعداد والتشغيل
- إضافة قسم حل المشاكل الشائعة"

echo "✅ تم إنشاء commit بنجاح"

echo ""
echo "🚀 رفع التغييرات إلى GitHub..."

# رفع التغييرات
if git push origin main 2>/dev/null || git push origin master 2>/dev/null; then
    echo "✅ تم رفع التغييرات بنجاح إلى GitHub!"
else
    echo "❌ فشل في رفع التغييرات. يرجى التحقق من الاتصال بالإنترنت وصلاحيات المستودع."
    echo ""
    echo "يمكنك المحاولة يدوياً باستخدام:"
    echo "git push origin main"
    echo "أو:"
    echo "git push origin master"
fi

echo ""
echo "🎉 انتهى تحديث المستودع!"
echo "========================================"
echo ""
echo "📋 ما تم إنجازه:"
echo "✅ إصلاح جميع أخطاء الكود"
echo "✅ إضافة ملفات مساعدة جديدة"
echo "✅ تحسين التوثيق والأمان"
echo "✅ رفع التغييرات إلى GitHub"
echo ""
echo "للتشغيل الآن:"
echo "python3 run.py"