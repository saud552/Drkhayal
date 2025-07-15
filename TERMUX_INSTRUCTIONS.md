# تعليمات إصلاح TDLib في Termux

## المشكلة
```
ModuleNotFoundError: No module named 'pytdlib'
```

## الحل

### الخطوة 1: إزالة الحزمة الخاطئة
```bash
# في Termux
pip uninstall python-telegram -y
```

### الخطوة 2: تثبيت الحزمة الصحيحة
```bash
# تثبيت aiotdlib
pip install aiotdlib
```

### الخطوة 3: تحديث ملف tdlib_client.py
انسخ محتوى ملف `tdlib_fix.py` إلى `Telegram/tdlib_client.py`

### الخطوة 4: تحديث ملف requirements.txt
```bash
# افتح ملف requirements.txt
nano requirements.txt

# استبدل السطر:
# pytdlib
# بـ:
# aiotdlib>=0.27.0
```

### الخطوة 5: اختبار التثبيت
```bash
python3 -c "from aiotdlib import Client; print('تم التثبيت بنجاح!')"
```

## إذا واجهت مشاكل في التثبيت

### خيار 1: استخدام --user
```bash
pip install aiotdlib --user
```

### خيار 2: استخدام virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install aiotdlib
```

### خيار 3: تحديث pip أولاً
```bash
pip install --upgrade pip
pip install aiotdlib
```

## ملاحظات مهمة
- تأكد من وجود ملف `libtdjson.so` في مجلد المشروع
- إذا لم يكن موجود، قم بتحميله من: https://github.com/up9cloud/android-libtdjson
- اختر الإصدار المناسب لمعالج هاتفك (arm64-v8a أو armv7)