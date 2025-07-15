#!/bin/bash

# ===================================================================
# سكريبت التثبيت الآلي للنظام
# Auto Installation Script
# ===================================================================

echo "🚀 بدء تثبيت نظام DrKhayal..."
echo "Starting DrKhayal system installation..."

# التحقق من وجود Python
echo "📋 التحقق من Python..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 غير موجود. يرجى تثبيته أولاً."
    exit 1
fi

echo "✅ Python3 موجود"

# التحقق من وجود pip
echo "📋 التحقق من pip..."
if ! command -v pip &> /dev/null; then
    echo "❌ pip غير موجود. يرجى تثبيته أولاً."
    exit 1
fi

echo "✅ pip موجود"

# إزالة أي مكتبات متضاربة
echo "🧹 تنظيف المكتبات المتضاربة..."
pip uninstall python-telegram telethon -y 2>/dev/null

# تحديث pip
echo "⬆️ تحديث pip..."
pip install --upgrade pip

# تثبيت المتطلبات
echo "📦 تثبيت المتطلبات..."
pip install -r requirements.txt

# التحقق من التثبيت
echo "🔍 التحقق من التثبيت..."
python3 -c "
try:
    import pytdlib
    import telegram
    import cryptography
    print('✅ جميع المكتبات تم تثبيتها بنجاح')
except ImportError as e:
    print(f'❌ خطأ في التثبيت: {e}')
    exit(1)
"

echo "🎉 التثبيت مكتمل بنجاح!"
echo "📚 لتشغيل البوتات:"
echo "   - بوت الحسابات: python3 add.py"
echo "   - بوت البلاغات: python3 khayal.py"
echo ""
echo "📖 اقرأ INSTALLATION_FIX.md للمزيد من المعلومات"