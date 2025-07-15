@echo off
REM ===================================================================
REM سكريپت التثبيت الآلي للنظام - Windows
REM Auto Installation Script for Windows
REM ===================================================================

echo 🚀 بدء تثبيت نظام DrKhayal...
echo Starting DrKhayal system installation...

REM التحقق من وجود Python
echo 📋 التحقق من Python...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Python غير موجود. يرجى تثبيته أولاً.
    pause
    exit /b 1
)

echo ✅ Python موجود

REM التحقق من وجود pip
echo 📋 التحقق من pip...
pip --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ❌ pip غير موجود. يرجى تثبيته أولاً.
    pause
    exit /b 1
)

echo ✅ pip موجود

REM إزالة أي مكتبات متضاربة
echo 🧹 تنظيف المكتبات المتضاربة...
pip uninstall python-telegram telethon -y >nul 2>&1

REM تحديث pip
echo ⬆️ تحديث pip...
pip install --upgrade pip

REM تثبيت المتطلبات
echo 📦 تثبيت المتطلبات...
pip install -r requirements.txt

REM التحقق من التثبيت
echo 🔍 التحقق من التثبيت...
python -c "try: import pytdlib, telegram, cryptography; print('✅ جميع المكتبات تم تثبيتها بنجاح') except ImportError as e: print(f'❌ خطأ في التثبيت: {e}'); exit(1)"

echo 🎉 التثبيت مكتمل بنجاح!
echo 📚 لتشغيل البوتات:
echo    - بوت الحسابات: python add.py
echo    - بوت البلاغات: python khayal.py
echo.
echo 📖 اقرأ INSTALLATION_FIX.md للمزيد من المعلومات
pause