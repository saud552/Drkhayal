#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
سكريبت إصلاح ملف Email/email_reports.py
يقوم بإضافة الدوال المفقودة وإصلاح الأخطاء
"""

import os
import re

def fix_email_reports():
    """إصلاح ملف Email/email_reports.py"""
    
    file_path = "Email/email_reports.py"
    
    if not os.path.exists(file_path):
        print(f"❌ الملف {file_path} غير موجود!")
        return False
    
    print(f"🔧 بدء إصلاح ملف {file_path}...")
    
    # قراءة محتوى الملف
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # إضافة الثوابت المفقودة
    constants_to_add = """# ------------ Conversation states ------------
EMAIL_MENU = 0
MANAGE_MENU = 10
GET_NUMBER = 1
GET_EMAILS = 2
GET_SUBJECT = 3
GET_BODY = 4
GET_ATTACHMENTS = 5
GET_DELAY = 6
CONFIRM = 7
ADD_EMAILS = 8
DELETE_EMAIL = 9
PROCESS_ADD_EMAILS = 11
PROCESS_DELETE_EMAIL = 12"""
    
    # البحث عن قسم الثوابت وتحديثه
    constants_pattern = r'# ------------ Conversation states ------------\n.*?(?=\n\n|\n#|$)'
    if re.search(constants_pattern, content, re.DOTALL):
        content = re.sub(constants_pattern, constants_to_add, content, flags=re.DOTALL)
        print("✅ تم تحديث الثوابت")
    else:
        # إضافة الثوابت بعد initialize_storage()
        init_pattern = r'initialize_storage\(\)\s*\n'
        content = re.sub(init_pattern, f'initialize_storage()\n\n{constants_to_add}\n\n', content)
        print("✅ تم إضافة الثوابت")
    
    # إضافة الدوال المساعدة المفقودة
    helper_functions = '''
# --- دوال مساعدة للقوائم ---
async def back_to_email_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """العودة للقائمة الرئيسية للبريد الإلكتروني"""
    await update.callback_query.answer()
    return await start_email(update, context)

async def manage_emails_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة إدارة حسابات البريد الإلكتروني"""
    await update.callback_query.answer()
    return await manage_emails(update, context)

async def start_campaign_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء تدفق الحملة"""
    await update.callback_query.answer()
    return await get_number(update, context)

async def ask_add_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب إضافة حسابات بريد إلكتروني"""
    await update.callback_query.answer()
    return await add_emails_callback(update, context)

async def ask_delete_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب حذف حساب بريد إلكتروني"""
    await update.callback_query.answer()
    return await delete_email_callback(update, context)

async def show_emails_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة حسابات البريد الإلكتروني"""
    await update.callback_query.answer()
    return await show_emails_callback(update, context)

# --- دوال معالجة الملفات والمرفقات ---
async def get_attachments_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة رفع المرفقات"""
    return await get_attachments(update, context)

async def ask_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب تحديد التأخير"""
    await update.callback_query.answer()
    return await get_delay(update, context)

async def get_delay_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال التأخير"""
    return await get_delay(update, context)

async def confirm_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تأكيد وإرسال الحملة"""
    await update.callback_query.answer()
    return await confirm_send_callback(update, context)

'''
    
    # إضافة الدوال قبل async def start_email
    start_email_pattern = r'async def start_email\(update: Update, context: ContextTypes\.DEFAULT_TYPE\):'
    if re.search(start_email_pattern, content):
        content = re.sub(start_email_pattern, f'{helper_functions}\n{start_email_pattern.replace(":", ":")[-70:]}', content)
        print("✅ تم إضافة الدوال المساعدة")
    
    # إصلاح أخطاء ConversationHandler
    content = content.replace('CallbackQuery_handler', 'CallbackQueryHandler')
    content = content.replace('process_add_emails_input', 'process_add_emails')
    content = content.replace('process_delete_email_input', 'process_delete_email')
    content = content.replace('get_number_input', 'get_number')
    content = content.replace('get_emails_input', 'get_emails')
    content = content.replace('get_subject_input', 'get_subject')
    content = content.replace('get_body_input', 'get_body')
    
    print("✅ تم إصلاح أخطاء ConversationHandler")
    
    # حفظ الملف المُحدث
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ تم حفظ الملف المُحدث في {file_path}")
    return True

def create_init_files():
    """إنشاء ملفات __init__.py"""
    
    init_files = [
        "Email/__init__.py",
        "Telegram/__init__.py"
    ]
    
    for file_path in init_files:
        dir_path = os.path.dirname(file_path)
        if not os.path.exists(dir_path):
            print(f"❌ المجلد {dir_path} غير موجود!")
            continue
            
        with open(file_path, 'w', encoding='utf-8') as f:
            package_name = dir_path
            f.write(f"# ملف __init__.py لجعل {package_name} حزمة Python\n")
        
        print(f"✅ تم إنشاء {file_path}")

def fix_khayal_import():
    """إصلاح استيراد البريد الإلكتروني في khayal.py"""
    
    file_path = "khayal.py"
    
    if not os.path.exists(file_path):
        print(f"❌ الملف {file_path} غير موجود!")
        return False
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # إصلاح الاستيراد
    content = content.replace('Email.Email_reports', 'Email.email_reports')
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ تم إصلاح استيراد البريد الإلكتروني في {file_path}")
    return True

def main():
    """الدالة الرئيسية"""
    print("🚀 بدء إصلاح مشروع Drkhayal Bot")
    print("=" * 50)
    
    # إنشاء ملفات __init__.py
    print("\n📁 إنشاء ملفات __init__.py...")
    create_init_files()
    
    # إصلاح ملف Email/email_reports.py
    print("\n🔧 إصلاح ملف Email/email_reports.py...")
    if fix_email_reports():
        print("✅ تم إصلاح ملف email_reports.py بنجاح")
    else:
        print("❌ فشل في إصلاح ملف email_reports.py")
    
    # إصلاح استيراد في khayal.py
    print("\n🔧 إصلاح استيراد في khayal.py...")
    if fix_khayal_import():
        print("✅ تم إصلاح khayal.py بنجاح")
    else:
        print("❌ فشل في إصلاح khayal.py")
    
    print("\n🎉 انتهى الإصلاح!")
    print("=" * 50)
    print("\n📋 ما تم إنجازه:")
    print("✅ إنشاء ملفات __init__.py")
    print("✅ إضافة الدوال المفقودة في email_reports.py")
    print("✅ إصلاح أخطاء ConversationHandler")
    print("✅ إصلاح استيراد البريد الإلكتروني")
    print("\n🚀 يمكنك الآن تشغيل البوت:")
    print("python3 khayal.py")

if __name__ == "__main__":
    main()