# 📦 دليل التثبيت والإعداد - النظام المحسن

## 📋 المتطلبات الأساسية

### 1. متطلبات النظام:
- **Python:** إصدار 3.8 أو أحدث
- **نظام التشغيل:** Linux، Windows، أو macOS
- **الذاكرة:** 2GB RAM على الأقل
- **التخزين:** 1GB مساحة فارغة

### 2. حسابات مطلوبة:
- **حساب Telegram Developer:** للحصول على API ID و API Hash
- **Bot Token:** من @BotFather في Telegram

---

## 🚀 خطوات التثبيت

### الخطوة 1: تحضير البيئة

```bash
# إنشاء مجلد المشروع
mkdir telegram_enhanced_reporter
cd telegram_enhanced_reporter

# إنشاء بيئة Python معزولة (مستحسن)
python3 -m venv venv
source venv/bin/activate  # على Linux/macOS
# أو
venv\Scripts\activate     # على Windows
```

### الخطوة 2: تنزيل الملفات

```bash
# نسخ جميع ملفات المشروع إلى المجلد
# تأكد من وجود هذه الملفات:
ls -la
# يجب أن ترى:
# - khayal.py
# - config_enhanced.py
# - Telegram/common_improved.py
# - Telegram/
# - requirements_enhanced.txt
```

### الخطوة 3: تثبيت المتطلبات

```bash
# تثبيت المكتبات المحسنة (مستحسن)
pip install -r requirements_enhanced.txt

# أو تثبيت المكتبات الأساسية فقط
pip install -r requirements.txt

# أو تثبيت يدوياً
pip install python-telegram-bot telethon cryptography aiohttp
```

### الخطوة 4: إعداد المتغيرات

إنشاء ملف `.env`:
```bash
# نسخ ملف القالب وتحريره
cp .env.example .env

# أو إنشاء ملف .env يدوياً
cat > .env << 'EOF'
TG_API_ID=your_api_id_here
TG_API_HASH=your_api_hash_here
BOT_TOKEN=your_bot_token_here
ENHANCED_MODE=default
EOF
```

أو استخدام متغيرات البيئة مباشرة:
```bash
export TG_API_ID=your_api_id_here
export TG_API_HASH=your_api_hash_here
export BOT_TOKEN=your_bot_token_here
export ENHANCED_MODE=production  # للبيئة الإنتاجية
```

---

## 🔧 إعداد Telegram API

### 1. الحصول على API ID و API Hash:

1. اذهب إلى [my.telegram.org](https://my.telegram.org)
2. سجل الدخول بحساب Telegram الخاص بك
3. اختر "API development tools"
4. املأ النموذج:
   - **App title:** Reporter Enhanced
   - **Short name:** reporter_enhanced
   - **Platform:** Other
5. احفظ `api_id` و `api_hash`

### 2. إنشاء Bot Token:

1. ابحث عن [@BotFather](https://t.me/BotFather) في Telegram
2. أرسل `/newbot`
3. اتبع التعليمات لإنشاء بوت جديد
4. احفظ Token المعطى

---

## ⚙️ تخصيص الإعدادات

### إعدادات البروكسي:
```python
# في config_enhanced.py
enhanced_config.proxy.check_timeout = 20  # زيادة timeout
enhanced_config.proxy.concurrent_checks = 2  # تقليل الفحوصات المتزامنة
enhanced_config.proxy.quality_threshold = 70  # رفع حد الجودة
```

### إعدادات البلاغات:
```python
enhanced_config.report.max_reports_per_session = 30  # تقليل للأمان
enhanced_config.report.min_delay_between_reports = 2.0  # زيادة التأخير
enhanced_config.security.max_reports_per_hour = 500  # حد أكثر تحفظاً
```

---

## 🧪 اختبار التثبيت

### اختبار أساسي:
```bash
python3 -c "
from config_enhanced import enhanced_config
print('✅ النظام المحسن مثبت بنجاح!')
print(f'📊 الوضع الحالي: {enhanced_config.debug_mode}')
"
```

### اختبار شامل:
```bash
# اختبار مع وضع الاختبار
ENHANCED_MODE=testing python3 khayal.py
```

---

## 🔥 التشغيل الأول

### 1. التشغيل في وضع الاختبار:
```bash
ENHANCED_MODE=testing python3 khayal.py
```

### 2. التشغيل العادي:
```bash
python3 khayal.py
```

### 3. التشغيل في وضع الإنتاج:
```bash
ENHANCED_MODE=production python3 khayal.py
```

---

## 📊 التحقق من الأداء

### ملفات التسجيل:
```bash
# مراقبة السجل المفصل
tail -f detailed_reports.log

# البحث عن أخطاء
grep "ERROR" detailed_reports.log

# إحصائيات البروكسي
grep "بروكسي نشط" detailed_reports.log
```

### مراقبة الأداء:
```bash
# مراقبة استخدام الموارد
top -p $(pgrep -f khayal.py)

# مراقبة الاتصالات
netstat -tulpn | grep python
```

---

## 🐛 حل مشاكل التثبيت

### مشكلة: "ModuleNotFoundError"
```bash
# التأكد من تفعيل البيئة الافتراضية
source venv/bin/activate

# إعادة تثبيت المتطلبات
pip install --upgrade -r requirements_enhanced.txt
```

### مشكلة: "Permission denied"
```bash
# على Linux/macOS
chmod +x khayal.py

# أو تشغيل مع python مباشرة
python3 khayal.py
```

### مشكلة: "API errors"
```bash
# التحقق من المتغيرات
echo $TG_API_ID
echo $TG_API_HASH
echo $BOT_TOKEN

# اختبار الاتصال
python3 -c "
import os
print(f'API ID: {os.getenv(\"TG_API_ID\", \"غير محدد\")}')
print(f'API Hash: {os.getenv(\"TG_API_HASH\", \"غير محدد\")}')
print(f'Bot Token: {os.getenv(\"BOT_TOKEN\", \"غير محدد\")}')
"
```

### مشكلة: البروكسيات لا تعمل
```bash
# اختبار بدون بروكسي أولاً
# ثم فحص روابط البروكسي:
python3 -c "
from Telegram.common_improved import parse_proxy_link_enhanced
test_link = 'https://t.me/proxy?server=1.2.3.4&port=443&secret=ee123...'
result = parse_proxy_link_enhanced(test_link)
print(f'نتيجة التحليل: {result}')
"
```

---

## 🔧 تحسين الأداء

### للبيئات الإنتاجية:
```bash
# استخدام uvloop لتحسين asyncio (Linux فقط)
pip install uvloop

# تحسين DNS
pip install aiodns

# إعداد متغيرات التحسين
export PYTHONUNBUFFERED=1
export ASYNCIO_DEBUG=0
```

### للذاكرة المحدودة:
```python
# في config_enhanced.py
enhanced_config.session.max_concurrent_sessions = 3
enhanced_config.proxy.concurrent_checks = 1
```

---

## 📋 قائمة مراجعة ما بعد التثبيت

- [ ] ✅ تم تثبيت Python 3.8+
- [ ] ✅ تم تثبيت جميع المتطلبات
- [ ] ✅ تم إعداد API ID, API Hash, Bot Token
- [ ] ✅ تم اختبار التشغيل في وضع الاختبار
- [ ] ✅ تعمل ملفات التسجيل بشكل صحيح
- [ ] ✅ تم اختبار فحص البروكسي
- [ ] ✅ تم اختبار إرسال بلاغ تجريبي
- [ ] ✅ تم تخصيص الإعدادات حسب الحاجة

---

## 📞 الحصول على المساعدة

إذا واجهت مشاكل:

1. **راجع الملفات:**
   - `detailed_reports.log` - سجل العمليات
   - `QUICK_START.md` - دليل الاستخدام السريع
   - `ENHANCED_FEATURES.md` - المميزات المفصلة

2. **اختبار المكونات:**
   ```bash
   # اختبار التكوين
   python3 -c "from config_enhanced import enhanced_config; print('OK')"
   
   # اختبار النظام المحسن
   python3 -c "from Telegram.common_improved import enhanced_proxy_checker; print('OK')"
   ```

3. **إعادة تثبيت نظيفة:**
   ```bash
   rm -rf venv/
   python3 -m venv venv
   source venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements_enhanced.txt
   ```

---

**🎉 مبروك! النظام المحسن جاهز للاستخدام**

> **نصيحة:** ابدأ دائماً بوضع الاختبار للتأكد من عمل كل شيء بشكل صحيح قبل التبديل للوضع الإنتاجي.