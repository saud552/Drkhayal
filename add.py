import os
import re
import uuid
import sqlite3
import logging
import asyncio
import random
import base64
import requests
import time
from datetime import datetime
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ConversationHandler, ContextTypes,
    CallbackQueryHandler
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, FloodWaitError,
    PhoneNumberInvalidError, PhoneCodeInvalidError,
    PhoneCodeExpiredError, ApiIdInvalidError,
    PhoneNumberBannedError, RPCError
)
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
from encryption import encrypt_session, decrypt_session
# ========== إعدادات التهيئة ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === إعدادات التطبيق ===
# استيراد إعدادات من ملف config.py
try:
    from config import API_ID, API_HASH, BOT_TOKEN, DB_PATH
except ImportError:
    # استخدام متغيرات البيئة كبديل
    API_ID = int(os.getenv('TG_API_ID', '26924046'))
    API_HASH = os.getenv('TG_API_HASH', '4c6ef4cee5e129b7a674de156e2bcc15')
    BOT_TOKEN = os.getenv('BOT_TOKEN', '7618405088:AAEikRuG-UXaLYqcrqGjgxf5k4V23U9kcAA')
    DB_PATH = 'accounts.db'
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '985612253').split(',') if x]
SESSION_TIMEOUT = 60  # ثانية
VIEW_PAGE_SIZE = 50  # عدد الحسابات في صفحة العرض
DEFAULT_PAGE_SIZE = 5  # عدد العناصر في الصفحة للعمليات الأخرى

if not all([API_ID, API_HASH, BOT_TOKEN]):
    raise ValueError("يجب تعيين المتغيرات البيئية: TG_API_ID, TG_API_HASH, BOT_TOKEN")

# === قائمة أجهزة Android ديناميكية ===
DEVICES = [
    # Google - أخر إصدارات
    {'device_model': 'Google Pixel 9 Pro', 'system_version': 'Android 15 (SDK 35)', 'app_version': 'Telegram Android 10.9.0', 'lang_code': 'en', 'lang_pack': 'android'},
]

# === حالات المحادثة ===
(
    MAIN_MENU, ADD_ACCOUNT_METHOD, ADD_ACCOUNT_SESSION, 
    ADD_ACCOUNT_CATEGORY, ADD_ACCOUNT_PHONE, 
    ADD_ACCOUNT_PHONE_HANDLE_EXISTING, ADD_ACCOUNT_CODE, 
    ADD_ACCOUNT_PASSWORD, DELETE_CATEGORY_SELECT,
    DELETE_ACCOUNT_SELECT, DELETE_ACCOUNT_CONFIRM, VIEW_CATEGORY_SELECT, 
    VIEW_ACCOUNTS, CHECK_CATEGORY_SELECT, CHECK_ACCOUNT_SELECT, 
    CHECK_ACCOUNT_DETAILS, CHECK_ACCOUNTS_IN_PROGRESS, STORAGE_CATEGORY_SELECT, 
    STORAGE_ACCOUNT_SELECT
) = range(19)

# === إنشاء قاعدة البيانات ===
# ===== تحديث دالة init_db =====
def init_db():
    # زيادة مهلة الاتصال إلى 20 ثانية وتمكين وضع WAL
    with sqlite3.connect(DB_PATH, timeout=20) as conn:
        # تمكين وضع WAL لتحسين الأداء في العمليات المتزامنة
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA busy_timeout=5000;')  # زيادة مهلة القفل
        
        # جدول الفئات (محدث)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1  -- إضافة حالة تنشيط الفئة
            )
        ''')
        
        # جدول الحسابات (محدث بإضافة حقول البروكسي)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL,
                username TEXT,
                session_str TEXT NOT NULL,
                phone TEXT NOT NULL,
                device_info TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                proxy_type TEXT,  -- نوع البروكسي (مثل 'mtproxy')
                proxy_server TEXT, -- عنوان الخادم
                proxy_port INTEGER, -- المنفذ
                proxy_secret TEXT,  -- السر (للـ MTProxy)
                FOREIGN KEY (category_id) REFERENCES categories(id)
            )
        ''')
        
        # إضافة الأعمدة الجديدة إذا لم تكن موجودة
        try:
            conn.execute("ALTER TABLE accounts ADD COLUMN is_active INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass  # تجاهل الخطأ إذا كان العمود موجوداً
        
        try:
            conn.execute("ALTER TABLE categories ADD COLUMN is_active INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        
        # التأكد من وجود فئة "حسابات التخزين"
        conn.execute(
            "INSERT OR IGNORE INTO categories (id, name, is_active) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), "حسابات التخزين", 1)
        )
        
        conn.commit()
        
        # التأكد من وجود فئة "حسابات التخزين"
        conn.execute(
            "INSERT OR IGNORE INTO categories (id, name, is_active) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), "حسابات التخزين", 1)
        )
        
        conn.commit()

init_db()
# ===== إضافة دالة مساعدة لاستعلامات قاعدة البيانات الآمنة =====
def safe_db_query(query: str, params: tuple = (), is_write: bool = False):
    """تنفيذ استعلام قاعدة بيانات مع إعادة المحاولة عند الأخطاء المؤقتة"""
    max_retries = 5
    for attempt in range(max_retries):
        try:
            with sqlite3.connect(DB_PATH, timeout=20) as conn:
                if is_write:
                    conn.execute('PRAGMA journal_mode=WAL;')
                cursor = conn.cursor()
                cursor.execute(query, params)
                if query.strip().upper().startswith("SELECT"):
                    return cursor.fetchall()
                conn.commit()
                return True
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                sleep_time = 0.5 * (attempt + 1)
                logger.warning(f"قاعدة البيانات مقفلة، إعادة المحاولة بعد {sleep_time} ثواني...")
                time.sleep(sleep_time)
                continue
            raise
    return None
    
# === دوال مساعدة ===
def get_random_device():
    return random.choice(DEVICES)

def validate_phone(phone: str) -> bool:
    return re.match(r'^\+\d{7,15}$', phone) is not None

def validate_code(code: str) -> bool:
    code = code.replace(' ', '').replace(',', '')
    return re.match(r'^\d{5,6}$', code) is not None

def restricted(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if ADMIN_IDS and uid not in ADMIN_IDS:
            await update.message.reply_text("⛔ ليس لديك صلاحية.")
            return
        return await func(update, context)
    return wrapper

async def create_client() -> TelegramClient:
    device = get_random_device()
    client = TelegramClient(
        StringSession(), 
        API_ID, 
        API_HASH,
        device_model=device['device_model'],
        system_version=device['system_version'],
        app_version=device['app_version'],
        lang_code=device['lang_code'],
        system_lang_code=device['lang_code'],
        connection_retries=3,
        timeout=SESSION_TIMEOUT
    )
    client._device_attrs = device
    return client

def get_categories_keyboard(page=0, action="check", only_non_empty=True):
    """إنشاء لوحة مفاتيح للفئات مع التقسيم للصفحات"""
    # بناء استعلام SQL بدون تعليقات
    query = """
        SELECT c.id, c.name, COUNT(a.id) 
        FROM categories c
        LEFT JOIN accounts a ON c.id = a.category_id AND a.is_active = 1
        WHERE c.is_active = 1
        GROUP BY c.id
    """
    
    # إضافة شرط HAVING إذا لزم الأمر
    if only_non_empty:
        query += " HAVING COUNT(a.id) > 0"
    
    query += " ORDER BY c.created_at DESC"
    
    categories = safe_db_query(query, is_write=False)
    
    if not categories:
        return None
    
    total_pages = (len(categories) + DEFAULT_PAGE_SIZE - 1) // DEFAULT_PAGE_SIZE
    start_idx = page * DEFAULT_PAGE_SIZE
    end_idx = start_idx + DEFAULT_PAGE_SIZE
    page_categories = categories[start_idx:end_idx]
    
    keyboard = []
    for category_id, category_name, account_count in page_categories:
        if action != "storage" or category_name != "حسابات التخزين":
            keyboard.append([InlineKeyboardButton(
                f"{category_name} ({account_count})", 
                callback_data=f"{action}_category_{category_id}"
            )])
    
    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("◀️ السابق", callback_data=f"prev_{page}"))
    if end_idx < len(categories):
        navigation_buttons.append(InlineKeyboardButton("▶️ التالي", callback_data=f"next_{page}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    keyboard.append([InlineKeyboardButton("الغاء", callback_data="cancel")])
    
    return InlineKeyboardMarkup(keyboard)

def get_accounts_keyboard(category_id, page=0, action_prefix="account", page_size=DEFAULT_PAGE_SIZE):
    """إنشاء لوحة مفاتيح للحسابات في فئة معينة"""
    # استبدال استعلام SQL باستخدام safe_db_query
    accounts = safe_db_query("""
        SELECT id, phone, username
        FROM accounts 
        WHERE category_id = ? AND is_active = 1
        ORDER BY created_at DESC
    """, (category_id,), is_write=False)
    
    if not accounts:
        return None
    
    total_pages = (len(accounts) + page_size - 1) // page_size
    start_idx = page * page_size
    end_idx = start_idx + page_size
    page_accounts = accounts[start_idx:end_idx]
    
    keyboard = []
    for account_id, phone, username in page_accounts:
        # عرض رقم الهاتف ويوزر الحساب إن وجد
        display_text = f"{phone}"
        if username:
            display_text += f" (@{username})"
        keyboard.append([InlineKeyboardButton(display_text, callback_data=f"{action_prefix}_{account_id}")])
    
    # أزرار التنقل بين الصفحات
    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("◀️ السابق", callback_data=f"prev_{page}"))
    if end_idx < len(accounts):
        navigation_buttons.append(InlineKeyboardButton("▶️ التالي", callback_data=f"next_{page}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)
    
    keyboard.append([InlineKeyboardButton("رجوع", callback_data="back_categories")])
    keyboard.append([InlineKeyboardButton("الغاء", callback_data="cancel")])
    
    return InlineKeyboardMarkup(keyboard)

async def check_account_restrictions(client):
    """فحص قيود الحساب باستخدام بوت SpamBot"""
    try:
        await client.send_message('SpamBot', '/start')
        await asyncio.sleep(2)
        messages = await client.get_messages('SpamBot', limit=1)
        if messages and messages[0].text:
            return messages[0].text
        return "❌ لم يتم الحصول على معلومات القيود"
    except Exception as e:
        logger.error(f"SpamBot error: {e}")
        return f"❌ حدث خطأ أثناء فحص القيود: {str(e)}"

# ========== معالجات الأوامر الرئيسية ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        ["➕ اضافه الحسابات"],
        ["👁️ عرض الحسابات"],
        ["🗑️ حذف حساب"],
        ["🔍 فحص الحسابات"],
        ["📦 حسابات التخزين"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "👋 مرحباً بك في نظام إدارة حسابات التليجرام!\n"
        "اختر أحد الخيارات من القائمة أدناه:",
        reply_markup=reply_markup
    )
    return MAIN_MENU

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    
    if text == "➕ اضافه الحسابات":
        # عرض خيارات إضافة الحساب
        keyboard = [
            [InlineKeyboardButton("➕ إضافة برقم الهاتف", callback_data="add_phone")],
            [InlineKeyboardButton("🔑 إضافة بكود الجلسة (للمشرف)", callback_data="add_session")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📋 اختر طريقة إضافة الحساب:",
            reply_markup=reply_markup
        )
        return ADD_ACCOUNT_METHOD
    
    elif text == "👁️ عرض الحسابات":
        keyboard = get_categories_keyboard(action="view", only_non_empty=True)
        if not keyboard:
            await update.message.reply_text("❌ لا توجد فئات متاحة.")
            return MAIN_MENU
        await update.message.reply_text(
            "📁 اختر الفئة لعرض حساباتها:",
            reply_markup=keyboard
        )
        return VIEW_CATEGORY_SELECT
    
    elif text == "🗑️ حذف حساب":
        keyboard = get_categories_keyboard(action="delete", only_non_empty=True)
        if not keyboard:
            await update.message.reply_text("❌ لا توجد فئات متاحة.")
            return MAIN_MENU
        await update.message.reply_text(
            "📁 اختر الفئة التي تحتوي على الحساب الذي تريد حذفه:",
            reply_markup=keyboard
        )
        return DELETE_CATEGORY_SELECT
    
    elif text == "🔍 فحص الحسابات":
        keyboard = get_categories_keyboard(action="check", only_non_empty=True)
        if not keyboard:
            await update.message.reply_text("❌ لا توجد فئات متاحة.")
            return MAIN_MENU
        await update.message.reply_text(
            "📁 اختر الفئة لفحص حساباتها:",
            reply_markup=keyboard
        )
        return CHECK_CATEGORY_SELECT
    
    elif text == "📦 حسابات التخزين":
        keyboard = get_categories_keyboard(action="storage", only_non_empty=True)
        if not keyboard:
            await update.message.reply_text("❌ لا توجد فئات متاحة.")
            return MAIN_MENU
        await update.message.reply_text(
            "📁 اختر الفئة التي تحتوي على الحساب المراد نقله للتخزين:",
            reply_markup=keyboard
        )
        return STORAGE_CATEGORY_SELECT
    
    await update.message.reply_text("❌ خيار غير صالح. الرجاء الاختيار من القائمة.")
    return MAIN_MENU

# ========== إضافة حساب جديد ==========
async def add_account_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if query.data == "add_phone":
        await query.edit_message_text(
            "📁 الرجاء إدخال اسم الفئة التي تريد تخزين الحساب فيها:",
            reply_markup=None
        )
        return ADD_ACCOUNT_CATEGORY
    
    elif query.data == "add_session":
        if update.effective_user.id not in ADMIN_IDS:
            await query.answer("⛔ هذا الخيار متاح للمشرفين فقط!", show_alert=True)
            return await start_from_query(query, context)
        
        await query.edit_message_text(
            "🔑 الرجاء إرسال كود جلسة Telethon الجاهز:",
            reply_markup=None
        )
        return ADD_ACCOUNT_SESSION

async def add_account_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    session_str = update.message.text.strip()
    
    try:
        # التحقق من صحة الجلسة
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()
        me = await client.get_me()
        
        if not me:
            raise ValueError("الجلسة غير صالحة")
        
        context.user_data['session_str'] = session_str
        context.user_data['phone'] = me.phone
        context.user_data['username'] = me.username
        
        await update.message.reply_text(
            "📁 الرجاء إدخال اسم الفئة التي تريد تخزين الحساب فيها:"
        )
        return ADD_ACCOUNT_CATEGORY
        
    except Exception as e:
        logger.error(f"Session validation error: {e}")
        await update.message.reply_text(
            f"❌ فشل التحقق من الجلسة: {str(e)}\n"
            "الرجاء المحاولة مرة أخرى أو استخدام طريقة أخرى."
        )
        return ADD_ACCOUNT_METHOD

async def add_account_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    category_name = update.message.text.strip()
    context.user_data['category_name'] = category_name
    
    # إنشاء أو الحصول على الفئة
    category_id = str(uuid.uuid4())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO categories (id, name) VALUES (?, ?)",
            (category_id, category_name)
        )
        conn.commit()
    
    # إذا كانت الإضافة عن طريق الجلسة
    if 'session_str' in context.user_data:
        return await save_account_from_session(update, context, category_name)
    
    # إذا كانت الإضافة عن طريق الهاتف
    await update.message.reply_text(
        "📱 الرجاء إرسال رقم الهاتف بصيغة دولية (مثال: +967771234567)\n"
        "❌ للإلغاء: /cancel"
    )
    return ADD_ACCOUNT_PHONE

async def save_account_from_session(update: Update, context: ContextTypes.DEFAULT_TYPE, category_name: str):
    session_str = context.user_data['session_str']
    phone = context.user_data['phone']
    username = context.user_data.get('username')
    
    try:
        # اختيار جهاز عشوائي
        device = get_random_device()
        device_info = {
            'app_name': device.get('app_name', 'Telegram'),
            'app_version': device['app_version'],
            'device_model': device['device_model'],
            'system_version': device['system_version']
        }
        
        # تشفير الجلسة
        encrypted_session = encrypt_session(session_str)
        
        # الحصول على معرف الفئة باستخدام الاستعلام الآمن
        category_result = safe_db_query(
            "SELECT id FROM categories WHERE name = ?",
            (category_name,),
            is_write=False
        )
        
        if not category_result:
            await update.message.reply_text(f"❌ فئة '{category_name}' غير موجودة!")
            return ConversationHandler.END
            
        category_id = category_result[0][0]
        
        # إنشاء معرف فريد للحساب
        account_id = str(uuid.uuid4())
        
        # إدخال الحساب في قاعدة البيانات باستخدام الاستعلام الآمن
        success = safe_db_query(
            """
            INSERT INTO accounts (
                id, category_id, username, session_str, 
                phone, device_info, proxy_type, 
                proxy_server, proxy_port, proxy_secret
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                category_id,
                username,
                encrypted_session,
                phone,
                str(device_info),
                None,  # proxy_type
                None,  # proxy_server
                None,  # proxy_port
                None   # proxy_secret
            ),
            is_write=True
        )
        
        if not success:
            raise Exception("فشل إدخال الحساب في قاعدة البيانات")
        
        await update.message.reply_text(
            f"✅ تم إضافة الحساب بنجاح في فئة '{category_name}'!\n\n"
            f"📱 الهاتف: {phone}\n"
            f"👤 المستخدم: @{username or 'غير معروف'}\n"
            f"📁 الفئة: {category_name}"
        )
        
        # تنظيف بيانات المستخدم
        context.user_data.clear()
        return await start(update, context)
        
    except Exception as e:
        logger.error(f"Save account from session error: {e}")
        await update.message.reply_text(
            f"❌ فشل حفظ الحساب: {str(e)}\n"
            "الرجاء المحاولة مرة أخرى."
        )
        return ConversationHandler.END

# ========== دوال إضافة الحساب عن طريق الهاتف ==========
async def add_account_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة رقم الهاتف لإضافة حساب"""
    phone = update.message.text.strip()
    
    # التحقق من صحة رقم الهاتف
    if not validate_phone(phone):
        await update.message.reply_text("❌ رقم الهاتف غير صالح. الرجاء إرسال رقم بصيغة دولية صحيحة.")
        return ADD_ACCOUNT_PHONE
    
    # التحقق من وجود الحساب مسبقاً
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM accounts WHERE phone = ?", (phone,))
        existing_account = cursor.fetchone()
    
    if existing_account:
        # إذا كان الحساب موجوداً، عرض خيارات
        keyboard = [
            [InlineKeyboardButton("حذف الحساب القديم وإضافة جديد", callback_data="replace_account")],
            [InlineKeyboardButton("استخدام رقم آخر", callback_data="use_another")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "⚠️ هذا الرقم مسجل مسبقاً في النظام.\n"
            "اختر أحد الخيارات:",
            reply_markup=reply_markup
        )
        context.user_data['phone'] = phone
        return ADD_ACCOUNT_PHONE_HANDLE_EXISTING
    
    # إذا لم يكن الحساب موجوداً، المتابعة
    context.user_data['phone'] = phone
    return await start_phone_verification(update, context)

async def handle_existing_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيارات الحساب الموجود مسبقاً"""
    query = update.callback_query
    await query.answer()
    
    choice = query.data
    phone = context.user_data.get('phone')
    
    if choice == "replace_account":
        # حذف الحساب القديم
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM accounts WHERE phone = ?", (phone,))
            conn.commit()
        
        await query.edit_message_text("✅ تم حذف الحساب القديم. الرجاء المتابعة لإضافة الحساب الجديد.")
        return await start_phone_verification(query, context)
    
    elif choice == "use_another":
        await query.edit_message_text("📱 الرجاء إرسال رقم هاتف جديد:")
        return ADD_ACCOUNT_PHONE

async def start_phone_verification(update, context):
    """بدء عملية التحقق بالهاتف"""
    phone = context.user_data['phone']
    
    try:
        # إنشاء عميل مؤقت
        client = await create_client()
        await client.connect()
        
        # إرسال رمز التحقق مع إعدادات إضافية
        sent = await client.send_code_request(
            phone,
            force_sms=True  # إجبار الإرسال عبر SMS فقط
        )
        
        context.user_data['client'] = client
        context.user_data['phone_code_hash'] = sent.phone_code_hash
        
        if isinstance(update, Update):
            await update.message.reply_text(
                "✅ تم إرسال رمز التحقق إلى حسابك عبر الرسائل النصية (SMS).\n"
                "🔢 أرسل الرمز مع مسافات بين الأرقام (مثال: 1 2 3 4 5):\n"
                "❌ للإلغاء: /cancel"
            )
        else:
            await update.edit_message_text(
                "✅ تم إرسال رمز التحقق إلى حسابك عبر الرسائل النصية (SMS).\n"
                "🔢 أرسل الرمز مع مسافات بين الأرقام (مثال: 1 2 3 4 5):\n"
                "❌ للإلغاء: /cancel"
            )
        
        return ADD_ACCOUNT_CODE
    except Exception as e:
        logger.error(f"Verification error: {e}")
        error_msg = f"❌ حدث خطأ: {str(e)}"
        
        # حل بديل باستخدام Telegram API مباشرة
        try:
            response = requests.post(
                "https://my.telegram.org/auth/send_password",
                data={
                    "phone": phone,
                    "api_id": API_ID,
                    "api_hash": API_HASH
                }
            )
            result = response.json()
            
            if result.get('sent'):
                context.user_data['phone_code_hash'] = result['phone_code_hash']
                error_msg = "✅ تم إرسال رمز التحقق عبر API البديل"
                
                # إضافة تعليمات إدخال الرمز
                error_msg += "\n\nالرجاء إرسال رمز التحقق الآن:"
                
                if isinstance(update, Update):
                    await update.message.reply_text(error_msg)
                else:
                    await update.edit_message_text(error_msg)
                
                return ADD_ACCOUNT_CODE
            else:
                error_msg += "\n❌ فشل الإرسال عبر API البديل"
        except Exception as api_error:
            logger.error(f"API verification error: {api_error}")
            error_msg += f"\n❌ فشل الإرسال عبر API البديل أيضاً: {str(api_error)}"
        
        if isinstance(update, Update):
            await update.message.reply_text(error_msg)
        else:
            await update.edit_message_text(error_msg)
        
        return ConversationHandler.END

async def add_account_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة رمز التحقق"""
    code = update.message.text.strip()
    
    # تجاهل المسافات والفاصلة
    code = code.replace(" ", "").replace(",", "")
    
    # التحقق من صحة الرمز
    if not validate_code(code):
        await update.message.reply_text("❌ رمز التحقق غير صالح. الرجاء إرسال رمز مكون من 5-6 أرقام.")
        return ADD_ACCOUNT_CODE
    
    client = context.user_data.get('client')
    phone = context.user_data.get('phone')
    phone_code_hash = context.user_data.get('phone_code_hash')
    category_name = context.user_data.get('category_name')
    
    if not all([client, phone, phone_code_hash, category_name]):
        await update.message.reply_text("❌ انتهت جلسة التسجيل. الرجاء البدء من جديد.")
        return ConversationHandler.END
    
    try:
        # محاولة تسجيل الدخول
        await client.sign_in(
            phone=phone,
            code=code,
            phone_code_hash=phone_code_hash
        )
    except SessionPasswordNeededError:
        await update.message.reply_text(
            "🔒 هذا الحساب محمي بكلمة مرور ثنائية.\n"
            "🔑 أرسل كلمة المرور الآن:\n"
            "❌ للإلغاء: /cancel"
        )
        return ADD_ACCOUNT_PASSWORD
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ رمز التحقق غير صحيح. الرجاء المحاولة مرة أخرى.")
        return ADD_ACCOUNT_CODE
    except PhoneCodeExpiredError:
        await update.message.reply_text("❌ رمز التحقق منتهي الصلاحية. الرجاء البدء من جديد.")
        await client.disconnect()
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Sign in error: {e}")
        await update.message.reply_text(f"❌ فشل تسجيل الدخول: {str(e)}")
        await client.disconnect()
        return ConversationHandler.END
    
    # تسجيل الدخول ناجح
    return await finalize_account_registration(update, context, client)

async def add_account_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة كلمة المرور الثنائية"""
    password = update.message.text.strip()
    client = context.user_data.get('client')
    
    if not client:
        await update.message.reply_text("❌ انتهت جلسة التسجيل. الرجاء البدء من جديد.")
        return ConversationHandler.END
    
    try:
        # تسجيل الدخول بكلمة المرور
        await client.sign_in(password=password)
        return await finalize_account_registration(update, context, client)
    except Exception as e:
        logger.error(f"2FA error: {e}")
        await update.message.reply_text(f"❌ فشل تسجيل الدخول: {str(e)}")
        await client.disconnect()
        return ConversationHandler.END

async def finalize_account_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, client: TelegramClient) -> int:
    """إكمال عملية تسجيل الحساب مع محاكاة تفاصيل جهاز وتطبيق رسمي"""
    try:
        # 1. جلب معلومات الحساب
        me = await client.get_me()
        phone = context.user_data['phone']
        category_name = context.user_data['category_name']

        # 2. استرجاع بيانات الجهاز من العميل (خزّناها عند الإنشاء)
        device = getattr(client, '_device_attrs', None)
        if not device:
            device = get_random_device()

        # 3. اسم التطبيق الذي سيظهر في التنبيه
        app_name = device.get('app_name', 'Telegram')

        # 4. استخراج معرف الفئة من قاعدة البيانات
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM categories WHERE name = ?", (category_name,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("الفئة غير موجودة")
            category_id = row[0]

        # 5. حفظ جلسة Telethon مشفّرة
        session_str = client.session.save()
        encrypted_session = encrypt_session(session_str)

        # 6. إنشاء معرف فريد للمستخدم
        account_id = str(uuid.uuid4())

        # 7. تحضير معلومات الجهاز والتطبيق للحفظ
        device_info = {
            'app_name': app_name,
            'app_version': device['app_version'],
            'device_model': device['device_model'],
            'system_version': device['system_version']
        }

        # 8. إدخال السجل إلى قاعدة البيانات
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO accounts (id, category_id, username, session_str, phone, device_info) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    account_id,
                    category_id,
                    me.username or None,
                    encrypted_session,
                    phone,
                    str(device_info)
                )
            )
            conn.commit()

        # 9. إرسال رسالة تأكيد للمستخدم
        await update.message.reply_text(
            f"✅ تم تسجيل الحساب بنجاح في فئة '{category_name}'!\n\n"
            f"📱 الهاتف: {phone}\n"
            f"👤 المستخدم: @{me.username or 'غير معروف'}\n"
            f"📁 الفئة: {category_name}\n"
            f"📲 التطبيق: {app_name} {device['app_version']}\n"
            f"📱 الجهاز: {device['device_model']}\n"
            f"⚙️ النظام: {device['system_version']}"
        )
    except Exception as e:
        logger.error(f"Finalization error: {e}")
        await update.message.reply_text(f"❌ حدث خطأ أثناء حفظ الحساب: {e}")
    finally:
        # 10. تنظيف الجلسة وبيانات المستخدم
        try:
            await client.disconnect()
        except:
            pass
        context.user_data.clear()

    # 11. العودة للقائمة الرئيسية
    return await start(update, context)

# === دوال معالجة اختيار الفئات ===
async def view_category_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار الفئة لعرض الحسابات"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("تم الإلغاء.")
        return await start_from_query(query, context)
    
    if query.data.startswith("view_category_"):
        category_id = query.data.split("_")[2]
        context.user_data['view_category_id'] = category_id
        
        # الحصول على اسم الفئة
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM categories WHERE id = ?", (category_id,))
            category_name = cursor.fetchone()[0]
        
        # عرض الحسابات في الفئة
        keyboard = get_accounts_keyboard(
            category_id, 
            0, 
            "view_account",
            page_size=VIEW_PAGE_SIZE
        )
        if not keyboard:
            await query.edit_message_text(f"❌ لا توجد حسابات في فئة '{category_name}'.")
            return await start_from_query(query, context)
        
        await query.edit_message_text(
            f"📁 فئة: {category_name}\n"
            f"📋 الحسابات (الصفحة 1):",
            reply_markup=keyboard
        )
        return VIEW_ACCOUNTS

async def delete_category_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار الفئة لحذف حساب منها"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("تم الإلغاء.")
        return await start_from_query(query, context)
    
    if query.data.startswith("delete_category_"):
        category_id = query.data.split("_")[2]
        context.user_data['delete_category_id'] = category_id
        
        # الحصول على اسم الفئة
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM categories WHERE id = ?", (category_id,))
            category_name = cursor.fetchone()[0]
        
        # عرض الحسابات في الفئة
        keyboard = get_accounts_keyboard(category_id, 0, "delete_account")
        if not keyboard:
            await query.edit_message_text(f"❌ لا توجد حسابات في فئة '{category_name}'.")
            return await start_from_query(query, context)
        
        context.user_data['delete_page'] = 0
        await query.edit_message_text(
            f"📁 فئة: {category_name}\n"
            f"📋 اختر الحساب لحذفه:",
            reply_markup=keyboard
        )
        return DELETE_ACCOUNT_SELECT

async def check_category_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار الفئة لفحص الحسابات"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("تم الإلغاء.")
        return await start_from_query(query, context)
    
    if query.data.startswith("check_category_"):
        category_id = query.data.split("_")[2]
        context.user_data['check_category_id'] = category_id
        
        # الحصول على اسم الفئة
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM categories WHERE id = ?", (category_id,))
            category_name = cursor.fetchone()[0]
        
        # عرض خيارات الفحص
        keyboard = [
            [InlineKeyboardButton("بدء فحص الحسابات", callback_data="start_accounts_check")],
            [InlineKeyboardButton("رجوع", callback_data="back_to_check_categories")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🔍 اخترت فئة: {category_name}\n"
            "اضغط على 'بدء فحص الحسابات' لبدء عملية الفحص التلقائي",
            reply_markup=reply_markup
        )
        return CHECK_ACCOUNT_SELECT

async def storage_category_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار الفئة لنقل الحسابات للتخزين"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("تم الإلغاء.")
        return await start_from_query(query, context)
    
    if query.data.startswith("storage_category_"):
        category_id = query.data.split("_")[2]
        context.user_data['storage_category_id'] = category_id
        
        # عرض الحسابات في الفئة
        keyboard = get_accounts_keyboard(category_id, 0, "storage_account")
        if not keyboard:
            await query.edit_message_text("❌ لا توجد حسابات في هذه الفئة.")
            return await start_from_query(query, context)
        
        await query.edit_message_text(
            "📋 اختر الحساب لنقله إلى فئة التخزين:",
            reply_markup=keyboard
        )
        return STORAGE_ACCOUNT_SELECT

# === دوال معالجة اختيار الحسابات للتخزين ===
async def storage_account_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار حساب لنقله للتخزين"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("تم الإلغاء.")
        return await start_from_query(query, context)
    
    if query.data == "back_categories":
        keyboard = get_categories_keyboard(action="storage", only_non_empty=True)
        await query.edit_message_text(
            "📁 اختر الفئة التي تحتوي على الحساب المراد نقله للتخزين:",
            reply_markup=keyboard
        )
        return STORAGE_CATEGORY_SELECT
    
    if query.data.startswith("storage_account_"):
        account_id = query.data.split("_")[2]
        
        # الحصول على فئة التخزين
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM categories WHERE name = ?", ("حسابات التخزين",))
            storage_category_id = cursor.fetchone()[0]
            
            # نقل الحساب إلى فئة التخزين
            cursor.execute("""
                UPDATE accounts 
                SET category_id = ? 
                WHERE id = ?
            """, (storage_category_id, account_id))
            conn.commit()
            
            # الحصول على تفاصيل الحساب
            cursor.execute("SELECT phone, username FROM accounts WHERE id = ?", (account_id,))
            phone, username = cursor.fetchone()
            display_text = f"{phone}"
            if username:
                display_text += f" (@{username})"
        
        await query.answer(f"✅ تم نقل الحساب {display_text} إلى التخزين", show_alert=True)
        await query.edit_message_text(
            f"✅ تم نقل الحساب {display_text} إلى فئة التخزين بنجاح!"
        )
        return await start_from_query(query, context)
    
    # معالجة التنقل بين الصفحات
    if query.data.startswith("prev_") or query.data.startswith("next_"):
        page = int(query.data.split("_")[1])
        category_id = context.user_data['storage_category_id']
        keyboard = get_accounts_keyboard(category_id, page, "storage_account")
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return STORAGE_ACCOUNT_SELECT

# === دالة بدء فحص الحسابات ===
async def start_accounts_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بدء عملية فحص الحسابات"""
    query = update.callback_query
    await query.answer()
    
    category_id = context.user_data['check_category_id']
    
    # الحصول على حسابات الفئة
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, phone, session_str, device_info 
            FROM accounts 
            WHERE category_id = ?
        """, (category_id,))
        accounts = cursor.fetchall()
    
    if not accounts:
        await query.edit_message_text("❌ لا توجد حسابات في هذه الفئة.")
        return await start_from_query(query, context)
    
    # تهيئة بيانات الفحص
    context.user_data['check_accounts'] = accounts
    context.user_data['check_results'] = []
    context.user_data['current_check_index'] = 0
    
    # إرسال رسالة التجهيز
    await query.edit_message_text("⏳ جارِ بدء عملية الفحص...")
    
    # بدء الفحص
    return await check_next_account(update, context)
    
# ========== دوال الفحص والتحقق ==========
async def check_account_restrictions(client):
    """فحص قيود الحساب باستخدام بوت SpamBot"""
    try:
        # إرسال رسالة إلى بوت SpamBot
        await client.send_message('SpamBot', '/start')
        
        # الانتظار للحصول على الرد
        await asyncio.sleep(2)
        
        # الحصول على آخر رسالة من SpamBot
        messages = await client.get_messages('SpamBot', limit=1)
        if messages and messages[0].text:
            return messages[0].text
        return "❌ لم يتم الحصول على معلومات القيود"
    except Exception as e:
        logger.error(f"SpamBot error: {e}")
        return f"❌ حدث خطأ أثناء فحص القيود: {str(e)}"

async def check_next_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """فحص الحساب التالي في القائمة"""
    accounts = context.user_data['check_accounts']
    index = context.user_data['current_check_index']
    
    if index >= len(accounts):
        # انتهاء الفحص
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ تم الانتهاء من فحص جميع الحسابات بنجاح!"
        )
        return CHECK_ACCOUNT_SELECT
    
    account_id, phone, session_str, device_info = accounts[index]
    device_info = eval(device_info) if device_info else {}
    
    # فحص الحساب
    try:
        async with TelegramClient(
            StringSession(decrypt_session(session_str)), 
            API_ID, 
            API_HASH,
            device_model=device_info.get('device_model', 'Unknown'),
            system_version=device_info.get('system_version', 'Unknown'),
            timeout=SESSION_TIMEOUT
        ) as client:
            await client.connect()
            
            # الحصول على معلومات الحساب
            me = await client.get_me()
            
            # فحص القيود
            restrictions = await check_account_restrictions(client)
            
            # تحديث وقت آخر استخدام
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    UPDATE accounts 
                    SET last_used = ? 
                    WHERE id = ?
                """, (datetime.now().isoformat(), account_id))
                conn.commit()
            
            # تحديد حالة الحساب
            is_restricted = "لا يمكنك إرسال الرسائل" in restrictions
            status = "✅" if me else "❌"
            status_text = f"{status} {'مقيد' if is_restricted else 'غير مقيد'}"
            
            # تخزين النتائج
            context.user_data['check_results'].append({
                'account_id': account_id,
                'phone': phone,
                'status': status,
                'status_text': status_text,
                'restrictions': restrictions,
                'username': me.username if me else None,
                'user_id': me.id if me else None,
                'error': None
            })
            
    except Exception as e:
        # تخزين الخطأ
        context.user_data['check_results'].append({
            'account_id': account_id,
            'phone': phone,
            'status': "❌",
            'status_text': f"❌ خطأ: {str(e)[:30]}",
            'restrictions': None,
            'username': None,
            'user_id': None,
            'error': str(e)
        })
    
    # تحديث رسالة الحالة
    await update_check_status_message(update, context)
    
    # الانتقال للحساب التالي
    context.user_data['current_check_index'] += 1
    return await check_next_account(update, context)

async def update_check_status_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحديث رسالة حالة الفحص"""
    results = context.user_data['check_results']
    category_id = context.user_data['check_category_id']
    
    # إنشاء لوحة مفاتيح لعرض النتائج
    keyboard = []
    for result in results:
        keyboard.append([InlineKeyboardButton(
            f"{result['phone']}: {result['status_text']}", 
            callback_data=f"account_detail_{result['account_id']}"
        )])
    
    # إضافة زر الرجوع
    keyboard.append([InlineKeyboardButton("رجوع", callback_data="back_to_check_start")])
    
    # إنشاء أو تحديث الرسالة
    message_text = "🔍 نتائج فحص الحسابات:\n"
    if context.user_data.get('status_message'):
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['status_message'].message_id,
                text=message_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except:
            # إذا فشل التحديث، إعادة إنشاء الرسالة
            new_message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=message_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data['status_message'] = new_message
    else:
        new_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['status_message'] = new_message

# ========== دوال مساعدة إضافية ==========
async def show_account_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """عرض تفاصيل حساب معين بعد الفحص"""
    query = update.callback_query
    await query.answer()
    
    account_id = query.data.split("_")[2]
    
    # البحث عن نتيجة الفحص لهذا الحساب
    account_result = None
    for result in context.user_data['check_results']:
        if result['account_id'] == account_id:
            account_result = result
            break
    
    if not account_result:
        await query.answer("❌ لم يتم العثور على تفاصيل هذا الحساب", show_alert=True)
        return CHECK_ACCOUNTS_IN_PROGRESS
    
    # إنشاء رسالة التفاصيل
    if account_result['error']:
        message_text = (
            f"📱 الهاتف: {account_result['phone']}\n"
            f"❌ حالة الحساب: غير نشط\n"
            f"⚠️ الخطأ: {account_result['error']}\n\n"
            "هذا الحساب غير قابل للاستخدام بسبب الخطأ أعلاه."
        )
    else:
        # استخدام نفس منطق القيود كما في الفحص الأولي
        is_restricted = "لا يمكنك إرسال الرسائل" in account_result['restrictions']
        username = account_result['username'] or 'غير معروف'
        user_id = account_result['user_id'] or 'غير معروف'
        
        message_text = (
            f"📱 الهاتف: {account_result['phone']}\n"
            f"👤 المستخدم: @{username}\n"
            f"🆔 ID: {user_id}\n"
            f"🔒 حالة القيود: {'مقيد' if is_restricted else 'غير مقيد'}\n\n"
            f"📝 تفاصيل القيود:\n{account_result['restrictions']}"
        )
    
    # إنشاء لوحة مفاتيح للتحكم
    keyboard = [
        [
            InlineKeyboardButton("🗑️ حذف الحساب", callback_data=f"delete_{account_id}"),
            InlineKeyboardButton("🔄 إعادة فحص", callback_data=f"recheck_{account_id}")
        ],
        [InlineKeyboardButton("🔙 رجوع إلى نتائج الفحص", callback_data="back_to_check_results")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # إرسال الرسالة
    await query.edit_message_text(
        message_text,
        reply_markup=reply_markup
    )
    
    return CHECK_ACCOUNT_DETAILS

async def recheck_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إعادة فحص حساب معين"""
    query = update.callback_query
    await query.answer()
    
    account_id = query.data.split("_")[1]
    
    # البحث عن الحساب في قاعدة البيانات
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT phone, session_str, device_info 
            FROM accounts 
            WHERE id = ?
        """, (account_id,))
        account = cursor.fetchone()
    
    if not account:
        await query.answer("❌ لم يتم العثور على الحساب", show_alert=True)
        return CHECK_ACCOUNT_DETAILS
    
    phone, session_str, device_info = account
    device_info = eval(device_info) if device_info else {}
    
    # إعادة الفحص
    try:
        async with TelegramClient(
            StringSession(decrypt_session(session_str)), 
            API_ID, 
            API_HASH,
            device_model=device_info.get('device_model', 'Unknown'),
            system_version=device_info.get('system_version', 'Unknown'),
            timeout=SESSION_TIMEOUT
        ) as client:
            await client.connect()
            
            # الحصول على معلومات الحساب
            me = await client.get_me()
            
            # فحص القيود
            restrictions = await check_account_restrictions(client)
            
            # تحديث وقت آخر استخدام
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    UPDATE accounts 
                    SET last_used = ? 
                    WHERE id = ?
                """, (datetime.now().isoformat(), account_id))
                conn.commit()
            
            # تحديد حالة الحساب
            is_restricted = "لا يمكنك إرسال الرسائل" in restrictions
            status = "✅" if me else "❌"
            status_text = f"{status} {'مقيد' if is_restricted else 'غير مقيد'}"
            
            # تحديث النتائج
            for result in context.user_data['check_results']:
                if result['account_id'] == account_id:
                    result.update({
                        'status': status,
                        'status_text': status_text,
                        'restrictions': restrictions,
                        'username': me.username if me else None,
                        'user_id': me.id if me else None,
                        'error': None
                    })
                    break
    
    except Exception as e:
        # تحديث النتائج بالخطأ
        for result in context.user_data['check_results']:
            if result['account_id'] == account_id:
                result.update({
                    'status': "❌",
                    'status_text': f"❌ خطأ: {str(e)[:30]}",
                    'restrictions': None,
                    'username': None,
                    'user_id': None,
                    'error': str(e)
                })
                break
    
    # تحديث رسالة الحالة
    await update_check_status_message(update, context)
    
    # إعادة عرض التفاصيل
    return await show_account_details(update, context)

async def delete_account_after_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """حذف حساب بعد الفحص"""
    query = update.callback_query
    await query.answer()
    
    account_id = query.data.split("_")[1]
    
    # حذف الحساب من قاعدة البيانات
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT phone, username FROM accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        phone = row[0] if row else "غير معروف"
        username = row[1] if row and len(row) > 1 else None
        
        display_text = phone
        if username:
            display_text += f" (@{username})"
            
        cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        conn.commit()
    
    # إزالة النتيجة من القائمة
    context.user_data['check_results'] = [
        r for r in context.user_data['check_results'] 
        if r['account_id'] != account_id
    ]
    
    # تحديث رسالة الحالة
    await update_check_status_message(update, context)
    
    await query.answer(f"✅ تم حذف الحساب {display_text}", show_alert=True)
    return await show_account_details(update, context)

# ========== دوال الرجوع والإلغاء ==========
async def back_to_check_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """العودة إلى بداية فحص الحسابات"""
    query = update.callback_query
    await query.answer()
    
    category_id = context.user_data['check_category_id']
    
    # الحصول على اسم الفئة
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM categories WHERE id = ?", (category_id,))
        category_name = cursor.fetchone()[0]
    
    # عرض زرين: بدء الفحص والرجوع
    keyboard = [
        [InlineKeyboardButton("بدء فحص الحسابات", callback_data="start_accounts_check")],
        [InlineKeyboardButton("رجوع إلى قائمة الفئات", callback_data="back_to_check_categories")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🔍 اخترت فئة: {category_name}\n"
        "اضغط على 'بدء فحص الحسابات' لبدء عملية الفحص التلقائي",
        reply_markup=reply_markup
    )
    return CHECK_ACCOUNT_SELECT

async def back_to_check_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """العودة إلى قائمة الفئات للفحص"""
    query = update.callback_query
    await query.answer()
    
    # تنظيف بيانات المستخدم
    context.user_data.pop('check_results', None)
    context.user_data.pop('check_accounts', None)
    
    keyboard = get_categories_keyboard(action="check", only_non_empty=True)
    await query.edit_message_text(
        "📁 اختر الفئة لفحص حساباتها:",
        reply_markup=keyboard
    )
    return CHECK_CATEGORY_SELECT

async def back_to_check_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """العودة إلى نتائج الفحص"""
    query = update.callback_query
    await query.answer()
    
    # تحديث رسالة الحالة
    await update_check_status_message(update, context)
    return CHECK_ACCOUNTS_IN_PROGRESS

async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إلغاء العملية الحالية والعودة للقائمة الرئيسية"""
    # تنظيف بيانات المستخدم
    context.user_data.clear()
    
    await update.message.reply_text(
        "تم إلغاء العملية.",
        reply_markup=ReplyKeyboardRemove()
    )
    return await start(update, context)

# ========== حذف حساب مع تأكيد ==========
async def delete_account_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    category_id = context.user_data['delete_category_id']
    
    if query.data == "cancel":
        await query.edit_message_text("تم الإلغاء.")
        return await start_from_query(query, context)
    
    if query.data == "back_categories":
        keyboard = get_categories_keyboard(action="delete", only_non_empty=True)
        await query.edit_message_text(
            "📁 اختر الفئة التي تحتوي على الحساب الذي تريد حذفه:",
            reply_markup=keyboard
        )
        return DELETE_CATEGORY_SELECT
    
    if query.data.startswith("delete_account_"):
        account_id = query.data.split("_")[2]
        context.user_data['delete_account_id'] = account_id
        
        # الحصول على تفاصيل الحساب
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT phone, username FROM accounts WHERE id = ?", (account_id,))
            phone, username = cursor.fetchone()
            display_text = f"{phone}"
            if username:
                display_text += f" (@{username})"
        
        # إنشاء لوحة مفاتيح التأكيد
        keyboard = [
            [InlineKeyboardButton("✅ نعم، تأكيد الحذف", callback_data="confirm_delete")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_delete")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⚠️ هل أنت متأكد من حذف الحساب:\n{display_text}؟",
            reply_markup=reply_markup
        )
        return DELETE_ACCOUNT_CONFIRM
    
    # معالجة التنقل بين الصفحات
    if query.data.startswith("prev_") or query.data.startswith("next_"):
        page = int(query.data.split("_")[1])
        context.user_data['delete_page'] = page
        
        keyboard = get_accounts_keyboard(category_id, page, "delete_account")
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return DELETE_ACCOUNT_SELECT
    
    return DELETE_ACCOUNT_SELECT

async def delete_account_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_delete":
        account_id = context.user_data['delete_account_id']
        
        # الحصول على رقم الهاتف قبل الحذف
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT phone, username FROM accounts WHERE id = ?", (account_id,))
            phone, username = cursor.fetchone()
            display_text = f"{phone}"
            if username:
                display_text += f" (@{username})"
            
            # حذف الحساب
            cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            conn.commit()
        
        await query.edit_message_text(f"✅ تم حذف الحساب ({display_text}) بنجاح.")
        return await start_from_query(query, context)
    
    elif query.data == "cancel_delete":
        # العودة إلى قائمة الحسابات
        category_id = context.user_data['delete_category_id']
        page = context.user_data.get('delete_page', 0)
        keyboard = get_accounts_keyboard(category_id, page, "delete_account")
        await query.edit_message_text(
            f"📋 اختر الحساب لحذفه:",
            reply_markup=keyboard
        )
        return DELETE_ACCOUNT_SELECT

# ========== عرض الحسابات بتنسيق محسن ==========
async def view_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    category_id = context.user_data['view_category_id']
    
    if query.data == "cancel":
        await query.edit_message_text("تم الإلغاء.")
        return await start_from_query(query, context)
    
    if query.data == "back_categories":
        keyboard = get_categories_keyboard(action="view", only_non_empty=True)
        await query.edit_message_text(
            "📁 اختر الفئة لعرض حساباتها:",
            reply_markup=keyboard
        )
        return VIEW_CATEGORY_SELECT
    
    if query.data.startswith("view_account_"):
        account_id = query.data.split("_")[2]
        
        # الحصول على تفاصيل الحساب
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.phone, a.username, c.name, a.device_info
                FROM accounts a
                JOIN categories c ON a.category_id = c.id
                WHERE a.id = ?
            """, (account_id,))
            phone, username, category_name, device_info = cursor.fetchone()
            device_info = eval(device_info) if device_info else {}
        
        message = (
            f"📋 تفاصيل الحساب:\n\n"
            f"📱 الهاتف: {phone}\n"
            f"👤 المستخدم: @{username or 'غير معروف'}\n"
            f"📁 الفئة: {category_name}\n"
            f"📱 الجهاز: {device_info.get('device_model', 'غير معروف')}\n"
            f"⚙️ النظام: {device_info.get('system_version', 'غير معروف')}\n"
            f"📲 التطبيق: {device_info.get('app_name', 'غير معروف')} {device_info.get('app_version', '')}"
        )
        
        await query.edit_message_text(message)
        return VIEW_ACCOUNTS
    
    # معالجة التنقل بين الصفحات
    if query.data.startswith("prev_") or query.data.startswith("next_"):
        page = int(query.data.split("_")[1])
        context.user_data['view_page'] = page
        
        # استخدام حجم صفحة مخصص للعرض (50 عنصر)
        keyboard = get_accounts_keyboard(
            category_id, 
            page, 
            "view_account",
            page_size=VIEW_PAGE_SIZE
        )
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return VIEW_ACCOUNTS

# ========== دوال مساعدة محسنة ==========
async def start_from_query(query, context):
    """بدء المحادثة من استعلام إنلاین"""
    await query.edit_message_text("العودة للقائمة الرئيسية...")
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="اختر أحد الخيارات:",
        reply_markup=ReplyKeyboardMarkup([
            ["➕ اضافه الحسابات"],
            ["👁️ عرض الحسابات"],
            ["🗑️ حذف حساب"],
            ["🔍 فحص الحسابات"],
            ["📦 حسابات التخزين"]  
        ], resize_keyboard=True)
    )
    return MAIN_MENU

async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إلغاء العملية الحالية والعودة للقائمة الرئيسية"""
    # تنظيف بيانات المستخدم
    context.user_data.clear()
    
    await update.message.reply_text(
        "تم إلغاء العملية.",
        reply_markup=ReplyKeyboardRemove()
    )
    return await start(update, context)

# ========== تشغيل البوت ==========
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # إعداد معالج المحادثة
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu)
            ],
            ADD_ACCOUNT_METHOD: [
                CallbackQueryHandler(add_account_method)
            ],
            ADD_ACCOUNT_SESSION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_session),
                CommandHandler('cancel', cancel_operation)
            ],
            ADD_ACCOUNT_CATEGORY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_category),
                CommandHandler('cancel', cancel_operation)
            ],
            ADD_ACCOUNT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_phone),
                CommandHandler('cancel', cancel_operation)
            ],
            ADD_ACCOUNT_PHONE_HANDLE_EXISTING: [
                CallbackQueryHandler(handle_existing_account)
            ],
            ADD_ACCOUNT_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_code),
                CommandHandler('cancel', cancel_operation)
            ],
            ADD_ACCOUNT_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_password),
                CommandHandler('cancel', cancel_operation)
            ],
            VIEW_CATEGORY_SELECT: [
                CallbackQueryHandler(view_category_select)
            ],
            VIEW_ACCOUNTS: [
                CallbackQueryHandler(view_accounts)
            ],
            DELETE_CATEGORY_SELECT: [
                CallbackQueryHandler(delete_category_select)
            ],
            DELETE_ACCOUNT_SELECT: [
                CallbackQueryHandler(delete_account_select)
            ],
            DELETE_ACCOUNT_CONFIRM: [
                CallbackQueryHandler(delete_account_confirm)
            ],
            CHECK_CATEGORY_SELECT: [
                CallbackQueryHandler(check_category_select)
            ],
            CHECK_ACCOUNT_SELECT: [
                CallbackQueryHandler(start_accounts_check),
                CallbackQueryHandler(back_to_check_categories, pattern="back_to_check_categories")
            ],
            CHECK_ACCOUNTS_IN_PROGRESS: [
                CallbackQueryHandler(show_account_details, pattern="account_detail_"),
                CallbackQueryHandler(back_to_check_start, pattern="back_to_check_start")
            ],
            CHECK_ACCOUNT_DETAILS: [
                CallbackQueryHandler(delete_account_after_check, pattern="delete_"),
                CallbackQueryHandler(recheck_account, pattern="recheck_"),
                CallbackQueryHandler(back_to_check_results, pattern="back_to_check_results")
            ],
            STORAGE_CATEGORY_SELECT: [
                CallbackQueryHandler(storage_category_select)
            ],
            STORAGE_ACCOUNT_SELECT: [
                CallbackQueryHandler(storage_account_select)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_operation)],
        allow_reentry=True
    )

    app.add_handler(conv_handler)
    
    # تشغيل البوت
    logger.info("Starting bot...")
    app.run_polling()

if __name__ == '__main__':
    main()