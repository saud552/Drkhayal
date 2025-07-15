# DrKhayal/Telegram/common.py

import asyncio
import sqlite3
import base64
import logging
import time
import random
import re
from urllib.parse import urlparse, parse_qs

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from telethon import TelegramClient, functions, types, utils
from telethon.errors import (
    AuthKeyDuplicatedError,
    FloodWaitError,
    PeerFloodError,
    SessionPasswordNeededError,
    RPCError  
)
from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
from telethon.sessions import StringSession
from encryption import decrypt_session
from config import API_ID, API_HASH
from add import safe_db_query

logger = logging.getLogger(__name__)
# استيراد DB_PATH من config.py
try:
    from config import DB_PATH
except ImportError:
    DB_PATH = 'accounts.db'  # قيمة افتراضية

# استثناءات مخصصة
class TemporaryFailure(Exception):
    """فشل مؤقت يمكن إعادة المحاولة عليه"""
    pass

class SessionExpired(Exception):
    """انتهت صلاحية الجلسة"""
    pass

class PermanentFailure(Exception):
    """فشل دائم يتطلب تخطي الحساب"""
    pass
    
# --- الثوابت المشتركة ---
REPORT_TYPES = {
    2: ("رسائل مزعجة", types.InputReportReasonSpam()),
    3: ("إساءة أطفال", types.InputReportReasonChildAbuse()),
    4: ("محتوى جنسي", types.InputReportReasonPornography()),
    5: ("عنف", types.InputReportReasonViolence()),
    6: ("انتهاك خصوصية", types.InputReportReasonPersonalDetails()),
    7: ("مخدرات", types.InputReportReasonIllegalDrugs()),
    8: ("حساب مزيف", types.InputReportReasonFake()),
    9: ("حقوق النشر", types.InputReportReasonCopyright()),
    11: ("أخرى", types.InputReportReasonOther()),
}

# --- دوال مساعدة مشتركة محسنة ---

def parse_message_link(link: str) -> dict | None:
    """تحليل رابط رسالة تليجرام لاستخراج اسم القناة ومعرف الرسالة"""
    try:
        # النمط الأساسي: https://t.me/channel/123
        base_pattern = r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)"
        match = re.search(base_pattern, link)
        if match:
            return {
                "channel": match.group(1),
                "message_id": int(match.group(2))
            }
        
        # النمط مع المعرف الخاص: https://t.me/c/1234567890/123
        private_pattern = r"https?://t\.me/c/(\d+)/(\d+)"
        match = re.search(private_pattern, link)
        if match:
            return {
                "channel": int(match.group(1)),
                "message_id": int(match.group(2))
            }
        
        logger.warning(f"رابط غير معترف به: {link}")
        return None
    except Exception as e:
        logger.error(f"خطأ في تحليل الرابط: {e}")
        return None

# --- دوال قاعدة البيانات ---
def get_categories():
    """استرجاع قائمة الفئات مع عدد الحسابات في كل منها"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.name, COUNT(a.id) 
        FROM categories c
        LEFT JOIN accounts a ON c.id = a.category_id
        WHERE c.is_active = 1
        GROUP BY c.id
        ORDER BY c.created_at DESC
    """)
    categories = cursor.fetchall()
    conn.close()
    return categories

def get_accounts(category_id):
    query = """
        SELECT id, session_str, phone, device_info, 
               proxy_type, proxy_server, proxy_port, proxy_secret
        FROM accounts
        WHERE category_id = ?
    """
    results = safe_db_query(query, (category_id,), is_write=False)
    
    accounts = []
    for row in results:
        try:
            decrypted_session = decrypt_session(row[1])
            accounts.append({
                "id": row[0],
                "session": decrypted_session,
                "phone": row[2],
                "device_info": eval(row[3]) if row[3] else {},
                "proxy_type": row[4],
                "proxy_server": row[5],
                "proxy_port": row[6],
                "proxy_secret": row[7],
            })
        except Exception as e:
            logging.error(f"خطأ في فك تشفير الجلسة للحساب {row[0]}: {str(e)}")
    
    return accounts

def parse_proxy_link(link: str) -> dict | None:
    """
    يحلل رابط بروكسي MTProto من نوع tg://proxy أو https://t.me/proxy ويستخرج المضيف والمنفذ والسرّ.
    يدعم المفاتيح الهكسية (مع بادئة dd أو ee أو بدونها) والمشفّرة بـ base64 URL-safe.
    """
    try:
        parsed = urlparse(link)
        params = parse_qs(parsed.query)

        # محاولة استخراج المعلمات من query string
        server = params.get('server', [''])[0]
        port = params.get('port', [''])[0]
        secret = params.get('secret', [''])[0]

        # إذا لم تُعثر المعلمات في query، حاول من المسار
        if not server or not port or not secret:
            path_parts = parsed.path.lstrip('/').split('/')
            if len(path_parts) >= 3:
                server = path_parts[0]
                port = path_parts[1]
                secret = '/'.join(path_parts[2:])

        if not server or not port or not secret:
            # رابط غير صالح
            return None

        server = server.strip()
        port = int(port)
        secret = secret.strip()

        # تحويل السر إلى تنسيق سداسي ثابت
        hex_secret = convert_secret(secret)
        if not hex_secret:
            return None

        return {'server': server, 'port': port, 'secret': hex_secret, 'format': 'hex'}
    except Exception as e:
        logger.error(f"خطأ في تحليل رابط البروكسي: {e}")
        return None
        
def convert_secret(secret: str) -> str | None:
    """
    يحول سلسلة السرّ إلى تمثيل هكس ثابت (32-64 حرفًا أو أكثر).
    يدعم الصيغ الهكسية ونصوص base64 URL-safe.
    """
    secret = secret.strip()

    # إزالة أي أحرف غير سداسية
    clean_secret = re.sub(r'[^A-Fa-f0-9]', '', secret)
    
    # إذا كان السرّ نص هكس (مجموعة [0-9A-Fa-f] فقط بطول زوجي)
    if re.fullmatch(r'[A-Fa-f0-9]+', clean_secret) and len(clean_secret) % 2 == 0:
        return clean_secret.lower()  # نعيدها بالصيغة العادية (أحرف صغيرة)
    
    # محاولة فك base64 URL-safe
    try:
        # إزالة البادئات الشائعة (ee, dd)
        if secret.startswith(('ee', 'dd')):
            secret = secret[2:]
            
        # إضافة الحشو المفقود
        cleaned = secret.replace('-', '+').replace('_', '/')
        padding = '=' * (-len(cleaned) % 4)
        decoded = base64.b64decode(cleaned + padding)
        
        # التحويل إلى سلسلة سداسية (hex string)
        return decoded.hex()
    except Exception as e:
        logger.error(f"خطأ في تحويل السر: {e}")
        return None

# --- نظام فحص وتدوير البروكسي ---
class ProxyChecker:
    def __init__(self):
        self.proxy_stats = {}
        self.check_intervals = [5, 10, 15, 30, 60]  # ثواني بين الفحوصات

    async def check_proxy(self, session_str: str, proxy_info: dict) -> dict:
        """فحص جودة البروكسي مع دعم السرود 32/64 حرفًا"""
        start_time = time.time()
        client = None
        result = proxy_info.copy()
        
        try:
            # إعداد معلمات العميل
            params = {
                "api_id": API_ID,
                "api_hash": API_HASH,
                "timeout": 10,
                "connection": ConnectionTcpMTProxyRandomizedIntermediate,
            }
            
            # معالجة السر - يجب أن يكون في تنسيق سداسي
            secret = proxy_info["secret"]
            
            # تأكد أن السر هو سلسلة نصية (str)
            if isinstance(secret, bytes):
                try:
                    secret = secret.decode('utf-8')
                except UnicodeDecodeError:
                    # إذا فشل التحويل، نستخدم التمثيل السداسي للبايتات
                    secret = secret.hex()
            
            # تحويل السر إلى بايتات
            try:
                secret_bytes = bytes.fromhex(secret)
            except ValueError:
                logger.error(f"❌ سر البروكسي غير صالح: {secret}")
                result.update({
                    "ping": 0,
                    "response_time": 0,
                    "last_check": int(time.time()),
                    "status": "invalid_secret",
                    "error": "تنسيق سر غير صالح"
                })
                return result
            
            # إنشاء كائن البروكسي المناسب
            params["proxy"] = (
                proxy_info["server"],
                proxy_info["port"],
                secret_bytes
            )
            
            # إنشاء العميل والتوصيل
            client = TelegramClient(StringSession(session_str), **params)
            await client.connect()
            
            # قياس سرعة الاتصال
            connect_time = time.time() - start_time
            
            # فحص فعالية البروكسي بمحاولة جلب معلومات بسيطة
            start_req = time.time()
            await client.get_me()
            response_time = time.time() - start_req
            
            result.update({
                "ping": int(connect_time * 1000),
                "response_time": int(response_time * 1000),
                "last_check": int(time.time()),
                "status": "active"
            })
            
        except RPCError as e:
            result.update({
                "ping": 0,
                "response_time": 0,
                "last_check": int(time.time()),
                "status": "connection_error",
                "error": str(e)
            })
        except Exception as e:
            result.update({
                "ping": 0,
                "response_time": 0,
                "last_check": int(time.time()),
                "status": "error",
                "error": str(e)
            })
        finally:
            if client and client.is_connected():
                await client.disconnect()
        
        # تحديث إحصائيات البروكسي
        self.proxy_stats[proxy_info["server"]] = result
        return result

    @staticmethod
    def parse_proxy_link(link: str) -> dict | None:
        """استدعاء الدالة المركزية لتحليل روابط البروكسي"""
        return parse_proxy_link(link)

    def get_best_proxy(self, proxies: list) -> dict:
        """الحصول على أفضل بروكسي بناءً على الإحصائيات"""
        if not proxies:
            return None
            
        # تصفية البروكسيات النشطة فقط
        active_proxies = [p for p in proxies if p.get('status') == 'active']
        
        if not active_proxies:
            return None
        
        # اختيار البروكسي مع أفضل وقت استجابة
        return min(active_proxies, key=lambda x: x.get('ping', 10000))

    def needs_check(self, proxy_info: dict) -> bool:
        """تحديد إذا كان البروكسي يحتاج فحصًا"""
        last_check = proxy_info.get('last_check', 0)
        interval = random.choice(self.check_intervals)
        return (time.time() - last_check) > interval

    def rotate_proxy(self, proxies: list, current_proxy: dict) -> dict:
        """تدوير البروكسي بشكل ذكي"""
        if not proxies or len(proxies) < 2:
            return current_proxy
            
        # استبعاد البروكسي الحالي
        available_proxies = [p for p in proxies if p != current_proxy]
        
        # تصنيف البروكسي حسب الجودة
        active_proxies = sorted(
            [p for p in available_proxies if p.get('status') == 'active'],
            key=lambda x: x['response_time']
        )
        
        if not active_proxies:
            return current_proxy
            
        # إذا كانت هناك بروكسي أفضل بنسبة 20% على الأقل
        if current_proxy and active_proxies[0]['response_time'] < current_proxy.get('response_time', 10000) * 0.8:
            return active_proxies[0]
            
        # إذا كان البروكسي الحالي بطيئًا جدًا
        if current_proxy and current_proxy.get('response_time', 0) > 5000:  # أكثر من 5 ثواني
            return active_proxies[0]
            
        return current_proxy if current_proxy else active_proxies[0]

# إنشاء نسخة عامة من مدقق البروكسي
proxy_checker = ProxyChecker()

# --- الفئة الأساسية المحسنة لتنفيذ البلاغات ---
class AdvancedReporter:
    """فئة مخصصة لتنظيم وتنفيذ عمليات الإبلاغ مع دعم تدوير البروكسي"""
    def __init__(self, client: TelegramClient, context: ContextTypes.DEFAULT_TYPE):
        self.client = client
        self.context = context
        self.stats = {"success": 0, "failed": 0, "last_report": None}

    async def dynamic_delay(self, delay: float):
        """تضمن وجود فاصل زمني بين عمليات الإبلاغ مع تقليل زمن الانتظار"""
        if self.stats["last_report"]:
            elapsed = time.time() - self.stats["last_report"]
            if elapsed < delay:
                wait = delay - elapsed
                logger.info(f"⏳ تأخير {wait:.1f} ثانية")
                await asyncio.sleep(wait)
        self.stats["last_report"] = time.time()

    async def resolve_target(self, target: str | dict):
        """تحول الهدف (رابط، يوزر) إلى كائن يمكن استخدامه في تيليثون"""
        try:
            # إذا كان الرابط يحتوي على معرف رسالة
            if isinstance(target, str) and 't.me/' in target:
                parsed = parse_message_link(target)
                if parsed:
                    entity = await self.client.get_entity(parsed["channel"])
                    return {
                        "channel": utils.get_input_peer(entity),
                        "message_id": parsed["message_id"]
                    }
            
            # إذا كان الهدف معرف قناة/دردشة
            if isinstance(target, str):
                entity = await self.client.get_entity(target)
                return utils.get_input_peer(entity)
            
            # إذا كان الهدف كائنًا جاهزًا
            if isinstance(target, dict) and "message_id" in target:
                entity = await self.client.get_entity(target["channel"])
                return {
                    "channel": utils.get_input_peer(entity),
                    "message_id": target["message_id"]
                }
                
            return None
        except Exception as e:
            logger.error(f"❌ خطأ في حل الهدف {target}: {e}")
            return None

    async def execute_report(self, target, reason_obj, method_type, message, reports_per_account, cycle_delay):
        """تنفذ بلاغًا فرديًا مع تحسينات في معالجة الأخطاء"""
        target_obj = await self.resolve_target(target)
        if not target_obj:
            self.stats["failed"] += reports_per_account
            return False

        for _ in range(reports_per_account):
            if not self.context.user_data.get("active", True): 
                return False
            try:
                await self.dynamic_delay(cycle_delay)

                if method_type == "peer":
                    # حساب الإبلاغ عن المستخدم/القناة باستخدام reason ككائن TL
                    await self.client(functions.account.ReportPeerRequest(
                        peer=target_obj,
                        reason=reason_obj,
                        message=message
                    ))
                    self.stats["success"] += 1
                    logger.info(f"✅ تم الإبلاغ بنجاح على {target}")

                elif method_type == "message":
                    # الإبلاغ عن رسالة مع اختيار السبب ديناميكيًا
                    peer = target_obj["channel"]
                    msg_id = target_obj["message_id"]

                    # الخطوة الأولى: طلب الخيارات دون رسالة نصية (empty)
                    result = await self.client(functions.messages.ReportRequest(
                        peer=peer,
                        id=[msg_id],
                        option=b'',
                        message=''
                    ))
                    # إذا لزم الاختيار:
                    if isinstance(result, types.ReportResultChooseOption):
                        # محاولة العثور على الخيار المناسب بناءً على reason_obj
                        chosen_option = None
                        # نطابق اسم السبب العربي أو المفتاح؟ هنا نطابق حسب النوع
                        for opt in result.options:
                            # opt.text قد يحتوي نص الخيار (مثل "Spam", "Child Abuse", إلخ.)
                            if reason_obj.__class__.__name__.lower().find(opt.text.lower()) != -1 or reason_obj.__class__.__name__.lower() == opt.text.lower():
                                chosen_option = opt.option
                                break
                        # إذا لم نجد تطابقًا، نأخذ الخيار الأول افتراضيًا
                        if not chosen_option and result.options:
                            chosen_option = result.options[0].option

                        # الخطوة الثانية: إرسال البلاغ مع الخيار المحدد ونص الرسالة
                        await self.client(functions.messages.ReportRequest(
                            peer=peer,
                            id=[msg_id],
                            option=chosen_option or b'',
                            message=message
                        ))
                    # في حال تم الإبلاغ مباشرة أو إضافة تعليق:
                    self.stats["success"] += 1
                    logger.info(f"✅ تم الإبلاغ بنجاح على الرسالة {msg_id}")

                elif method_type == "photo":
                    photos = await self.client.get_profile_photos(target_obj, limit=1)
                    if not photos:
                        logger.error(f"❌ لا توجد صورة للملف الشخصي للهدف: {target}")
                        self.stats["failed"] += 1
                        continue
                    photo_input = types.InputPhoto(
                        id=photos[0].id,
                        access_hash=photos[0].access_hash,
                        file_reference=photos[0].file_reference
                    )
                    await self.client(functions.account.ReportProfilePhotoRequest(
                        peer=target_obj,
                        photo_id=photo_input,
                        reason=reason_obj,
                        message=message
                    ))
                    self.stats["success"] += 1
                    logger.info(f"✅ تم الإبلاغ بنجاح على صورة الملف الشخصي لـ {target}")

                elif method_type == "sponsored":
                    # الإبلاغ عن منشور ممول ديناميكيًا
                    random_id = base64.urlsafe_b64decode(target)
                    # الخطوة الأولى: طلب خيارات البلاغ دون تحديد الخيار
                    result = await self.client(functions.messages.ReportSponsoredMessageRequest(
                        random_id=random_id,
                        option=b''
                    ))
                    # إذا لزم الأمر اختيار خيار:
                    if isinstance(result, types.SponsoredMessageReportResultChooseOption):
                        # اختر أول خيار (أو بناءً على شيء محدد)
                        if result.options:
                            chosen_option = result.options[0].option
                            await self.client(functions.messages.ReportSponsoredMessageRequest(
                                random_id=random_id,
                                option=chosen_option
                            ))
                    self.stats["success"] += 1
                    logger.info(f"✅ تم الإبلاغ بنجاح على المنشور الممول {target}")

            except (FloodWaitError, PeerFloodError) as e:
                wait_time = e.seconds if isinstance(e, FloodWaitError) else 300  # افتراضي 5 دقائق للـ PeerFlood
                logger.warning(f"⏳ توقف بسبب {type(e).__name__}. سيتم الانتظار لـ {wait_time} ثانية.")
                await asyncio.sleep(wait_time + 5)
            except Exception as e:
                self.stats["failed"] += 1
                logger.error(f"❌ فشل الإبلاغ: {type(e).__name__} - {e}")

        return True

    async def execute_mass_report(self, targets, reason_obj, message):
        """تنفذ بلاغًا جماعيًا على عدة منشورات دفعة واحدة مع تحسين الأداء"""
        if not targets:
            return
        
        try:
            # استخراج اسم القناة وكائناتها وقائمة الرسائل
            channel_username = targets[0]["channel"]
            entity = await self.client.get_entity(channel_username)
            peer = utils.get_input_peer(entity)
            message_ids = [t["message_id"] for t in targets]

            # إرسال الطلب الأولي للحصول على خيارات البلاغ
            result = await self.client(functions.messages.ReportRequest(
                peer=peer,
                id=message_ids,
                option=b'',
                message=''
            ))
            # إذا تم إرجاع خيارات، اختر المناسب وأعد الطلب
            if isinstance(result, types.ReportResultChooseOption):
                chosen_option = None
                for opt in result.options:
                    if reason_obj.__class__.__name__.lower().find(opt.text.lower()) != -1 or reason_obj.__class__.__name__.lower() == opt.text.lower():
                        chosen_option = opt.option
                        break
                if not chosen_option and result.options:
                    chosen_option = result.options[0].option
                await self.client(functions.messages.ReportRequest(
                    peer=peer,
                    id=message_ids,
                    option=chosen_option or b'',
                    message=message
                ))

            count = len(message_ids)
            self.stats["success"] += count
            logger.info(f"✅ تم إرسال بلاغ جماعي ناجح على {count} منشور.")
        except Exception as e:
            self.stats["failed"] += len(targets)
            logger.error(f"❌ فشل البلاغ الجماعي: {type(e).__name__} - {e}", exc_info=True)

# --- دوال تشغيل العملية المحسنة ---
async def do_session_report(session_data: dict, config: dict, context: ContextTypes.DEFAULT_TYPE):
    """تنفذ جميع البلاغات المطلوبة لحساب (جلسة) واحد مع إدارة أفضل للموارد"""
    session_str = session_data.get("session")
    proxies = config.get("proxies", [])
    client, connected = None, False
    
    # تدوير البروكسي - اختيار الأفضل
    current_proxy = None
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries and context.user_data.get("active", True):
        # تدوير البروكسي
        current_proxy = proxy_checker.rotate_proxy(proxies, current_proxy)
        
        try:
            # إعداد معلمات العميل
            params = {
                "api_id": API_ID,
                "api_hash": API_HASH,
                "timeout": 15,
                "device_model": "Reporter Bot",
                "system_version": "1.0",
                "app_version": "1.0"
            }
            
            if current_proxy:
                # السر في قاعدة البيانات هو سلسلة هكس (تم تحويله مسبقًا)
                secret_hex = current_proxy["secret"]
                
                # تأكد أن السر هو سلسلة نصية (str)
                if isinstance(secret_hex, bytes):
                    try:
                        secret_hex = secret_hex.decode('utf-8')
                    except UnicodeDecodeError:
                        # إذا فشل التحويل، نستخدم التمثيل السداسي للبايتات
                        secret_hex = secret_hex.hex()
                
                try:
                    # تحويل السر السداسي إلى بايتات
                    secret_bytes = bytes.fromhex(secret_hex)
                except ValueError:
                    logger.error(f"❌ سر البروكسي غير صالح: {secret_hex}")
                    current_proxy['status'] = 'invalid_secret'
                    retry_count += 1
                    continue
                
                # إنشاء كائن البروكسي
                params.update({
                    "connection": ConnectionTcpMTProxyRandomizedIntermediate,
                    "proxy": (
                        current_proxy["server"],
                        current_proxy["port"],
                        secret_bytes
                    )
                })
                logger.info(f"Using proxy: {current_proxy['server']} (converted secret)")
            
            client = TelegramClient(StringSession(session_str), **params)
            await client.connect()
            
            # التحقق من تفعيل الجلسة
            if not await client.is_user_authorized():
                logger.warning("⚠️ الجلسة غير مصرح لها.")
                return
            
            connected = True
            reporter = AdvancedReporter(client, context)
            method_type = config.get("method_type")
            targets_list = config.get("targets", [])
            reports_per_account = config.get("reports_per_account", 1)
            cycle_delay = config.get("cycle_delay", 1)

            # تنفيذ الإبلاغ حسب النوع
            if method_type == "mass":
                await reporter.execute_mass_report(targets_list, config["reason_obj"], config.get("message", ""))
            else:
                for _ in range(reports_per_account):
                    if not context.user_data.get("active", True): 
                        break
                    
                    for target in targets_list:
                        if not context.user_data.get("active", True):
                            break
                        
                        await reporter.execute_report(
                            target, config["reason_obj"], method_type,
                            config.get("message", ""), 1, cycle_delay
                        )

            # تحديث الإحصائيات
            lock = context.bot_data.setdefault('progress_lock', asyncio.Lock())
            async with lock:
                context.user_data["progress_success"] = context.user_data.get("progress_success", 0) + reporter.stats["success"]
                context.user_data["progress_failed"] = context.user_data.get("progress_failed", 0) + reporter.stats["failed"]
            
            break  # الخروج عند النجاح

        except (RPCError, TimeoutError) as e:
            retry_count += 1
            if current_proxy:
                current_proxy['status'] = 'connection_failed'
                current_proxy['error'] = str(e)
                logger.warning(f"❌ فشل الاتصال بالبروكسي {current_proxy['server']}: {e}")
            if retry_count < max_retries:
                logger.info(f"⏳ إعادة المحاولة {retry_count}/{max_retries}...")
                await asyncio.sleep(2)  # انتظار قبل إعادة المحاولة
            else:
                logger.error(f"❌ فشل الاتصال بعد {max_retries} محاولات.")
        except (AuthKeyDuplicatedError, SessionPasswordNeededError) as e:
            logger.error(f"❌ مشكلة في الجلسة: {type(e).__name__}")
            break
        except Exception as e:
            logger.error(f"❌ خطأ فادح في جلسة: {e}", exc_info=True)
            break
        finally:
            if client and client.is_connected():
                await client.disconnect()

async def run_report_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = context.user_data
    sessions = config.get("accounts", [])
    if not sessions:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ لا توجد حسابات صالحة لبدء العملية.")
        return

    targets = config.get("targets", [])
    reports_per_account = config.get("reports_per_account", 1)

    total_reports = len(sessions) * len(targets) * reports_per_account

    # تهيئة متغيرات التتبع
    config["total_reports"] = total_reports
    config["progress_success"] = 0
    config["progress_failed"] = 0
    config["active"] = True
    config["lock"] = asyncio.Lock()  # قفل للعمليات المتزامنة
    config["failed_reports"] = 0  # للإبلاغات الفاشلة المؤقتة

    proxies = config.get("proxies", [])
    
    try:
        progress_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⏳ جاري إعداد عملية الإبلاغ...",
            parse_mode="HTML"
        )
        context.user_data["progress_message"] = progress_message
    except Exception as e:
        logger.error(f"فشل في إرسال رسالة التقدم: {str(e)}")
        return
    
    session_tasks = []
    monitor_task = None
    
    try:
        # إنشاء مهام مع التعامل الفردي مع كل جلسة
        for session in sessions:
            task = asyncio.create_task(
                process_single_account(
                    session, 
                    targets, 
                    reports_per_account,
                    config,
                    context
                )
            )
            session_tasks.append(task)
        
        context.user_data["tasks"] = session_tasks

        # مراقبة البروكسي (إن وجد)
        if proxies:
            async def monitor_proxies():
                while config.get("active", True):
                    try:
                        await asyncio.sleep(30)
                        current_proxies = config.get("proxies", [])
                        for proxy in current_proxies:
                            if proxy_checker.needs_check(proxy):
                                updated = await proxy_checker.check_proxy(sessions[0]["session"], proxy)
                                proxy.update(updated)
                    except asyncio.CancelledError:
                        logger.info("تم إلغاء مهمة مراقبة البروكسي")
                        return
                    except Exception as e:
                        logger.warning(f"خطأ في فحص البروكسي: {str(e)}")
        
            monitor_task = asyncio.create_task(monitor_proxies())

        start_timestamp = time.time()
        last_update_timestamp = start_timestamp
        
        if monitor_task:
        	context.user_data["monitor_task"] = monitor_task  # حفظ المرجع للإلغاء
        
        # تحديث التقدم الرئيسي
        while config.get("active", True) and any(not t.done() for t in session_tasks):
            async with config["lock"]:
                success = config["progress_success"]
                failed = config["progress_failed"]
                temp_failed = config["failed_reports"]
                total_failed = failed + temp_failed
                
            completed = success + total_failed
            total = config.get("total_reports", 1)
            progress_percent = min(100, int((completed / total) * 100))
            
            remaining = total - completed
            
            current_timestamp = time.time()
            elapsed = current_timestamp - start_timestamp
            
            if completed > 0 and elapsed > 0:
                speed = completed / elapsed
                eta_seconds = remaining / speed if speed > 0 else 0
                
                hours, remainder = divmod(eta_seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                
                if hours > 0:
                    eta_str = f"{int(hours)}:{int(minutes):02d}:{int(seconds):02d}"
                else:
                    eta_str = f"{int(minutes)}:{int(seconds):02d}"
            else:
                eta_str = "تقدير..."
            
            filled_length = int(20 * (progress_percent / 100))
            progress_bar = "[" + "■" * filled_length + "□" * (20 - filled_length) + "]"
            
            text = (
                f"📊 <b>تقدم الإبلاغات</b>\n\n"
                f"{progress_bar} {progress_percent}%\n\n"
                f"▫️ الإجمالي المطلوب: {total}\n"
                f"✅ الناجحة: {success}\n"
                f"❌ الفاشلة: {total_failed} (مؤقتة: {temp_failed})\n"
                f"⏳ المتبقية: {max(0, remaining)}\n"
                f"⏱ الوقت المتوقع: {eta_str}"
            )
            
            try:
                await context.bot.edit_message_text(
                    chat_id=progress_message.chat_id, 
                    message_id=progress_message.message_id, 
                    text=text,
                    parse_mode="HTML"
                )
                last_update_timestamp = current_timestamp
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    if "Message to edit not found" in str(e):
                        logger.warning("رسالة التقدم غير موجودة، توقف التحديثات")
                        break
                    logger.warning(f"فشل تحديث رسالة التقدم: {e}")
            except Exception as e:
                logger.error(f"خطأ غير متوقع أثناء تحديث التقدم: {e}")
                if current_timestamp - last_update_timestamp > 10:
                    logger.error("فشل متكرر في تحديث التقدم، إيقاف التحديثات")
                    break
            
            await asyncio.sleep(5)

        # الحساب النهائي بعد اكتمال المهام
        async with config["lock"]:
            success = config["progress_success"]
            failed = config["progress_failed"]
            temp_failed = config["failed_reports"]
            total_failed = failed + temp_failed
            
        total = config.get("total_reports", 1)
        success_rate = (success / total) * 100 if total > 0 else 0
        
        elapsed_time = time.time() - start_timestamp
        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)
        time_str = f"{minutes}:{seconds:02d}"
        
        final_text = (
            f"✅ <b>اكتملت عمليات الإبلاغ!</b>\n\n"
            f"• الحسابات المستخدمة: {len(sessions)}\n"
            f"• الإبلاغات الناجحة: {success} ({success_rate:.1f}%)\n"
            f"• الإبلاغات الفاشلة: {total_failed}\n"
            f"• الوقت المستغرق: {time_str}"
        )
        
        try:
            await context.bot.edit_message_text(
                chat_id=progress_message.chat_id, 
                message_id=progress_message.message_id, 
                text=final_text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"فشل تحديث الرسالة النهائية: {str(e)}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=final_text,
                parse_mode="HTML"
            )
            
    except asyncio.CancelledError:
        logger.info("تم إلغاء العملية")
    finally:
        config["active"] = False
        
        # إلغاء المهام المتبقية
        for task in session_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"خطأ أثناء إلغاء مهمة: {str(e)}")
        
        if monitor_task and not monitor_task.done():
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"خطأ أثناء إلغاء مراقبة البروكسي: {str(e)}")
        
        # تنظيف البيانات المؤقتة
        config.pop("tasks", None)
        config.pop("active", None)
        config.pop("lock", None)

# الدالة المساعدة لمعالجة الحساب الفردي
async def process_single_account(session, targets, reports_per_account, config, context):
    session_id = session.get("id", "unknown")
    total_reports_for_account = len(targets) * reports_per_account
    account_success = 0
    account_temp_failures = 0
    
    try:
        for target in targets:
            for _ in range(reports_per_account):
                try:
                    # تنفيذ عملية الإبلاغ الفعلية
                    await do_session_report(session, {
                        "targets": [target],
                        "reports_per_account": 1,
                        "reason_obj": config["reason_obj"],
                        "method_type": config["method_type"],
                        "message": config.get("message", ""),
                        "cycle_delay": config.get("cycle_delay", 1),
                        "proxies": config.get("proxies", [])
                    }, context)
                    
                    account_success += 1
                    async with config["lock"]:
                        config["progress_success"] += 1
                        
                except (FloodWaitError, PeerFloodError) as e:
                    # أخطاء مؤقتة من تيليثون
                    logger.warning(f"فشل مؤقت للحساب {session_id}: {str(e)}")
                    account_temp_failures += 1
                    async with config["lock"]:
                        config["failed_reports"] += 1
                        
                except (AuthKeyDuplicatedError, SessionPasswordNeededError) as e:
                    # أخطاء دائمة في الجلسة
                    logger.error(f"فشل دائم للحساب {session_id}: {str(e)}")
                    remaining = total_reports_for_account - (account_success + account_temp_failures)
                    async with config["lock"]:
                        config["progress_failed"] += remaining
                    return
                        
                except Exception as e:
                    # أخطاء عامة
                    logger.error(f"خطأ غير متوقع للحساب {session_id}: {str(e)}")
                    account_temp_failures += 1
                    async with config["lock"]:
                        config["failed_reports"] += 1
                    
    except Exception as e:
        logger.error(f"خطأ جسيم في معالجة الحساب {session_id}: {str(e)}")
        remaining = total_reports_for_account - (account_success + account_temp_failures)
        async with config["lock"]:
            config["progress_failed"] += remaining

async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """تلغي العملية الحالية وتنهي المحادثة."""
    query = update.callback_query
    user_data = context.user_data
    
    # إعلام المستخدم بالإلغاء
    if query and query.message:
        try:
            await query.message.edit_text("🛑 جاري إيقاف العملية...")
        except BadRequest:
            # في حالة عدم وجود رسالة أو مشكلة في التعديل
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="🛑 جاري إيقاف العملية..."
                )
            except Exception:
                pass
    
    # وضع علامة الإلغاء
    user_data["active"] = False
    
    # إلغاء المهام الجارية
    tasks = user_data.get("tasks", [])
    for task in tasks:
        if not task.done():
            try:
                task.cancel()
                await asyncio.sleep(0.1)  # إعطاء وقت للإلغاء
            except Exception as e:
                logger.error(f"خطأ أثناء إلغاء المهمة: {e}")
    
    # إلغاء مهمة مراقبة البروكسي إن وجدت
    monitor_task = user_data.get("monitor_task")
    if monitor_task and not monitor_task.done():
        try:
            monitor_task.cancel()
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"خطأ أثناء إلغاء مراقبة البروكسي: {e}")
    
    # تنظيف بيانات المستخدم
    keys_to_remove = [
        "tasks", "active", "lock", "failed_reports",
        "progress_message", "monitor_task", "accounts",
        "targets", "reason_obj", "method_type"
    ]
    for key in keys_to_remove:
        if key in user_data:
            del user_data[key]
    
    # إرسال رسالة الإلغاء النهائية
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🛑 تم إلغاء العملية بنجاح."
        )
    except Exception as e:
        logger.error(f"خطأ في إرسال رسالة الإلغاء: {e}")
    
    return ConversationHandler.END