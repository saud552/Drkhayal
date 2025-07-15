import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
)

from .common import run_report_process, cancel_operation, REPORT_TYPES
from .common_improved import run_enhanced_report_process

# States
(
    SELECT_REASON,
    ENTER_TARGET,
    ENTER_DETAILS,
    ENTER_REPORT_COUNT,
    ENTER_DELAY,
    CONFIRM_START,
) = range(30, 36)

async def start_photo_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["method_type"] = "photo"

    keyboard = [[InlineKeyboardButton(r[0], callback_data=f"reason_{k}")] for k, r in REPORT_TYPES.items()]
    keyboard.append([InlineKeyboardButton("إلغاء ❌", callback_data="cancel")])
    
    await query.edit_message_text(
        "🖼️ <b>طريقة الإبلاغ عن الصور الشخصية</b>\n\n"
        "اختر سبب الإبلاغ:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_REASON

async def select_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    reason_num = int(query.data.split("_")[1])
    context.user_data["reason_obj"] = REPORT_TYPES[reason_num][1]
    
    await query.edit_message_text(
        "👤 <b>إدخال الهدف</b>\n\n"
        "أرسل يوزر أو رابط الحساب المستهدف للإبلاغ عن صورته الشخصية:\n\n"
        "📌 <i>أمثلة:</i>\n"
        "@username\n"
        "https://t.me/username",
        parse_mode="HTML"
    )
    return ENTER_TARGET

async def process_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.text.strip()
    # استخراج المعرف من الرابط إن وجد
    if target.startswith("https://t.me/"):
        target = target.split("/")[-1].replace("@", "")
    
    context.user_data["targets"] = [target]
    
    await update.message.reply_text(
        "💬 <b>التفاصيل الإضافية</b>\n\n"
        "أرسل رسالة تفصيلية للبلاغ (أو أرسل /skip للتخطي):",
        parse_mode="HTML"
    )
    return ENTER_DETAILS
    
async def process_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() != '/skip':
        context.user_data["message"] = update.message.text
    else:
        context.user_data["message"] = ""
    
    keyboard = [
        [InlineKeyboardButton("1 مرة", callback_data="count_1")],
        [InlineKeyboardButton("2 مرات", callback_data="count_2")],
        [InlineKeyboardButton("3 مرات", callback_data="count_3")],
        [InlineKeyboardButton("مخصص", callback_data="count_custom")]
    ]
    await update.message.reply_text(
        "🔄 <b>عدد مرات الإبلاغ</b>\n\n"
        "اختر عدد مرات الإبلاغ على الصورة من كل حساب:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return ENTER_REPORT_COUNT

async def process_report_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "count_custom":
        await query.edit_message_text(
            "🔢 <b>عدد مخصص</b>\n\n"
            "أدخل عدد مرات الإبلاغ:",
            parse_mode="HTML"
        )
        return ENTER_REPORT_COUNT
    
    count = int(query.data.split("_")[1])
    context.user_data["reports_per_account"] = count
    
    keyboard = [
        [InlineKeyboardButton("5 ثواني", callback_data="delay_5")],
        [InlineKeyboardButton("10 ثواني", callback_data="delay_10")],
        [InlineKeyboardButton("30 ثواني", callback_data="delay_30")],
        [InlineKeyboardButton("مخصص", callback_data="delay_custom")]
    ]
    await query.edit_message_text(
        "⏱️ <b>الفاصل الزمني</b>\n\n"
        "اختر الفاصل الزمني بين الإبلاغات:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return ENTER_DELAY

async def custom_report_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة العدد المخصص للإبلاغات"""
    try:
        count = int(update.message.text)
        if count <= 0:
            await update.message.reply_text("❌ يجب أن يكون العدد أكبر من الصفر.")
            return ENTER_REPORT_COUNT
            
        context.user_data["reports_per_account"] = count
        
        keyboard = [
            [InlineKeyboardButton("5 ثواني", callback_data="delay_5")],
            [InlineKeyboardButton("10 ثواني", callback_data="delay_10")],
            [InlineKeyboardButton("30 ثواني", callback_data="delay_30")],
            [InlineKeyboardButton("مخصص", callback_data="delay_custom")]
        ]
        await update.message.reply_text(
            "⏱️ <b>الفاصل الزمني</b>\n\n"
            "اختر الفاصل الزمني بين الإبلاغات:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard))
        return ENTER_DELAY
    except ValueError:
        await update.message.reply_text("❌ أدخل رقمًا صحيحًا فقط.")
        return ENTER_REPORT_COUNT

async def process_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "delay_custom":
        await query.edit_message_text(
            "⏳ <b>فاصل زمني مخصص</b>\n\n"
            "أدخل الفاصل الزمني (بالثواني):",
            parse_mode="HTML"
        )
        return ENTER_DELAY
    
    delay = int(query.data.split("_")[1])
    context.user_data["cycle_delay"] = delay
    
    # عرض ملخص وتأكيد
    config = context.user_data
    summary = (
        f"📝 <b>ملخص العملية</b>\n\n"
        f"• الهدف: {config['targets'][0]}\n"
        f"• عدد البلاغات/حساب: {config['reports_per_account']}\n"
        f"• الفاصل الزمني: {config['cycle_delay']} ثانية\n\n"
        f"هل تريد بدء العملية؟"
    )
    keyboard = [
        [InlineKeyboardButton("بدء العملية ✅", callback_data="confirm")],
        [InlineKeyboardButton("إلغاء ❌", callback_data="cancel")],
    ]
    await query.edit_message_text(
        summary, 
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM_START

async def custom_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الفاصل الزمني المخصص"""
    try:
        delay = int(update.message.text)
        if delay <= 0:
            await update.message.reply_text("❌ يجب أن يكون الفاصل الزمني أكبر من الصفر.")
            return ENTER_DELAY
            
        context.user_data["cycle_delay"] = delay
    
        # عرض ملخص وتأكيد
        config = context.user_data
        summary = (
            f"📝 <b>ملخص العملية</b>\n\n"
            f"• الهدف: {config['targets'][0]}\n"
            f"• عدد البلاغات/حساب: {config['reports_per_account']}\n"
            f"• الفاصل الزمني: {config['cycle_delay']} ثانية\n\n"
            f"هل تريد بدء العملية؟"
        )
        keyboard = [
            [InlineKeyboardButton("بدء العملية ✅", callback_data="confirm")],
            [InlineKeyboardButton("إلغاء ❌", callback_data="cancel")],
        ]
        await update.message.reply_text(
            summary, 
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard))
        return CONFIRM_START
    except ValueError:
        await update.message.reply_text("❌ أدخل رقمًا صحيحًا فقط.")
        return ENTER_DELAY
    
async def confirm_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["active"] = True
    
    # حساب التكلفة التقديرية
    num_accounts = len(context.user_data["accounts"])
    reports_per = context.user_data["reports_per_account"]
    total_reports = num_accounts * reports_per
    
    # تقدير الوقت
    delay = context.user_data["cycle_delay"]
    est_time = total_reports * delay / 60  # بالدقائق
    
    msg = await query.edit_message_text(
        f"⏳ <b>جاري بدء عملية الإبلاغ...</b>\n\n"
        f"• عدد الحسابات: {num_accounts}\n"
        f"• إجمالي البلاغات: {total_reports}\n"
        f"• الوقت المتوقع: {est_time:.1f} دقيقة\n\n"
        "سيتم تحديث التقدم هنا...",
        parse_mode="HTML"
    )
    context.user_data["progress_message"] = msg
    
    # بدء عملية الإبلاغ المحسنة في الخلفية
    asyncio.create_task(run_enhanced_report_process(update, context))
    return ConversationHandler.END


photo_report_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_photo_report, pattern='^method_photo$')],
    states={
        SELECT_REASON: [CallbackQueryHandler(select_reason, pattern='^reason_')],
        ENTER_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_target)],
        ENTER_DETAILS: [MessageHandler(filters.TEXT | filters.COMMAND, process_details)],
        ENTER_REPORT_COUNT: [
            CallbackQueryHandler(process_report_count, pattern='^count_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, custom_report_count)
        ],
        ENTER_DELAY: [
            CallbackQueryHandler(process_delay, pattern='^delay_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, custom_delay)
        ],
        CONFIRM_START: [
            CallbackQueryHandler(confirm_and_start, pattern='^confirm$'),
            CallbackQueryHandler(cancel_operation, pattern='^cancel$'),
        ],
    },
    fallbacks=[CallbackQueryHandler(cancel_operation, pattern='^cancel$')],
    per_user=True,
)