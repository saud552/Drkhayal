# DrKhayal/khayal.py

import sys
import os
import asyncio
import logging
import time
from urllib.parse import urlparse, parse_qs

# ===================================================================
#  إضافة المجلد الرئيسي للمشروع إلى مسار بايثون
# ===================================================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# ===================================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate

# --- استيراد الإعدادات الأساسية ---
try:
    from config import BOT_TOKEN, OWNER_ID, DB_PATH, API_ID, API_HASH
except ImportError:
    logging.error("خطأ: لم يتم العثور على ملف config.py أو أنه ناقص. يجب أن يحتوي على: BOT_TOKEN, OWNER_ID, DB_PATH, API_ID, API_HASH")
    exit(1)
# --- استيراد معالجات المحادثة من الوحدات المنفصلة ---
try:
    from Email.Email_reports import email_conv_handler
except ImportError:
    logging.warning("تحذير: لم يتم العثور على وحدة البريد الإلكتروني. سيتم تجاهل هذا القسم.")
    email_conv_handler = None

try:
    from Telegram.support_module import register_support_handlers
except ImportError:
    logging.warning("تحذير: لم يتم العثور على وحدة الدعم الخاص (support_module.py). سيتم تجاهلها.")
    register_support_handlers = None

from Telegram.report_peer import peer_report_conv
from Telegram.report_message import message_report_conv
from Telegram.report_photo import photo_report_conv
from Telegram.report_sponsored import sponsored_report_conv
from Telegram.report_mass import mass_report_conv

# استيراد الدوال المشتركة المحدثة
from Telegram.common import get_categories, get_accounts, parse_proxy_link, proxy_checker, cancel_operation, convert_secret
from Telegram.common_improved import (
    enhanced_proxy_checker, 
    parse_proxy_link_enhanced, 
    run_enhanced_report_process,
    EnhancedProxyChecker,
    VerifiedReporter
)
from config_enhanced import enhanced_config

# تقليل مستوى تسجيل telethon لتجنب الرسائل غير الضرورية
logging.getLogger('telethon').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- حالات المحادثة للملف الرئيسي (للإعداد الأولي) ---
(
    TELEGRAM_MENU,
    SELECT_CATEGORY,
    SELECT_PROXY_OPTION,
    ENTER_PROXY_LINKS,
) = range(4)

# ===================================================================
#  قسم البدء والقائمة الرئيسية
# ===================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """يعرض القائمة الرئيسية عند إرسال /start أو العودة إليها."""
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("❌ هذا البوت مخصص للمالك فقط.")
        return

    keyboard = [
        [InlineKeyboardButton("📧 قسم بلاغات ايميل", callback_data="main_email")],
        [InlineKeyboardButton("📢 قسم بلاغات تيليجرام", callback_data="main_telegram")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
             "👋 أهلاً بك! اختر القسم الذي تريد العمل عليه:",
             reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "👋 أهلاً بك! اختر القسم الذي تريد العمل عليه:",
            reply_markup=reply_markup
        )

# ===================================================================
# قسم إعداد بلاغات تيليجرام (التدفق الأولي)
# ===================================================================

async def show_telegram_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يعرض قائمة خيارات قسم تيليجرام."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🏴‍☠ بدء عملية الإبلاغ", callback_data="start_report_setup")],
        [InlineKeyboardButton("🛠 الدعم الخاص", callback_data="special_support")],
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main_menu")]
    ]
    
    await query.edit_message_text(
        "📢 <b>قسم بلاغات تيليجرام</b>\n\n"
        "اختر الإجراء الذي تريد تنفيذه:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TELEGRAM_MENU

async def choose_session_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """الخطوة 1: تطلب من المستخدم اختيار فئة الحسابات."""
    query = update.callback_query
    await query.answer()
    
    categories = get_categories()
    if not categories:
        await query.edit_message_text("❌ لا توجد فئات تحتوي على حسابات. يرجى إضافتها أولاً.")
        return ConversationHandler.END
        
    keyboard = []
    for cat_id, name, count in categories:
        keyboard.append([InlineKeyboardButton(f"{name} ({count} حساب)", callback_data=f"cat_{cat_id}")])
    
    keyboard.append([InlineKeyboardButton("رجوع 🔙", callback_data="back_to_tg_menu")])
    
    await query.edit_message_text(
        "📂 <b>الخطوة 1/3: اختيار فئة الحسابات</b>\n\n"
        "اختر الفئة التي تحتوي على الحسابات التي تريد استخدامها:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECT_CATEGORY

async def process_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """الخطوة 2: تعالج اختيار الفئة وتطلب خيار البروكسي."""
    query = update.callback_query
    await query.answer()
    category_id = query.data.split("_")[1]
    
    # استخدم الدالة المحدثة من common.py
    accounts = get_accounts(category_id)
    
    if not accounts:
        await query.answer("❌ لا توجد حسابات صالحة في هذه الفئة!", show_alert=True)
        return SELECT_CATEGORY
        
    context.user_data['accounts'] = accounts
    await query.edit_message_text(
        f"✅ <b>تم تحميل {len(accounts)} حساب بنجاح!</b>\n\n"
        "📡 <b>الخطوة 2/3: إعداد البروكسي</b>\n\n"
        "هل تريد استخدام بروكسي للحسابات؟",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📡 استخدام بروكسي", callback_data="use_proxy")],
            [InlineKeyboardButton("⏭️ تخطي (اتصال مباشر)", callback_data="skip_proxy")],
            [InlineKeyboardButton("رجوع 🔙", callback_data="back_to_cat_select")],
        ])
    )
    return SELECT_PROXY_OPTION

async def process_proxy_option(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """تعالج خيار استخدام البروكسي وتطلب الروابط إذا لزم الأمر."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "use_proxy":
        await query.edit_message_text(
            "🌐 <b>إدخال روابط البروكسي</b>\n\n"
            "أرسل روابط بروكسي MTProto (كل رابط في سطر):\n\n"
            "📌 <i>مثال:</i>\n"
            "https://t.me/proxy?server=1.2.3.4&port=443&secret=ee...\n\n"
            "⚠️ الحد الأقصى: 50 بروكسي",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء ❌", callback_data="cancel_setup")]])
        )
        return ENTER_PROXY_LINKS
        
    context.user_data['proxies'] = []
    return await select_method_menu(update, context, is_query=True)

async def process_proxy_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة روابط البروكسي مع النظام المحسن المطور"""
    input_links = update.message.text.strip().splitlines()
    if not input_links:
        await update.message.reply_text("لم يتم إدخال أي روابط.")
        return await select_method_menu(update, context)

    accounts = context.user_data.get("accounts")
    if not accounts:
        await update.message.reply_text("❌ خطأ: لا توجد حسابات للتحقق من البروكسيات.")
        return ConversationHandler.END

    # تطبيق الحد الأقصى للبروكسيات
    MAX_PROXIES = enhanced_config.proxy.quality_threshold or 50
    if len(input_links) > MAX_PROXIES:
        input_links = input_links[:MAX_PROXIES]
        await update.message.reply_text(f"⚠️ تم تقليل عدد البروكسيات إلى {MAX_PROXIES} (الحد الأقصى)")

    msg = await update.message.reply_text(f"🔍 جاري الفحص المحسن لـ {len(input_links)} بروكسي...")
    session_str = accounts[0]["session"]

    # تحليل الروابط بالنظام المحسن
    parsed_proxies = []
    for link in input_links:
        proxy_info = parse_proxy_link_enhanced(link)
        if proxy_info:
            parsed_proxies.append(proxy_info)
        else:
            logger.warning(f"❌ رابط بروكسي غير صالح: {link}")
            
    if not parsed_proxies:
        await msg.edit_text("❌ لم يتم العثور على أي روابط بروكسي صالحة.")
        return await select_method_menu(update, context)
        
    # فحص البروكسيات بالنظام المحسن مع تتبع التقدم
    try:
        await msg.edit_text(f"🔍 بدء الفحص العميق لـ {len(parsed_proxies)} بروكسي...")
        
        # استخدام النظام المحسن للفحص المتوازي
        valid_proxies = await enhanced_proxy_checker.batch_check_proxies(session_str, parsed_proxies)
        
        # تصفية البروكسيات النشطة وترتيبها حسب الجودة
        active_proxies = [p for p in valid_proxies if p.get('status') == 'active']
        failed_proxies = [p for p in valid_proxies if p.get('status') != 'active']
        
        # إحصائيات مفصلة
        total_checked = len(valid_proxies)
        active_count = len(active_proxies)
        failed_count = len(failed_proxies)
        
        # تسجيل النتائج المفصلة
        for proxy in active_proxies:
            logger.info(f"✅ بروكسي نشط: {proxy['server']} - جودة: {proxy.get('quality_score', 0)}% - ping: {proxy.get('ping', 0)}ms")
        
        for proxy in failed_proxies:
            logger.warning(f"❌ بروكسي فاشل: {proxy['server']} - السبب: {proxy.get('error', 'غير محدد')}")
            
    except Exception as e:
        logger.error(f"خطأ في النظام المحسن لفحص البروكسيات: {e}")
        await msg.edit_text("❌ حدث خطأ في النظام المحسن. جاري التراجع للنظام القديم...")
        
        # التراجع للنظام القديم في حالة الخطأ
        valid_proxies = []
        for link in input_links:
            proxy_info = parse_proxy_link(link)
            if not proxy_info: 
                continue
            try:
                checked_proxy = await proxy_checker.check_proxy(session_str, proxy_info)
                if checked_proxy.get("status") == "active":
                    valid_proxies.append(checked_proxy)
            except Exception as fallback_error:
                logger.error(f"خطأ في النظام القديم أيضاً: {fallback_error}")

    # عرض النتائج المحسنة
    if not active_proxies:
        await msg.edit_text(
            f"⚠️ <b>نتائج الفحص</b>\n\n"
            f"• تم فحص: {total_checked} بروكسي\n"
            f"• نشط: {active_count}\n"
            f"• فاشل: {failed_count}\n\n"
            f"سيتم استخدام الاتصال المباشر.",
            parse_mode="HTML"
        )
        context.user_data['proxies'] = []
    else:
        # الحصول على أفضل البروكسيات
        best_proxies = enhanced_proxy_checker.get_best_proxies(active_proxies, 3)
        best_proxy = best_proxies[0] if best_proxies else None
        
        # تفاصيل أفضل البروكسيات
        best_details = "\n".join([
            f"• {p['server']} - جودة: {p.get('quality_score', 0)}% - {p.get('ping', 0)}ms"
            for p in best_proxies[:3]
        ])
        
        await msg.edit_text(
            f"✅ <b>نتائج الفحص المحسن</b>\n\n"
            f"• تم فحص: {total_checked} بروكسي\n"
            f"• نشط: {active_count}\n"
            f"• فاشل: {failed_count}\n"
            f"• معدل النجاح: {(active_count/total_checked*100):.1f}%\n\n"
            f"🏆 <b>أفضل البروكسيات:</b>\n{best_details}",
            parse_mode="HTML"
        )
        
        # حفظ البروكسيات النشطة مرتبة حسب الجودة
        context.user_data['proxies'] = active_proxies
    
    return await select_method_menu(update, context)

async def select_method_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, is_query=False) -> int:
    """الخطوة 3: تعرض قائمة طرق الإبلاغ ثم تنهي محادثة الإعداد."""
    text = (
        "🛠️ <b>الخطوة 3/3: اختيار طريقة الإبلاغ</b>\n\n"
        "اختر طريقة الإبلاغ التي تناسبك:"
    )
    keyboard = [
        [InlineKeyboardButton("👤 حساب/قناة", callback_data="method_peer")],
        [InlineKeyboardButton("💬 رسالة", callback_data="method_message")],
        [InlineKeyboardButton("🖼️ صورة شخصية", callback_data="method_photo")],
        [InlineKeyboardButton("📢 إعلان ممول", callback_data="method_sponsored")],
        [InlineKeyboardButton("🔥 بلاغ جماعي", callback_data="method_mass")],
        [InlineKeyboardButton("رجوع 🔙", callback_data="back_to_proxy_option")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if is_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        
    return ConversationHandler.END

# --- دوال الإلغاء والرجوع ---
async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دالة العودة إلى القائمة الرئيسية من أي مكان."""
    query = update.callback_query
    if query: 
        await query.answer()
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END

async def cancel_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يلغي عملية الإعداد ويعود لقائمة تيليجرام."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("تم إلغاء عملية الإعداد.")
    await show_telegram_menu(update, context)
    return TELEGRAM_MENU

# ===================================================================
# إعداد البوت
# ===================================================================

def main() -> None:
    """إعداد وتشغيل البوت."""
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # --- معالج البدء الرئيسي ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(back_to_main_menu, pattern='^back_to_main_menu$'))

    # --- معالج قسم تيليجرام (الإعداد الأولي) ---
    telegram_setup_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(show_telegram_menu, pattern='^main_telegram$')],
        states={
            TELEGRAM_MENU: [
                CallbackQueryHandler(choose_session_source, pattern='^start_report_setup$'),
                CallbackQueryHandler(back_to_main_menu, pattern='^special_support$'),
            ],
            SELECT_CATEGORY: [
                CallbackQueryHandler(process_category_selection, pattern='^cat_'),
                CallbackQueryHandler(show_telegram_menu, pattern='^back_to_tg_menu$')
            ],
            SELECT_PROXY_OPTION: [
                CallbackQueryHandler(process_proxy_option, pattern='^(use_proxy|skip_proxy)$'),
                CallbackQueryHandler(choose_session_source, pattern='^back_to_cat_select$'),
            ],
            ENTER_PROXY_LINKS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_proxy_links),
                CallbackQueryHandler(show_telegram_menu, pattern='^back_to_proxy_option$')
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_setup, pattern='^cancel_setup$'),
            
            CommandHandler('cancel', cancel_operation),
            CallbackQueryHandler(back_to_main_menu, pattern='^back_to_main_menu$'),
        ],
        per_user=True,
    )
    
    # --- إضافة جميع المعالجات إلى التطبيق ---
    app.add_handler(telegram_setup_conv)
    
    if email_conv_handler: 
        app.add_handler(email_conv_handler)
    
    app.add_handler(peer_report_conv)
    app.add_handler(message_report_conv)
    app.add_handler(photo_report_conv)
    app.add_handler(sponsored_report_conv)
    app.add_handler(mass_report_conv)
    
    if register_support_handlers: 
        register_support_handlers(app)
    
    logger.info("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()