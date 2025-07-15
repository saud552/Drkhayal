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

from Telegram.tdlib_client import TDLibClient, FloodWaitError, PeerFloodError, AuthKeyDuplicatedError, SessionPasswordNeededError, RPCError
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
    1: {"label": "لم تعجبني", "subtypes": []},
    2: {"label": "إساءة للأطفال", "subtypes": []},
    3: {"label": "عنف", "subtypes": []},
    4: {"label": "بضائع غير قانونية", "subtypes": [
        "أسلحة",
        "مخدرات",
        "وثائق مزوّرة",
        "أموال مزيفة",
        "بضائع أخرى"
    ]},
    5: {"label": "محتوى غير قانوني للبالغين", "subtypes": [
        "إساءة للأطفال",
        "التحرش والإيحاءات الجنسية",
        "محتوى جنسي غير قانوني آخر"
    ]},
    6: {"label": "معلومات شخصية", "subtypes": [
        "صور خاصة",
        "أرقام هواتف",
        "عناوين",
        "معلومات شخصية أخرى"
    ]},
    7: {"label": "إرهاب", "subtypes": []},
    8: {"label": "احتيال أو إزعاج", "subtypes": [
        "تصيّد",
        "انتحال الشخصية",
        "مبيعات احتيالية",
        "إزعاج"
    ]},
    9: {"label": "حقوق النشر", "subtypes": []},
    10: {"label": "أخرى", "subtypes": []},
    11: {"label": "ليست (غير قانونية)، ولكن يجب إزالتها.", "subtypes": []},
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
        SELECT id, phone, device_info, 
               proxy_type, proxy_server, proxy_port, proxy_secret
        FROM accounts
        WHERE category_id = ?
    """
    results = safe_db_query(query, (category_id,), is_write=False)
    
    accounts = []
    for row in results:
        try:
            accounts.append({
                "id": row[0],
                "phone": row[1],
                "device_info": eval(row[2]) if row[2] else {},
                "proxy_type": row[3],
                "proxy_server": row[4],
                "proxy_port": row[5],
                "proxy_secret": row[6],
            })
        except Exception as e:
            logging.error(f"خطأ في معالجة الحساب {row[0]}: {str(e)}")
    return accounts

def parse_proxy_link(link: str) -> dict | None:
    """
    يحلل رابط بروكسي MTProto من نوع https://t.me/proxy ويستخرج المضيف والمنفذ والسرّ.
    يدعم جميع أنواع الأسرار MTProto (dd وee prefixes).
    
    مثال: https://t.me/proxy?server=example.com&port=8888&secret=eeNEgYdJvXrFGRMC...
    """
    try:
        # تنظيف الرابط
        link = link.strip()
        
        # التعامل مع روابط t.me و tg://
        if link.startswith('tg://proxy'):
            link = link.replace('tg://proxy', 'https://t.me/proxy')
        
        parsed = urlparse(link)
        if parsed.netloc != 't.me' or not parsed.path.startswith('/proxy'):
            logger.warning(f"رابط بروكسي غير معترف به: {link}")
            return None
            
        params = parse_qs(parsed.query)

        # استخراج المعلمات
        server = params.get('server', [''])[0].strip()
        port_str = params.get('port', [''])[0].strip()
        secret = params.get('secret', [''])[0].strip()

        if not server or not port_str or not secret:
            logger.error(f"معلمات ناقصة في رابط البروكسي: server={server}, port={port_str}, secret={'***' if secret else 'None'}")
            return None

        # التحقق من صحة المنفذ
        try:
            port = int(port_str)
            if not (1 <= port <= 65535):
                logger.error(f"منفذ غير صالح: {port}")
                return None
        except ValueError:
            logger.error(f"منفذ غير صالح: {port_str}")
            return None

        # معالجة السر
        processed_secret = convert_secret(secret)
        if not processed_secret:
            logger.error(f"سر بروكسي غير صالح: {secret[:20]}...")
            return None

        return {
            'server': server,
            'port': port,
            'secret': processed_secret,
            'type': 'mtproto',
            'original_secret': secret[:20] + '...' if len(secret) > 20 else secret
        }
        
    except Exception as e:
        logger.error(f"خطأ في تحليل رابط البروكسي: {e}")
        return None
        
def convert_secret(secret: str) -> str | None:
    """
    يحول سلسلة السرّ إلى تمثيل هكس ثابت للاستخدام مع MTProto.
    يدعم:
    - البادئات الشائعة: ee, dd
    - تشفير base64 URL-safe
    - نصوص هكس مباشرة
    """
    if not secret:
        return None
        
    secret = secret.strip()
    original_secret = secret

    try:
        # 1. إذا كان السرّ نص هكس مباشر
        clean_secret = re.sub(r'[^A-Fa-f0-9]', '', secret)
        if re.fullmatch(r'[A-Fa-f0-9]+', clean_secret) and len(clean_secret) % 2 == 0 and len(clean_secret) >= 32:
            logger.debug(f"تم التعرف على سر هكس مباشر: {len(clean_secret)} أحرف")
            return clean_secret.lower()
        
        # 2. معالجة أسرار MTProto مع البادئات
        prefix = None
        if secret.startswith(('ee', 'dd')):
            prefix = secret[:2]
            secret = secret[2:]
            logger.debug(f"تم اكتشاف بادئة: {prefix}")
        
        # 3. فك تشفير base64 URL-safe
        # تحويل URL-safe base64 إلى base64 عادي
        cleaned = secret.replace('-', '+').replace('_', '/')
        
        # إضافة الحشو المفقود
        padding_needed = 4 - (len(cleaned) % 4)
        if padding_needed != 4:
            cleaned += '=' * padding_needed
        
        # فك التشفير
        decoded = base64.b64decode(cleaned)
        
        # إضافة البادئة إذا كانت موجودة
        if prefix:
            prefix_bytes = bytes.fromhex(prefix)
            decoded = prefix_bytes + decoded
        
        # التحويل إلى نص هكس
        hex_result = decoded.hex()
        
        # التحقق من طول السر (يجب أن يكون 32 أو 64 أو 128 بايت على الأقل)
        if len(hex_result) < 32:
            logger.warning(f"سر قصير جداً: {len(hex_result)} أحرف")
            return None
            
        logger.debug(f"تم تحويل السر بنجاح: {len(hex_result)} أحرف hex")
        return hex_result.lower()
        
    except Exception as e:
        logger.error(f"خطأ في تحويل السر '{original_secret[:20]}...': {e}")
        return None

# --- نظام فحص وتدوير البروكسي ---
class ProxyChecker:
    def __init__(self):
        self.proxy_stats = {}
        self.check_intervals = [5, 10, 15, 30, 60]  # ثواني بين الفحوصات

    async def check_proxy(self, phone: str, proxy_info: dict) -> dict:
        """فحص جودة البروكسي MTProto باستخدام TDLib"""
        start_time = time.time()
        client = None
        result = proxy_info.copy()
        
        try:
            # معالجة السر وتحويله للتنسيق الصحيح
            secret = proxy_info["secret"]
            if not secret:
                raise ValueError("سر البروكسي مفقود")
            
            # تأكد أن السر في تنسيق hex
            if isinstance(secret, str) and re.fullmatch(r'[A-Fa-f0-9]+', secret):
                secret_bytes = bytes.fromhex(secret)
            else:
                raise ValueError(f"سر البروكسي ليس في تنسيق hex صحيح: {secret[:20]}...")
            
            # إعداد معلومات البروكسي لـ TDLib
            proxy_config = {
                'type': 'mtproto',
                'server': proxy_info["server"],
                'port': proxy_info["port"],
                'secret': secret_bytes
            }
            
            # إنشاء عميل TDLib مع البروكسي
            client = TDLibClient(
                api_id=API_ID,
                api_hash=API_HASH,
                phone=phone,
                proxy=proxy_config
            )
            
            # قياس وقت الاتصال
            connect_start = time.time()
            await client.start()
            connect_time = time.time() - connect_start
            
            # فحص فعالية البروكسي بجلب معلومات المستخدم
            test_start = time.time()
            me = await client.get_me()
            test_time = time.time() - test_start
            
            if me:
                total_time = time.time() - start_time
                result.update({
                    "ping": int(connect_time * 1000),
                    "response_time": int(test_time * 1000),
                    "total_time": int(total_time * 1000),
                    "last_check": int(time.time()),
                    "status": "active",
                    "user_id": me.get('id') if isinstance(me, dict) else None
                })
                logger.info(f"✅ بروكسي نشط: {proxy_info['server']}:{proxy_info['port']} - ping: {int(connect_time * 1000)}ms")
            else:
                raise Exception("فشل في جلب معلومات المستخدم")
            
        except FloodWaitError as e:
            logger.warning(f"⏳ انتظار مطلوب للبروكسي {proxy_info['server']}: {e.seconds}s")
            result.update({
                "ping": 0,
                "response_time": 0,
                "last_check": int(time.time()),
                "status": "flood_wait",
                "error": f"انتظار {e.seconds} ثانية",
                "retry_after": int(time.time()) + e.seconds
            })
        except Exception as e:
            error_msg = str(e).lower()
            if 'timeout' in error_msg or 'connection' in error_msg:
                status = "timeout"
            elif 'invalid' in error_msg or 'wrong' in error_msg:
                status = "invalid"
            else:
                status = "error"
                
            logger.warning(f"❌ فشل فحص البروكسي {proxy_info['server']}: {e}")
            result.update({
                "ping": 0,
                "response_time": 0,
                "last_check": int(time.time()),
                "status": status,
                "error": str(e)[:200]  # تحديد طول رسالة الخطأ
            })
        finally:
            if client:
                try:
                    await client.stop()
                except:
                    pass
        
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
    def __init__(self, client: TDLibClient, context: ContextTypes.DEFAULT_TYPE):
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

    async def resolve_target(self, target):
        # استخدم دالة tdlib_client مباشرة
        return await self.client.resolve_target(target)

    async def execute_report(self, target, reason_obj, method_type, message, reports_per_account, cycle_delay, subtype_label=None):
        target_obj = await self.resolve_target(target)
        if not target_obj:
            self.stats["failed"] += reports_per_account
            return False

        for _ in range(reports_per_account):
            if not self.context.user_data.get("active", True): 
                return False
            try:
                await self.dynamic_delay(cycle_delay)

                # دمج subtype_label مع نص البلاغ إذا كان موجودًا
                full_message = message
                if subtype_label:
                    if full_message:
                        full_message = f"[{subtype_label}] {full_message}"
                    else:
                        full_message = subtype_label

                if method_type == "peer":
                    await self.client.report_peer(
                        chat_id=target_obj.id,
                        reason=reason_obj,
                        message=full_message
                    )
                    self.stats["success"] += 1
                    logger.info(f"✅ تم الإبلاغ بنجاح على {target}")

                elif method_type == "message":
                    await self.client.report_message(
                        chat_id=target_obj["channel"].id if isinstance(target_obj, dict) else target_obj.id,
                        message_ids=[target_obj["message_id"]] if isinstance(target_obj, dict) else [],
                        reason=reason_obj,
                        message=full_message
                    )
                    self.stats["success"] += 1
                    logger.info(f"✅ تم الإبلاغ بنجاح على الرسالة {target}")

            except Exception as e:
                self.stats["failed"] += 1
                logger.error(f"❌ فشل الإبلاغ: {type(e).__name__} - {e}")

        return True

    async def execute_mass_report(self, targets, reason_obj, message, subtype_label=None):
        if not targets:
            return
        try:
            channel_username = targets[0]["channel"]
            entity = await self.client.resolve_target(channel_username)
            chat_id = entity.id
            message_ids = [t["message_id"] for t in targets]
            full_message = message
            if subtype_label:
                if full_message:
                    full_message = f"[{subtype_label}] {full_message}"
                else:
                    full_message = subtype_label
            await self.client.report_message(
                chat_id=chat_id,
                message_ids=message_ids,
                reason=reason_obj,
                message=full_message
            )
            count = len(message_ids)
            self.stats["success"] += count
            logger.info(f"✅ تم إرسال بلاغ جماعي ناجح على {count} منشور.")
        except Exception as e:
            self.stats["failed"] += len(targets)
            logger.error(f"❌ فشل البلاغ الجماعي: {type(e).__name__} - {e}", exc_info=True)

# --- دوال تشغيل العملية المحسنة ---
async def do_session_report(session_data: dict, config: dict, context: ContextTypes.DEFAULT_TYPE):
    phone = session_data.get("phone")
    proxies = config.get("proxies", [])
    client, connected = None, False
    current_proxy = None
    max_retries = 3
    retry_count = 0
    while retry_count < max_retries and context.user_data.get("active", True):
        current_proxy = proxy_checker.rotate_proxy(proxies, current_proxy)
        try:
            client = TDLibClient(API_ID, API_HASH, phone, proxy=current_proxy)
            await client.start()
            if not await client.is_user_authorized():
                logger.warning("⚠️ الجلسة غير مصرح لها.")
                return
            connected = True
            reporter = AdvancedReporter(client, context)
            method_type = config.get("method_type")
            targets_list = config.get("targets", [])
            reports_per_account = config.get("reports_per_account", 1)
            cycle_delay = config.get("cycle_delay", 1)
            if method_type == "mass":
                await reporter.execute_mass_report(targets_list, config["reason_obj"], config.get("message", ""), config.get("subtype_label"))
            else:
                for _ in range(reports_per_account):
                    if not context.user_data.get("active", True): 
                        break
                    for target in targets_list:
                        if not context.user_data.get("active", True):
                            break
                        await reporter.execute_report(
                            target, config["reason_obj"], method_type,
                            config.get("message", ""), 1, cycle_delay,
                            config.get("subtype_label")
                        )
            lock = context.bot_data.setdefault('progress_lock', asyncio.Lock())
            async with lock:
                context.user_data["progress_success"] = context.user_data.get("progress_success", 0) + reporter.stats["success"]
                context.user_data["progress_failed"] = context.user_data.get("progress_failed", 0) + reporter.stats["failed"]
            break
        except Exception as e:
            retry_count += 1
            logger.error(f"❌ خطأ فادح في جلسة: {e}", exc_info=True)
            if retry_count < max_retries:
                logger.info(f"⏳ إعادة المحاولة {retry_count}/{max_retries}...")
                await asyncio.sleep(2)
            else:
                logger.error(f"❌ فشل الاتصال بعد {max_retries} محاولات.")
        finally:
            if client:
                await client.stop()

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
                        "proxies": config.get("proxies", []),
                        "subtype_label": config.get("subtype_label")
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
    """تلغي العملية الحالية وتنهي المحادثة - محسنة للنظام الجديد."""
    query = update.callback_query if update.callback_query else None
    user_data = context.user_data
    
    # إعلام المستخدم بالإلغاء فوراً
    cancel_msg = None
    try:
        if query and query.message:
            try:
                cancel_msg = await query.message.edit_text("🛑 جاري إيقاف العملية...")
                await query.answer("🛑 جاري الإلغاء...")
            except BadRequest:
                # في حالة عدم إمكانية تعديل الرسالة
                cancel_msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="🛑 جاري إيقاف العملية..."
                )
        else:
            # إذا كان الأمر من رسالة نصية /cancel
            cancel_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🛑 جاري إيقاف العملية..."
            )
    except Exception as e:
        logger.error(f"خطأ في إرسال رسالة بداية الإلغاء: {e}")
    
    # وضع علامة الإلغاء أولاً لإيقاف العمليات الجارية
    user_data["active"] = False
    
    # إلغاء المهام الجارية مع تتبع مفصل
    cancelled_tasks = 0
    total_tasks = 0
    
    # إلغاء مهام النظام الجديد (المتزامن)
    tasks = user_data.get("tasks", [])
    if tasks:
        total_tasks = len(tasks)
        logger.info(f"🛑 محاولة إلغاء {total_tasks} مهمة...")
        
        for i, task in enumerate(tasks):
            if not task.done():
                try:
                    task.cancel()
                    cancelled_tasks += 1
                    logger.debug(f"✅ تم إلغاء المهمة {i+1}/{total_tasks}")
                except Exception as e:
                    logger.error(f"❌ خطأ في إلغاء المهمة {i+1}: {e}")
        
        # انتظار قصير للسماح للمهام بالإلغاء
        if cancelled_tasks > 0:
            try:
                await asyncio.sleep(0.5)  # انتظار أطول قليلاً للنظام الجديد
                logger.info(f"✅ تم إلغاء {cancelled_tasks}/{total_tasks} مهمة")
            except Exception as e:
                logger.error(f"خطأ أثناء انتظار إلغاء المهام: {e}")
    
    # إلغاء مهمة مراقبة البروكسي إن وجدت
    monitor_task = user_data.get("monitor_task")
    if monitor_task and not monitor_task.done():
        try:
            monitor_task.cancel()
            await asyncio.sleep(0.1)
            logger.info("✅ تم إلغاء مهمة مراقبة البروكسي")
        except Exception as e:
            logger.error(f"خطأ أثناء إلغاء مراقبة البروكسي: {e}")
    
    # إلغاء مهام النظام المحسن إن وجدت
    progress_message = user_data.get("progress_message")
    if progress_message:
        try:
            await progress_message.edit_text(
                "🛑 <b>تم إيقاف العملية</b>\n\n"
                "جاري إنهاء المهام المعلقة...",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"خطأ في تحديث رسالة التقدم: {e}")
    
    # تنظيف بيانات المستخدم
    keys_to_remove = [
        "tasks", "active", "lock", "failed_reports",
        "progress_message", "monitor_task", "accounts",
        "targets", "reason_obj", "method_type", "channel",
        "channel_title", "fetch_type", "fetch_limit", "days",
        "message", "reports_per_account", "cycle_delay",
        "proxies", "total_reports", "total_cycles", "current_cycle",
        "progress_success", "progress_confirmed", "progress_failed",
        "start_time", "detailed_stats"
    ]
    
    removed_keys = 0
    for key in keys_to_remove:
        if key in user_data:
            del user_data[key]
            removed_keys += 1
    
    logger.info(f"🗑️ تم تنظيف {removed_keys} عنصر من بيانات المستخدم")
    
    # إرسال رسالة الإلغاء النهائية مع إحصائيات
    final_message = (
        "🛑 <b>تم إلغاء العملية بنجاح</b>\n\n"
        f"📊 <b>إحصائيات الإلغاء:</b>\n"
    )
    
    if total_tasks > 0:
        final_message += f"• المهام الملغاة: {cancelled_tasks}/{total_tasks}\n"
    
    final_message += (
        f"• البيانات المنظفة: {removed_keys} عنصر\n\n"
        "💡 يمكنك البدء من جديد باستخدام /start"
    )
    
    try:
        if cancel_msg:
            await cancel_msg.edit_text(final_message, parse_mode="HTML")
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=final_message,
                parse_mode="HTML"
            )
    except Exception as e:
        # محاولة أخيرة بدون HTML formatting
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🛑 تم إلغاء العملية بنجاح.\n\nيمكنك البدء من جديد باستخدام /start"
            )
        except Exception as e2:
            logger.error(f"خطأ في إرسال رسالة الإلغاء النهائية: {e}, {e2}")
    
    # تسجيل الإلغاء في السجل
    logger.info(f"🛑 تم إلغاء العملية للمستخدم {update.effective_user.id} - مهام ملغاة: {cancelled_tasks}, بيانات منظفة: {removed_keys}")
    
    return ConversationHandler.END