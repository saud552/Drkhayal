# DrKhayal/Telegram/report_mass.py

import asyncio
import re
import time
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors.rpcerrorlist import ChannelPrivateError, UsernameNotOccupiedError, FloodWaitError, PeerIdInvalidError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
    CommandHandler,
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
from .common_improved import run_enhanced_report_process

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
    """معالجة إدخال القناة المستهدفة - محسن مع دعم حسابات متعددة"""
    channel_link = update.message.text.strip()
    context.user_data["channel_link"] = channel_link
    
    if not API_ID or not API_HASH:
        await update.message.reply_text("❌ خطأ في الإعدادات: API_ID أو API_HASH غير متوفر.")
        return ConversationHandler.END

    # التحقق من وجود حسابات
    accounts = context.user_data.get("accounts", [])
    if not accounts:
        await update.message.reply_text("❌ خطأ داخلي: لم يتم تحميل الحسابات. يرجى البدء من جديد.")
        return ConversationHandler.END
    
    # رسالة تحقق مؤقتة
    checking_msg = await update.message.reply_text("🔍 جاري التحقق من القناة...")
    
    # محاولة التحقق من القناة باستخدام حسابات متعددة
    successful_validation = False
    entity = None
    last_error = None
    proxies = context.user_data.get("proxies", [])
    
    for attempt, session_data in enumerate(accounts[:3]):  # نجرب أول 3 حسابات فقط للسرعة
        session_str = session_data.get("session")
        session_id = session_data.get("id", f"حساب-{attempt+1}")
        
        if not session_str:
            logger.warning(f"تخطي الحساب {session_id}: جلسة فارغة")
            continue
        
        client = None
        current_proxy = None
        
        try:
            # اختيار بروكسي عشوائي إن وُجد
            if proxies:
                import random
                current_proxy = random.choice(proxies)
            
            # إعداد العميل مع البروكسي
            params = {
                "api_id": API_ID,
                "api_hash": API_HASH,
                "timeout": 20,
                "device_model": f"ChannelChecker-{session_id}",
                "system_version": "4.0.0",
                "app_version": "4.0.0"
            }
            
            if current_proxy:
                from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
                params.update({
                    "connection": ConnectionTcpMTProxyRandomizedIntermediate,
                    "proxy": (current_proxy["server"], current_proxy["port"], current_proxy["secret"])
                })
            
            client = TelegramClient(StringSession(session_str), **params)
            
            # تحديث رسالة التحقق
            try:
                await checking_msg.edit_text(f"🔍 جاري التحقق من القناة...\n🔄 محاولة مع الحساب {session_id}")
            except Exception:
                pass
            
            await client.connect()
            
            if not await client.is_user_authorized():
                logger.warning(f"الحساب {session_id} غير مفوض")
                continue
            
            # محاولة جلب معلومات القناة
            entity = await client.get_entity(channel_link)
            
            # تجربة جلب منشور واحد للتأكد من إمكانية الوصول
            try:
                async for message in client.iter_messages(entity, limit=1):
                    break  # إذا تمكن من جلب رسالة واحدة، فالوصول متاح
            except ChannelPrivateError:
                logger.warning(f"الحساب {session_id} لا يستطيع الوصول للقناة الخاصة")
                continue
            except Exception as access_error:
                logger.warning(f"خطأ في الوصول للقناة من الحساب {session_id}: {access_error}")
                continue
            
            successful_validation = True
            logger.info(f"✅ تم التحقق من القناة بنجاح باستخدام الحساب {session_id}")
            break
            
        except (ValueError, UsernameNotOccupiedError):
            last_error = "رابط القناة أو اسم المستخدم غير صالح"
            logger.warning(f"رابط غير صالح مع الحساب {session_id}")
            # لا نكمل مع باقي الحسابات إذا كان الرابط غير صالح
            break
            
        except ChannelPrivateError:
            last_error = f"القناة خاصة أو الحساب {session_id} ليس عضواً فيها"
            logger.warning(last_error)
            
        except FloodWaitError as e:
            last_error = f"حد المعدل للحساب {session_id}: انتظار {e.seconds} ثانية"
            logger.warning(last_error)
            
        except Exception as e:
            last_error = f"خطأ في الحساب {session_id}: {str(e)}"
            logger.error(last_error, exc_info=True)
            
        finally:
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass
    
    # التحقق من نجاح التحقق
    if not successful_validation or not entity:
        if "رابط القناة أو اسم المستخدم غير صالح" in str(last_error):
            await checking_msg.edit_text("❌ رابط القناة أو اسم المستخدم غير صالح. يرجى التحقق والمحاولة مرة أخرى.")
            return ENTER_CHANNEL
        elif last_error and "خاصة" in last_error:
            error_msg = (
                "⚠️ لا يمكن الوصول للقناة من أي من الحسابات المتاحة.\n\n"
                "💡 تأكد من:\n"
                "• أن الحسابات أعضاء في القناة\n"
                "• أن القناة ليست محظورة\n"
                "• أن رابط القناة صحيح"
            )
            await checking_msg.edit_text(error_msg)
            return ENTER_CHANNEL
        else:
            error_msg = f"❌ فشل في التحقق من القناة من جميع الحسابات المتاحة"
            if last_error:
                error_msg += f"\n\nآخر خطأ: {last_error}"
            await checking_msg.edit_text(error_msg)
            return ConversationHandler.END
    
    # حفظ معلومات القناة
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
    
    success_msg = (
        f"✅ تم التحقق من القناة: <b>{entity.title}</b>\n\n"
        "اختر طريقة تحديد المنشورات للإبلاغ:"
    )
    
    # إضافة معلومات إضافية عن القناة
    try:
        if hasattr(entity, 'participants_count') and entity.participants_count:
            success_msg += f"\n👥 عدد الأعضاء: {entity.participants_count:,}"
    except:
        pass
    
    await checking_msg.edit_text(
        success_msg,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECT_POSTS_OPTION

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
    """جلب المنشورات من القناة حسب الخيار المحدد - محسن مع دعم حسابات متعددة"""
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
    else:
        msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=loading_text)

    channel_entity_id = context.user_data["channel"]
    accounts = context.user_data.get("accounts", [])
    proxies = context.user_data.get("proxies", [])
    
    if not accounts:
        await msg.edit_text("❌ لا توجد حسابات صالحة لجلب المنشورات.")
        return ConversationHandler.END
    
    posts = []
    successful_fetch = False
    last_error = None
    
    # محاولة استخدام حسابات متعددة مع آلية fallback
    for attempt, session_data in enumerate(accounts):
        session_str = session_data.get("session")
        session_id = session_data.get("id", f"حساب-{attempt+1}")
        
        if not session_str:
            logger.warning(f"تخطي الحساب {session_id}: جلسة فارغة")
            continue
            
        client = None
        current_proxy = None
        
        try:
            # اختيار بروكسي عشوائي إن وُجد
            if proxies:
                import random
                current_proxy = random.choice(proxies)
                logger.info(f"استخدام البروكسي {current_proxy['server']} مع الحساب {session_id}")
            
            # إعداد العميل مع البروكسي
            params = {
                "api_id": API_ID,
                "api_hash": API_HASH,
                "timeout": 30,
                "device_model": f"PostFetcher-{session_id}",
                "system_version": "4.0.0",
                "app_version": "4.0.0"
            }
            
            if current_proxy:
                from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
                params.update({
                    "connection": ConnectionTcpMTProxyRandomizedIntermediate,
                    "proxy": (current_proxy["server"], current_proxy["port"], current_proxy["secret"])
                })
            
            client = TelegramClient(StringSession(session_str), **params)
            
            # تحديث رسالة التحميل
            try:
                await msg.edit_text(f"{loading_text}\n🔄 محاولة مع الحساب {session_id}...")
            except Exception:
                pass
            
            await client.connect()
            
            if not await client.is_user_authorized():
                logger.warning(f"الحساب {session_id} غير مفوض")
                continue
            
            # جلب المنشورات حسب النوع المحدد
            if fetch_type == 'recent':
                limit = context.user_data['fetch_limit']
                logger.info(f"جلب آخر {limit} منشور من {channel_entity_id}")
                
                async for message in client.iter_messages(channel_entity_id, limit=limit):
                    if message.id:  # تأكد من وجود معرف الرسالة
                        posts.append({"channel": channel_entity_id, "message_id": message.id})
                
            elif fetch_type == 'media':
                limit = context.user_data['fetch_limit']
                media_posts_count = 0
                logger.info(f"جلب آخر {limit} منشور يحتوي على وسائط من {channel_entity_id}")
                
                # البحث عن منشورات تحتوي على وسائط
                async for message in client.iter_messages(channel_entity_id, limit=limit * 3):  # جلب أكثر للعثور على الوسائط
                    if message.media and message.id:
                        posts.append({"channel": channel_entity_id, "message_id": message.id})
                        media_posts_count += 1
                        if media_posts_count >= limit:
                            break
                
            elif fetch_type == 'date':
                days = context.user_data['days']
                from datetime import datetime, timedelta
                
                # حساب التاريخ المطلوب (منذ X أيام)
                target_date = datetime.now() - timedelta(days=days)
                logger.info(f"جلب المنشورات من {target_date.strftime('%Y-%m-%d')} حتى الآن")
                
                # جلب المنشورات من التاريخ المحدد
                message_count = 0
                async for message in client.iter_messages(channel_entity_id, limit=None):
                    if message.date and message.id:
                        # إذا كانت الرسالة أحدث من التاريخ المحدد
                        if message.date >= target_date:
                            posts.append({"channel": channel_entity_id, "message_id": message.id})
                            message_count += 1
                        else:
                            # وصلنا لتاريخ أقدم من المطلوب، توقف
                            break
                    
                    # حد أقصى للأمان (تجنب الحلقة اللانهائية)
                    if message_count >= 1000:
                        logger.warning("تم الوصول للحد الأقصى من المنشورات (1000)")
                        break
            
            successful_fetch = True
            logger.info(f"✅ تم جلب {len(posts)} منشور بنجاح باستخدام الحساب {session_id}")
            break  # نجح الجلب، لا حاجة لمحاولة حسابات أخرى
            
        except ChannelPrivateError:
            last_error = f"القناة خاصة أو الحساب {session_id} ليس عضواً فيها"
            logger.warning(last_error)
            
        except PeerIdInvalidError:
            last_error = f"معرف القناة غير صالح للحساب {session_id}"
            logger.warning(last_error)
            
        except FloodWaitError as e:
            last_error = f"حد المعدل للحساب {session_id}: انتظار {e.seconds} ثانية"
            logger.warning(last_error)
            # لا نتوقف هنا، نجرب الحساب التالي
            
        except Exception as e:
            last_error = f"خطأ في الحساب {session_id}: {str(e)}"
            logger.error(last_error, exc_info=True)
            
        finally:
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass
    
    # التحقق من نجاح العملية
    if not successful_fetch:
        error_msg = f"❌ فشل في جلب المنشورات من جميع الحسابات المتاحة"
        if last_error:
            error_msg += f"\n\nآخر خطأ: {last_error}"
        
        error_msg += f"\n\n💡 تأكد من:\n• أن الحسابات أعضاء في القناة\n• أن رابط القناة صحيح\n• أن القناة ليست محظورة"
        
        await msg.edit_text(error_msg)
        return ConversationHandler.END

    if not posts:
        await msg.edit_text("❌ لم يتم العثور على أي منشورات تطابق المعايير في هذه القناة.")
        return ConversationHandler.END
    
    # إزالة المنشورات المكررة (في حالة وجودها)
    unique_posts = []
    seen_ids = set()
    for post in posts:
        if post["message_id"] not in seen_ids:
            unique_posts.append(post)
            seen_ids.add(post["message_id"])
    
    context.user_data["targets"] = unique_posts
    
    # رسالة النجاح مع تفاصيل
    success_msg = f"✅ تم جلب {len(unique_posts)} منشور بنجاح"
    
    if fetch_type == 'recent':
        success_msg += f" (آخر {context.user_data['fetch_limit']} منشور)"
    elif fetch_type == 'media':
        success_msg += f" (منشورات تحتوي على وسائط)"
    elif fetch_type == 'date':
        success_msg += f" (من آخر {context.user_data['days']} يوم)"
    
    success_msg += "\n\nالآن، أرسل رسالة تفصيلية للبلاغ (أو أرسل /skip للتخطي):"
    
    await msg.edit_text(success_msg)
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
        asyncio.create_task(run_enhanced_report_process(update, context))
        
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
    fallbacks=[
        CallbackQueryHandler(cancel_operation, pattern='^cancel$'),
        CommandHandler('cancel', cancel_operation),
        MessageHandler(filters.Regex(r'^/cancel$'), cancel_operation),
    ],
    per_user=True,
)
