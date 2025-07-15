# DrKhayal/Telegram/report_mass.py

import asyncio
import re
import time
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors.rpcerrorlist import ChannelPrivateError, UsernameNotOccupiedError, FloodWaitError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
)
import logging

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# استيراد الإعدادات من الملف الرئيسي
try:
    from config import API_ID, API_HASH
except ImportError:
    logger.error("خطأ: لا يمكن استيراد API_ID و API_HASH من config.py")
    API_ID, API_HASH = None, None
    # Exit or handle this critical error appropriately in a real application
    # For now, we'll let it proceed but expect issues if not set.

from .common import run_report_process, cancel_operation, REPORT_TYPES, parse_message_link

# States
(
    SELECT_REASON,
    ENTER_CHANNEL,
    SELECT_POSTS_OPTION,
    ENTER_MEDIA_LIMIT,
    ENTER_POSTS_NUMBER,
    ENTER_DAYS,
    FETCH_POSTS_TRIGGER, # Renamed for clarity - this is a trigger state
    ENTER_DETAILS,
    ENTER_REPORT_COUNT,
    ENTER_DELAY,
    CONFIRM_START,
) = range(50, 61)

async def start_mass_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية الإبلاغ الجماعي"""
    query = update.callback_query
    await query.answer()
    context.user_data["method_type"] = "mass"
    
    # بناء لوحة الأسباب
    keyboard = []
    for k, r in REPORT_TYPES.items():
        keyboard.append([InlineKeyboardButton(r[0], callback_data=f"reason_{k}")])
    keyboard.append([InlineKeyboardButton("إلغاء ❌", callback_data="cancel")])
    
    await query.edit_message_text(
        "اختر سبب الإبلاغ الجماعي:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECT_REASON

async def select_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار سبب الإبلاغ"""
    query = update.callback_query
    await query.answer()
    reason_num = int(query.data.split("_")[1])
    context.user_data["reason_obj"] = REPORT_TYPES[reason_num][1]
    
    await query.edit_message_text(
        "أرسل رابط القناة العامة أو الخاصة المستهدفة:\n\n"
        "📌 أمثلة:\n"
        "https://t.me/channel_name\n"
        "@channel_username"
    )
    return ENTER_CHANNEL

async def process_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال القناة المستهدفة"""
    channel_link = update.message.text.strip()
    context.user_data["channel_link"] = channel_link
    
    if not API_ID or not API_HASH:
        await update.message.reply_text("❌ خطأ في الإعدادات: API_ID أو API_HASH غير متوفر.")
        return ConversationHandler.END

    # التحقق من وجود حسابات
    if not context.user_data.get("accounts"):
        await update.message.reply_text("❌ خطأ داخلي: لم يتم تحميل الحسابات. يرجى البدء من جديد.")
        return ConversationHandler.END

    # استخدام أول حساب للتحقق
    session_data = context.user_data["accounts"][0]
    client = TelegramClient(StringSession(session_data["session"]), API_ID, API_HASH)
    
    try:
        await client.connect()
        entity = await client.get_entity(channel_link)
        context.user_data["channel"] = entity.username or entity.id
        context.user_data["channel_title"] = entity.title
        
        # عرض خيارات جلب المنشورات
        keyboard = [
            [InlineKeyboardButton("آخر 50 منشور", callback_data="posts_limit_50")],
            [InlineKeyboardButton("آخر 100 منشور", callback_data="posts_limit_100")],
            [InlineKeyboardButton("آخر 200 منشور", callback_data="posts_limit_200")],
            [InlineKeyboardButton("منشورات محددة (إرسال روابط)", callback_data="posts_custom")],
            [InlineKeyboardButton("منشورات من فترة محددة", callback_data="posts_date")],
            [InlineKeyboardButton("منشورات الوسائط فقط", callback_data="posts_media")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_channel")],
        ]
        await update.message.reply_text(
            f"✅ تم التحقق من القناة: <b>{entity.title}</b>\n\n"
            "اختر طريقة تحديد المنشورات للإبلاغ:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SELECT_POSTS_OPTION

    except (ValueError, UsernameNotOccupiedError):
        await update.message.reply_text("❌ رابط القناة أو اسم المستخدم غير صالح. يرجى التحقق والمحاولة مرة أخرى.")
        return ENTER_CHANNEL
    except ChannelPrivateError:
        await update.message.reply_text("⚠️ لا يمكن الوصول للقناة. قد تكون خاصة أو أن الحسابات المستخدمة ليست أعضاء فيها.")
        return ENTER_CHANNEL
    except FloodWaitError as e:
        await update.message.reply_text(f"⚠️ لقد تجاوزت حدود تليجرام. يرجى الانتظار {e.seconds} ثانية والمحاولة مرة أخرى.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"خطأ غير متوقع في التحقق من القناة: {e}", exc_info=True)
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع أثناء التحقق من القناة: {e}")
        return ConversationHandler.END
    finally:
        if client.is_connected():
            await client.disconnect()

async def select_posts_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار خيار المنشورات"""
    query = update.callback_query
    await query.answer()
    choice = query.data

    if choice == "posts_custom":
        context.user_data['fetch_type'] = 'custom'
        await query.edit_message_text(
            "أرسل روابط المنشورات المراد الإبلاغ عنها (روابط متعددة مفصولة بمسافة أو أسطر جديدة):\n\n"
            "📌 مثال:\n"
            "https://t.me/channel/123\n"
            "https://t.me/channel/456"
        )
        return ENTER_POSTS_NUMBER
    elif choice == "posts_date":
        context.user_data['fetch_type'] = 'date'
        await query.edit_message_text(
            "أدخل عدد الأيام الماضية لجلب المنشورات منها (مثال: 7 لجلب منشورات آخر 7 أيام):"
        )
        return ENTER_DAYS
    elif choice == "back_to_channel":
        await query.edit_message_text("أرسل رابط القناة المستهدفة:")
        return ENTER_CHANNEL
    elif choice == "posts_media":
        context.user_data['fetch_type'] = 'media'
        # طلب اختيار عدد المنشورات مع وسائط
        keyboard = [
            [InlineKeyboardButton("50 منشور", callback_data="limit_50")],
            [InlineKeyboardButton("100 منشور", callback_data="limit_100")],
            [InlineKeyboardButton("200 منشور", callback_data="limit_200")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_posts_option")],
        ]
        await query.edit_message_text(
            "اختر عدد المنشورات التي تحتوي على وسائط التي تريد جلبها:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ENTER_MEDIA_LIMIT
    elif choice.startswith("posts_limit_"):
        context.user_data['fetch_type'] = 'recent'
        limit = int(choice.split("_")[2]) # Extract limit from posts_limit_XX
        context.user_data['fetch_limit'] = limit
        return await fetch_posts(update, context, from_callback=True)
    
    # Should not reach here
    await query.edit_message_text("❌ خيار غير صالح. يرجى المحاولة مرة أخرى.")
    return SELECT_POSTS_OPTION

async def handle_media_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار عدد المنشورات مع وسائط"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_posts_option":
        # Re-display post options
        keyboard = [
            [InlineKeyboardButton("آخر 50 منشور", callback_data="posts_limit_50")],
            [InlineKeyboardButton("آخر 100 منشور", callback_data="posts_limit_100")],
            [InlineKeyboardButton("آخر 200 منشور", callback_data="posts_limit_200")],
            [InlineKeyboardButton("منشورات محددة (إرسال روابط)", callback_data="posts_custom")],
            [InlineKeyboardButton("منشورات من فترة محددة", callback_data="posts_date")],
            [InlineKeyboardButton("منشورات الوسائط فقط", callback_data="posts_media")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_channel")],
        ]
        await query.edit_message_text(
            "اختر طريقة تحديد المنشورات للإبلاغ:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SELECT_POSTS_OPTION
        
    limit = int(query.data.split("_")[1])
    context.user_data['fetch_limit'] = limit
    return await fetch_posts(update, context, from_callback=True)

async def process_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال عدد الأيام"""
    try:
        days = int(update.message.text)
        if days <= 0:
            await update.message.reply_text("❌ عدد الأيام يجب أن يكون أكبر من الصفر.")
            return ENTER_DAYS
        context.user_data['days'] = days
        return await fetch_posts(update, context, from_message=True)
    except ValueError:
        await update.message.reply_text("❌ عدد الأيام غير صالح. أدخل رقمًا صحيحًا.")
        return ENTER_DAYS

async def process_posts_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال روابط المنشورات"""
    # Allow multiple links separated by space or new line
    links = re.split(r'\s+|\n+', update.message.text.strip())
    targets = []
    channel_entity = context.user_data["channel"] # Use the stored channel entity for parsing

    # We need to ensure that the message ID is indeed for the current channel.
    # The parse_message_link should ideally return channel ID/username as well.
    # For now, we'll assume it's for the target channel, but this is a point of potential improvement
    # in parse_message_link if it doesn't validate the channel part of the link.
    
    for link in links:
        parsed = parse_message_link(link)
        if parsed and parsed.get("message_id"):
            # Basic validation: check if the parsed link belongs to the currently selected channel
            # This is a weak check, a more robust parse_message_link would extract channel info.
            # For now, just ensure it's a message link.
            targets.append({
                "channel": channel_entity, # Use stored channel for consistency
                "message_id": parsed["message_id"]
            })
    
    if not targets:
        await update.message.reply_text("❌ لم يتم العثور على روابط صالحة أو أنها لا تخص هذه القناة. أعد المحاولة.")
        return ENTER_POSTS_NUMBER
    
    context.user_data["targets"] = targets
    await update.message.reply_text(
        f"✅ تم تحديد {len(targets)} منشور للإبلاغ.\n\n"
        "أرسل رسالة تفصيلية للبلاغ (أو أرسل /skip للتخطي):"
    )
    return ENTER_DETAILS

async def fetch_posts(update: Update, context: ContextTypes.DEFAULT_TYPE, from_callback=False, from_message=False):
    """جلب المنشورات من القناة حسب الخيار المحدد"""
    fetch_type = context.user_data['fetch_type']
    
    loading_text = ""
    if fetch_type == 'recent':
        limit = context.user_data['fetch_limit']
        loading_text = f"⏳ جاري جلب آخر {limit} منشور..."
    elif fetch_type == 'media':
        limit = context.user_data['fetch_limit']
        loading_text = f"⏳ جاري جلب آخر {limit} منشور تحتوي على وسائط..."
    elif fetch_type == 'date':
        days = context.user_data['days']
        loading_text = f"⏳ جاري جلب المنشورات من آخر {days} يوم..."

    if from_message:
        msg = await update.message.reply_text(loading_text)
    elif from_callback:
        msg = await update.callback_query.message.edit_text(loading_text)
    else: # Fallback, should ideally not happen if called correctly
        msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=loading_text)


    channel_entity_id = context.user_data["channel"]
    session_str = context.user_data["accounts"][0]["session"]
    
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    posts = []
    
    try:
        await client.connect()
        
        if fetch_type == 'recent':
            limit = context.user_data['fetch_limit']
            async for message in client.iter_messages(channel_entity_id, limit=limit):
                posts.append({"channel": channel_entity_id, "message_id": message.id})
                
        elif fetch_type == 'media':
            limit = context.user_data['fetch_limit']
            media_posts_count = 0
            # Iterate through messages and collect only those with media
            # We'll fetch more than 'limit' to ensure we get enough media posts
            async for message in client.iter_messages(channel_entity_id, limit=None): # Iterate indefinitely
                if message.media:
                    posts.append({"channel": channel_entity_id, "message_id": message.id})
                    media_posts_count += 1
                if media_posts_count >= limit:
                    break
            
        elif fetch_type == 'date':
            days = context.user_data['days']
            offset_date = datetime.now() - timedelta(days=days)
            # Fetch messages until we go past the offset_date
            async for message in client.iter_messages(channel_entity_id, offset_date=offset_date):
                # iter_messages with offset_date gives messages *older* than the date
                # We want messages *newer* than the date.
                # So we iterate and add if message.date is after offset_date
                if message.date > offset_date:
                    posts.append({"channel": channel_entity_id, "message_id": message.id})
                else:
                    # Once we hit a message older than or equal to the offset_date, stop
                    # as messages are returned in reverse chronological order.
                    break

    except FloodWaitError as e:
        logger.error(f"FloodWaitError during fetching posts: {e}", exc_info=True)
        await msg.edit_text(f"⚠️ لقد تجاوزت حدود تليجرام أثناء جلب المنشورات. يرجى الانتظار {e.seconds} ثانية والمحاولة مرة أخرى.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"فشل جلب المنشورات: {e}", exc_info=True)
        await msg.edit_text(f"❌ فشل جلب المنشورات: {e}")
        return ConversationHandler.END
    finally:
        if client.is_connected():
            await client.disconnect()

    if not posts:
        await msg.edit_text("❌ لم يتم العثور على أي منشورات تطابق المعايير في هذه القناة.")
        return ConversationHandler.END
        
    context.user_data["targets"] = posts
    await msg.edit_text(
        f"✅ تم جلب {len(posts)} منشور بنجاح.\n\nالآن، أرسل رسالة تفصيلية للبلاغ (أو أرسل /skip للتخطي):"
    )
    return ENTER_DETAILS

async def process_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة التفاصيل الإضافية للبلاغ"""
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
        "اختر عدد مرات الإبلاغ على كل منشور من كل حساب:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ENTER_REPORT_COUNT

async def process_report_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار عدد مرات الإبلاغ"""
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
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
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
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ENTER_DELAY
    except ValueError:
        await update.message.reply_text("❌ أدخل رقمًا صحيحًا فقط.")
        return ENTER_REPORT_COUNT

async def process_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار الفاصل الزمني"""
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
        f"• القناة المستهدفة: <b>{config.get('channel_title', 'غير معروف')}</b>\n"
        f"• عدد المنشورات المحددة: {len(config['targets'])}\n"
        f"• عدد البلاغات لكل حساب/منشور: {config['reports_per_account']}\n"
        f"• الفاصل الزمني بين البلاغات: {config['cycle_delay']} ثانية\n\n"
        f"هل تريد بدء العملية؟"
    )
    keyboard = [
        [InlineKeyboardButton("بدء العملية ✅", callback_data="confirm")],
        [InlineKeyboardButton("إلغاء ❌", callback_data="cancel")],
    ]
    await query.edit_message_text(
        summary, 
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
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
            f"• القناة المستهدفة: <b>{config.get('channel_title', 'غير معروف')}</b>\n"
            f"• عدد المنشورات المحددة: {len(config['targets'])}\n"
            f"• عدد البلاغات لكل حساب/منشور: {config['reports_per_account']}\n"
            f"• الفاصل الزمني بين البلاغات: {config['cycle_delay']} ثانية\n\n"
            f"هل تريد بدء العملية؟"
        )
        keyboard = [
            [InlineKeyboardButton("بدء العملية ✅", callback_data="confirm")],
            [InlineKeyboardButton("إلغاء ❌", callback_data="cancel")],
        ]
        await update.message.reply_text(
            summary, 
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CONFIRM_START
    except ValueError:
        await update.message.reply_text("❌ أدخل رقمًا صحيحًا فقط.")
        return ENTER_DELAY
    
async def confirm_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية الإبلاغ بعد التأكيد مع تحديثات التقدم المحسنة"""
    query = update.callback_query
    await query.answer()
    
    # حساب التكلفة التقديرية
    num_accounts = len(context.user_data["accounts"])
    num_targets = len(context.user_data["targets"])
    reports_per = context.user_data["reports_per_account"]
    total_reports_to_attempt = num_accounts * num_targets * reports_per
    
    # تقدير الوقت
    delay = context.user_data["cycle_delay"]
    # Total effective reports for time estimation considers only successful reports with delay
    est_total_delay_seconds = total_reports_to_attempt * delay
    est_time_minutes = est_total_delay_seconds / 60  # بالدقائق
    
    # تسجيل وقت البدء
    start_time = time.time()
    context.user_data["start_time"] = start_time
    
    # عرض ملخص العملية مع شريط تقدم مبدئي
    progress_bar = "[□□□□□□□□□□] 0%"
    
    summary = (
        f"📊 <b>بدء عملية الإبلاغ الجماعي</b>\n\n"
        f"{progress_bar}\n\n"
        f"• القناة: <b>{context.user_data.get('channel_title', 'غير معروف')}</b>\n"
        f"• عدد الحسابات المستخدمة: {num_accounts}\n"
        f"• عدد المنشورات المستهدفة: {num_targets}\n"
        f"• البلاغات لكل حساب/منشور: {reports_per}\n"
        f"• إجمالي البلاغات المتوقعة: {total_reports_to_attempt}\n"
        f"• الفاصل الزمني بين البلاغات: {delay} ثانية\n"
        f"• الوقت المتوقع للانتهاء: حوالي {est_time_minutes:.1f} دقيقة\n\n"
        "⏳ جاري بدء العملية... يرجى الانتظار."
    )
    
    context.user_data["active"] = True
    context.user_data["total_reports_attempted"] = total_reports_to_attempt # Renamed for clarity
    context.user_data["progress_success"] = 0
    context.user_data["progress_failed"] = 0
    context.user_data["operation_status_message"] = None # To store the message object for updates
    
    try:
        msg = await query.edit_message_text(
            text=summary,
            parse_mode="HTML"
        )
        context.user_data["operation_status_message"] = msg
        
        # بدء عملية الإبلاغ في الخلفية
        asyncio.create_task(run_report_process(update, context))
        
        # Initial quick update after a short delay
        await asyncio.sleep(2)
        try:
            # Update the message to indicate accounts are being loaded
            current_message_text = msg.text
            if "جاري بدء العملية..." in current_message_text:
                updated_text = current_message_text.replace("جاري بدء العملية...", "⏳ جاري تحميل الحسابات وبدء البلاغ الأول...")
                await context.bot.edit_message_text(
                    chat_id=msg.chat_id,
                    message_id=msg.message_id,
                    text=updated_text,
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.warning(f"Could not update initial progress message: {e}")
        
    except Exception as e:
        logger.error(f"خطأ في بدء عملية الإبلاغ: {str(e)}", exc_info=True)
        await query.edit_message_text(
            f"❌ فشل بدء العملية: {str(e)}"
        )
    
    return ConversationHandler.END

# معالج المحادثة المحدث
mass_report_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_mass_report, pattern='^method_mass$')],
    states={
        SELECT_REASON: [CallbackQueryHandler(select_reason, pattern='^reason_')],
        ENTER_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_channel)],
        SELECT_POSTS_OPTION: [CallbackQueryHandler(select_posts_option, pattern='^posts_')],
        ENTER_POSTS_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_posts_number)],
        ENTER_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_days)],
        ENTER_MEDIA_LIMIT: [CallbackQueryHandler(handle_media_limit, pattern='^limit_')],
        # FETCH_POSTS_TRIGGER state is implicit here, called directly by handlers
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
