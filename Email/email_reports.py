# DrKhayal/Email/email_reports.py

import os
import json
import logging
import re
import smtplib
import traceback
from threading import Thread, Lock
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from time import sleep

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# --- استيراد الإعدادات من ملف config.py الرئيسي ---
try:
    from config import OWNER_ID
except ImportError:
    logging.error("خطأ: لا يمكن استيراد OWNER_ID من config.py.")
    # استخدام قيمة افتراضية لتجنب التعطل، لكن يجب إصلاح الاستيراد
    OWNER_ID = 0

# إعداد بريد المالك للاختبار
OWNER_EMAIL = "test@example.com"  # يجب تحديث هذا ببريد المالك الفعلي

# --- تعريف الثوابت والمتغيرات الخاصة بوحدة الإيميل ---
logger = logging.getLogger(__name__)

# تحديد المسارات بناءً على موقع الملف الحالي
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
EMAILS_FILE = os.path.join(CURRENT_DIR, '..', 'emails.json') # افترض أن الملف في المجلد الرئيسي
TEMP_DIR = os.path.join(CURRENT_DIR, '..', 'temp_attachments')
os.makedirs(TEMP_DIR, exist_ok=True)

FILE_LOCK = Lock()
EMAIL_REGEX = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'

# -------------- تهيئة التخزين --------------
def initialize_storage():
    try:
        with FILE_LOCK:
            if not os.path.exists(EMAILS_FILE):
                with open(EMAILS_FILE, 'w', encoding='utf-8') as f:
                    json.dump([], f)
                os.chmod(EMAILS_FILE, 0o666)
                logging.info(f"Created new file: {EMAILS_FILE}")
                
    except Exception:
        logging.critical(f"Storage init failed: {traceback.format_exc()}")
        raise

initialize_storage()

# ------------ Conversation states ------------
GET_NUMBER = 1
GET_EMAILS = 2
GET_SUBJECT = 3
GET_BODY = 4
GET_ATTACHMENTS = 5
GET_DELAY = 6
CONFIRM = 7
ADD_EMAILS = 8
DELETE_EMAIL = 9

# زر الرجوع العام
BACK_BUTTON = InlineKeyboardButton('رجوع', callback_data='back')

# دالة للاستجابة للمستخدمين غير المصرح لهم
async def unauthorized_response(message, is_callback=False):
    text = "❌ ليس مصرحاً لك باستخدام هذا الأمر."
    if is_callback:
        await message.reply_text(text)
    else:
        await message.reply_text(text)


# -------------- Helpers --------------
def load_email_accounts():
    try:
        with FILE_LOCK, open(EMAILS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        logger.error(f"Failed loading emails: {traceback.format_exc()}")
        return []

def save_email_accounts(accounts):
    try:
        with FILE_LOCK, open(EMAILS_FILE, 'w', encoding='utf-8') as f:
            json.dump(accounts, f, indent=2, ensure_ascii=False)
        os.chmod(EMAILS_FILE, 0o666)
    except Exception:
        logger.error(f"Failed saving emails: {traceback.format_exc()}")
        raise

# -------------- SMTP Client --------------

class SMTPClient:
    def __init__(self, email, password, targets, count, subject, body, attachments, delay):
        self.email = email
        self.password = password
        self.targets = targets
        self.count = count
        self.subject = subject
        self.body = body
        self.attachments = attachments or []
        self.delay = delay

    def verify(self):
        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(self.email, self.password)
            server.quit()
            return True
        except Exception as e:
            logger.error(f"SMTP verify failed: {e}")
            return False

    def send_emails(self):
        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(self.email, self.password)
            for _ in range(self.count):
                msg = MIMEMultipart()
                msg['From'] = self.email
                msg['To'] = ', '.join(self.targets)
                msg['Subject'] = self.subject
                msg.attach(MIMEText(self.body, 'plain'))
                for path in self.attachments:
                    if os.path.exists(path):
                        part = MIMEBase('application', 'octet-stream')
                        with open(path, 'rb') as f:
                            part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(path)}"')
                        msg.attach(part)
                server.sendmail(self.email, self.targets, msg.as_string())
                sleep(self.delay)
            server.quit()
            return True
        except Exception as e:
            logger.error(f"SMTP send failed: {e}")
            return False
        finally:
            for p in self.attachments:
                try: os.remove(p)
                except: pass
 
async def start_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id
    await update.callback_query.answer()
    if user_id != OWNER_ID:
        await unauthorized_response(update.callback_query.message, is_callback=True)
        return
    kb = [[InlineKeyboardButton('بدء الرفع الخارجي', callback_data='external_upload')],
          [InlineKeyboardButton('إدارة الإيميلات', callback_data='manage_emails')]]
    await update.callback_query.edit_message_text('قسم بلاغات ايميل:', reply_markup=InlineKeyboardMarkup(kb))

async def back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # إزالة الحالة الحالية من المكدس
    stack = context.user_data.setdefault('state_stack', [])
    if stack:
        stack.pop()
    # إذا لم يبقَ حالة، ننهي المحادثة
    if not stack:
        context.user_data.clear()
        return ConversationHandler.END

    prev_state = stack.pop()
    # الرجوع إلى الشاشة المناسبة حسب الحالة السابقة
    if prev_state == ADD_EMAILS:
        await update.callback_query.edit_message_text(
            '''أرسل الإيميلات وكلمات المرور بالتنسيق:
email@example.com,password
email2@example.com,password2''',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return ADD_EMAILS

    elif prev_state == DELETE_EMAIL:
        await update.callback_query.edit_message_text(
            'أرسل الإيميل المراد حذفه:',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return DELETE_EMAIL

    elif prev_state == GET_NUMBER:
        await update.callback_query.edit_message_text(
            'أدخل عدد الرسائل:',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return GET_NUMBER

    elif prev_state == GET_EMAILS:
        await update.callback_query.edit_message_text(
            'أرسل إيميلات مستهدفة مفصولة بفواصل:',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return GET_EMAILS

    elif prev_state == GET_SUBJECT:
        await update.callback_query.edit_message_text(
            'أرسل الموضوع:',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return GET_SUBJECT

    elif prev_state == GET_BODY:
        await update.callback_query.edit_message_text(
            'أرسل نص الرسالة:',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return GET_BODY

    elif prev_state == GET_ATTACHMENTS:
        kb = [[InlineKeyboardButton('التالي ➡️', callback_data='next')], [BACK_BUTTON]]
        await update.callback_query.edit_message_text(
            'ارسل مرفق أو اضغط التالي:',
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return GET_ATTACHMENTS

    elif prev_state == GET_DELAY:
        await update.callback_query.edit_message_text(
            'أدخل التأخير بين كل إرسال (بالثواني):',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return GET_DELAY

    elif prev_state == CONFIRM:
        # إعادة بناء ملخص الإرسال من بيانات user_data
        summary = (
            f"عدد: {context.user_data.get('count', 0)}\n"
            f"مستفيدين: {len(context.user_data.get('targets', []))}\n"
            f"موضوع: {context.user_data.get('subject', '')}\n"
            f"مرفقات: {len(context.user_data.get('attachments', []))}\n"
            f"تأخير: {context.user_data.get('delay', 0)}ث\nاضغط إرسال."
        )
        kb = [[InlineKeyboardButton('إرسال', callback_data='send')], [BACK_BUTTON]]
        await update.callback_query.edit_message_text(summary,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return CONFIRM

    elif prev_state == 'manage_emails' or prev_state == 'show_emails' or prev_state == 'test_email':
        # العودة إلى قائمة إدارة الإيميلات
        kb = [
            [InlineKeyboardButton('عرض الإيميلات', callback_data='show_emails')],
            [InlineKeyboardButton('إضافة إيميلات', callback_data='add_emails')],
            [InlineKeyboardButton('حذف ايميل', callback_data='delete_email')],
            [BACK_BUTTON]
        ]
        await update.callback_query.edit_message_text('إدارة الإيميلات:',
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return ConversationHandler.END

    elif prev_state == 'start_tg':
        # العودة إلى قائمة أقسام التيليجرام
        keyboard = [
            [InlineKeyboardButton("🏴‍☠ بدء عملية الإبلاغ", callback_data="start_report")],
            [BACK_BUTTON]
        ]
        await update.callback_query.edit_message_text("قسم بلاغات تيليجرام:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    # بخلاف ذلك، ننهي المحادثة كإجراء افتراضي
    return ConversationHandler.END
    
async def manage_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # حفظ الحالة الحالية على المكدس
    context.user_data.setdefault('state_stack', []).append('manage_emails')
    kb = [
        [InlineKeyboardButton('عرض الإيميلات', callback_data='show_emails')],
        [InlineKeyboardButton('إضافة إيميلات', callback_data='add_emails')],
        [InlineKeyboardButton('حذف ايميل', callback_data='delete_email')],
        [BACK_BUTTON]
    ]
    await update.callback_query.edit_message_text('إدارة الإيميلات:',
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def add_emails_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # حفظ الحالة الحالية (إضافة إيميلات) على المكدس
    context.user_data.setdefault('state_stack', []).append(ADD_EMAILS)
    await update.callback_query.edit_message_text(
        '''أرسل الإيميلات وكلمات المرور بالتنسيق:
email@example.com,password
email2@example.com,password2''',
        reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
    )
    return ADD_EMAILS


async def process_add_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw_text = update.message.text
        text = raw_text.strip().replace(' ', '')
        if text.lower() == 'رجوع':
            return await cancel(update, context)

        lines = text.splitlines()
        accounts = load_email_accounts()
        existing_emails = {acc['email'].lower() for acc in accounts}
        
        added = 0
        duplicates = 0
        invalid = 0

        for idx, ln in enumerate(lines, 1):
            if ',' not in ln:
                invalid +=1
                continue

            email, pwd = ln.split(',', 1)
            email = email.strip()
            pwd = pwd.strip()

            if not re.fullmatch(EMAIL_REGEX, email):
                invalid +=1
                continue

            if email.lower() in existing_emails:
                duplicates +=1
                continue

            accounts.append({'email': email, 'password': pwd})
            existing_emails.add(email.lower())
            added +=1

        if added > 0:
            save_email_accounts(accounts)

        msg =f'✅ تمت إضافة {added} إيميلات.'
        if duplicates > 0: msg += f'\n⚠️ تم تخطي {duplicates} إيميلات مكررة.'
        if invalid > 0: msg += f'\n⚠️ تم تخطي {invalid} سطور غير صالحة.'
        
        await update.message.reply_text(msg)
        return ConversationHandler.END

    except Exception as e:
        await update.message.reply_text('❌ حدث خطأ فادح أثناء المعالجة!')
        return ConversationHandler.END

async def delete_email_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # حفظ الحالة الحالية (حذف إيميل) على المكدس
    context.user_data.setdefault('state_stack', []).append(DELETE_EMAIL)
    await update.callback_query.edit_message_text(
        'أرسل الإيميل المراد حذفه:',
        reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
    )
    return DELETE_EMAIL

async def process_delete_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.text.strip().lower()
    if target == 'رجوع':
        return await cancel(update, context)

    accounts = load_email_accounts()
    original_count = len(accounts)
    new_list = [a for a in accounts if a['email'].lower() != target]
    
    if len(new_list) < original_count:
        save_email_accounts(new_list)
        await update.message.reply_text('✅ تم الحذف.')
    else:
        await update.message.reply_text('⚠️ الإيميل غير موجود.')
    
    return ConversationHandler.END

async def external_upload_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id
    await update.callback_query.answer()
    if user_id != OWNER_ID:
        await unauthorized_response(update.callback_query.message, is_callback=True)
        return ConversationHandler.END
    # إعادة تهيئة بيانات المستخدم
    context.user_data.clear()
    # حفظ الحالة الحالية (إدخال العدد) على المكدس
    context.user_data.setdefault('state_stack', []).append(GET_NUMBER)
    await update.callback_query.edit_message_text('أدخل عدد الرسائل:',
        reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
    )
    return GET_NUMBER

async def show_emails_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # حفظ الحالة الحالية (عرض الإيميلات) على المكدس
    context.user_data.setdefault('state_stack', []).append('show_emails')
    accounts = load_email_accounts()
    text = 'لا توجد إيميلات مخزنة.' if not accounts else 'الإيميلات المخزنة:\n' + \
           '\n'.join(f"{i+1}. {acc['email']}" for i, acc in enumerate(accounts))
    await update.callback_query.edit_message_text(text,
        reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
    )

async def get_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == 'رجوع':
        return await cancel(update, context)
    try:
        n = int(update.message.text)
        if n < 1:
            raise ValueError
        context.user_data['count'] = n
        await update.message.reply_text('أرسل إيميلات مستهدفة مفصولة بفواصل:',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        # حفظ الحالة الحالية (إدخال الإيميلات) على المكدس
        context.user_data.setdefault('state_stack', []).append(GET_EMAILS)
        return GET_EMAILS
    except:
        await update.message.reply_text('رقم غير صالح!',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return GET_NUMBER

async def get_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == 'رجوع':
        return await cancel(update, context)
    lst = [e.strip() for e in update.message.text.split(',')]
    if not all(re.fullmatch(EMAIL_REGEX, e) for e in lst):
        await update.message.reply_text('قائمة غير صحيحة!',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return GET_EMAILS
    context.user_data['targets'] = lst
    await update.message.reply_text('أرسل الموضوع:',
        reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
    )
    # حفظ الحالة الحالية (إدخال الموضوع) على المكدس
    context.user_data.setdefault('state_stack', []).append(GET_SUBJECT)
    return GET_SUBJECT

async def get_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == 'رجوع':
        return await cancel(update, context)
    context.user_data['subject'] = update.message.text
    await update.message.reply_text('أرسل نص الرسالة:',
        reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
    )
    # حفظ الحالة الحالية (إدخال نص الرسالة) على المكدس
    context.user_data.setdefault('state_stack', []).append(GET_BODY)
    return GET_BODY

async def get_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == 'رجوع':
        return await cancel(update, context)
    context.user_data['body'] = update.message.text
    kb = [[InlineKeyboardButton('التالي ➡️', callback_data='next')], [BACK_BUTTON]]
    await update.message.reply_text('ارسل مرفق أو اضغط التالي:',
        reply_markup=InlineKeyboardMarkup(kb)
    )
    # حفظ الحالة الحالية (رفع المرفقات) على المكدس
    context.user_data.setdefault('state_stack', []).append(GET_ATTACHMENTS)
    return GET_ATTACHMENTS

async def get_attachments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.strip() == 'رجوع':
        return await cancel(update, context)
    os.makedirs(TEMP_DIR, exist_ok=True)

    if update.message.document:
        file = await update.message.document.get_file()
        name = update.message.document.file_name
    elif update.message.photo:
        file = await update.message.photo[-1].get_file()
        name = f"photo_{file.file_id}.jpg"
    else:
        await update.message.reply_text('نوع المرفق غير مدعوم.',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return GET_ATTACHMENTS

    path = os.path.join(TEMP_DIR, f"{file.file_id}_{name}")
    await file.download_to_drive(path)
    context.user_data.setdefault('attachments', []).append(path)

    kb = [[InlineKeyboardButton('التالي ➡️', callback_data='next')], [BACK_BUTTON]]
    await update.message.reply_text(f'تم رفع {name}',
        reply_markup=InlineKeyboardMarkup(kb)
    )
    # حفظ الحالة الحالية (رفع مرفقات إضافية) على المكدس
    context.user_data.setdefault('state_stack', []).append(GET_ATTACHMENTS)
    return GET_ATTACHMENTS

async def next_step_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data.setdefault('attachments', [])  # إنشاء قائمة فارغة إن لم تكن موجودة
    await update.callback_query.edit_message_text('أدخل التأخير بين كل إرسال (بالثواني):',
        reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
    )
    context.user_data.setdefault('state_stack', []).append(GET_DELAY)
    return GET_DELAY

async def get_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == 'رجوع':
        return await cancel(update, context)
    try:
        d = float(update.message.text)
    except ValueError:
        await update.message.reply_text('تأخير غير صالح!',
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
        )
        return GET_DELAY
    context.user_data['delay'] = d
    summary = (
        f"عدد: {context.user_data['count']}\n"
        f"مستفيدين: {len(context.user_data['targets'])}\n"
        f"موضوع: {context.user_data['subject']}\n"
        f"مرفقات: {len(context.user_data.get('attachments', []))}\n"
        f"تأخير: {d}ث\nاضغط إرسال."
    )
    kb = [[InlineKeyboardButton('إرسال', callback_data='send')], [BACK_BUTTON]]
    await update.message.reply_text(summary,
        reply_markup=InlineKeyboardMarkup(kb)
    )
    # حفظ الحالة الحالية (تأكيد الإرسال) على المكدس
    context.user_data.setdefault('state_stack', []).append(CONFIRM)
    return CONFIRM

async def confirm_send_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await perform_send(update, context)

async def perform_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # قائمة المفاتيح المطلوبة للإرسال (دون المرفقات)
    required_keys = ['count', 'targets', 'subject', 'body', 'delay']
    for key in required_keys:
        if key not in context.user_data:
            await update.callback_query.message.reply_text(f'❌ بيانات ناقصة: {key}')
            return

    # تأكد من وجود المفتاح 'attachments' ولو بقائمة فارغة
    attachments = context.user_data.get('attachments', [])

    msg = update.callback_query.message
    await msg.reply_text('جاري الإرسال...')
    accounts = load_email_accounts()
    threads = []

    for acc in accounts:
        client = SMTPClient(
            email=acc['email'],
            password=acc['password'],
            targets=context.user_data['targets'],
            count=context.user_data['count'],
            subject=context.user_data['subject'],
            body=context.user_data['body'],
            attachments=attachments,
            delay=context.user_data['delay']
        )
        if client.verify():
            t = Thread(target=client.send_emails)
            threads.append(t)
            t.start()

    # انتظر انتهاء جميع خيوط الإرسال
    for t in threads:
        t.join()

    await msg.reply_text(
        '✅ تم الإرسال!',
        reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]])
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # احذف أي مرفقات تم رفعها
    for fp in context.user_data.get('attachments', []):
        try: os.remove(fp)
        except: pass
    # مسح كل البيانات المؤقتة
    context.user_data.clear()
    # نرسل رسالة التثبيط
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text('تم الإلغاء.')
    else:
        await update.message.reply_text('تم الإلغاء.')
    # إعادة رسالة البداية مع الأزرار الرئيسية
    kb = [
        [InlineKeyboardButton('قسم بلاغات ايميل', callback_data='email_reports')],
        [InlineKeyboardButton('قسم بلاغات تيليجرام', callback_data='telegram_reports')]
    ]
    if update.callback_query:
        chat_id = update.callback_query.message.chat_id
        await context.bot.send_message(chat_id=chat_id, text='مرحبًا! اختر القسم:', reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text('مرحبًا! اختر القسم:', reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# Test email feature
async def test_email_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text('ليس مصرحاً لك.')
        return
    accounts = load_email_accounts()
    if not accounts:
        await update.message.reply_text('لا توجد إيميلات مخزنة.')
        return
    # حفظ الحالة الحالية (اختبار الإيميل) على المكدس
    context.user_data.setdefault('state_stack', []).append('test_email')
    keyboard = [[InlineKeyboardButton(acc['email'], callback_data=f'test_email_{acc["email"]}')] 
                for acc in accounts]
    keyboard.append([BACK_BUTTON])
    await update.message.reply_text('اختر إيميلًا لاختباره:',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_test_email_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    email_to_test = update.callback_query.data[len('test_email_'):]
    accounts = load_email_accounts()
    account = next((acc for acc in accounts if acc['email'] == email_to_test), None)
    
    if not account:
        await update.callback_query.message.reply_text('الإيميل غير موجود.')
        return
    
    client = SMTPClient(
        account['email'],
        account['password'],
        [OWNER_EMAIL],
        1,
        'اختبار إيميل',
        'هذا بريد اختباري من البوت',
        [],
        0
    )
    
    if not client.verify():
        await update.callback_query.message.reply_text(f'فشل التحقق من الإيميل {email_to_test}.')
        return
    
    if client.send_emails():
        await update.callback_query.message.reply_text(f'✅ تم إرسال البريد الاختباري إلى {OWNER_EMAIL} باستخدام {email_to_test}.')
    else:
        await update.callback_query.message.reply_text(f'❌ فشل إرسال البريد الاختباري باستخدام {email_to_test}.')

# --- تجميع كل المعالجات في ConversationHandler واحد ---
email_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_email, pattern='^email_reports$'),
        CallbackQueryHandler(manage_emails, pattern='^manage_emails$'),
        CallbackQueryHandler(external_upload_callback, pattern='^external_upload$')
    ],
    states={
        GET_NUMBER: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_number),
            CallbackQueryHandler(back_callback, pattern='^back$')
        ],
        GET_EMAILS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_emails),
            CallbackQueryHandler(back_callback, pattern='^back$')
        ],
        GET_SUBJECT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_subject),
            CallbackQueryHandler(back_callback, pattern='^back$')
        ],
        GET_BODY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_body),
            CallbackQueryHandler(back_callback, pattern='^back$')
        ],
        GET_ATTACHMENTS: [
            MessageHandler(filters.Document.ALL | filters.PHOTO, get_attachments),
            CallbackQueryHandler(next_step_callback, pattern='^next$'),
            CallbackQueryHandler(back_callback, pattern='^back$')
        ],
        GET_DELAY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_delay),
            CallbackQueryHandler(back_callback, pattern='^back$')
        ],
        CONFIRM: [
            CallbackQueryHandler(confirm_send_callback, pattern='^send$'),
            CallbackQueryHandler(back_callback, pattern='^back$')
        ],
        ADD_EMAILS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_emails),
            CallbackQueryHandler(back_callback, pattern='^back$')
        ],
        DELETE_EMAIL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, process_delete_email),
            CallbackQueryHandler(back_callback, pattern='^back$')
        ],
    },
    fallbacks=[
        CallbackQueryHandler(cancel, pattern='^cancel$'),
        CallbackQueryHandler(back_callback, pattern='^back$'),
        MessageHandler(filters.TEXT & filters.Regex('^رجوع$'), cancel),
    ],
    per_user=True,
)

# إضافة معالج للـ callbacks الإضافية
additional_callbacks = [
    CallbackQueryHandler(manage_emails, pattern='^manage_emails$'),
    CallbackQueryHandler(add_emails_callback, pattern='^add_emails$'),
    CallbackQueryHandler(delete_email_callback, pattern='^delete_email$'),
    CallbackQueryHandler(show_emails_callback, pattern='^show_emails$'),
    CallbackQueryHandler(external_upload_callback, pattern='^external_upload$'),
    CallbackQueryHandler(handle_test_email_selection, pattern='^test_email_'),
]