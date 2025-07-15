# تحذير: هذا الملف يحتوي على بيانات حساسة!
# لا يجب مشاركة هذا الملف أو رفعه إلى نظام تحكم بالإصدارات العام
# استخدم متغيرات البيئة (.env) في بيئة الإنتاج

import os

# تأكد من وجود المتغيرات الأساسية لـ TDLib
API_ID = int(os.getenv('TG_API_ID', '26924046'))
API_HASH = os.getenv('TG_API_HASH', '4c6ef4cee5e129b7a674de156e2bcc15')
BOT_TOKEN = os.getenv('BOT_TOKEN', 'ضع التوكن هنا')
OWNER_ID = 985612253
BOT_USERNAME = '@AAAK6BOT'
START_DATE = 1746590427.040948
EXPIRY_DAYS = 30
DB_PATH = 'accounts.db'