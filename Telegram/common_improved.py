# DrKhayal/Telegram/common_improved.py - نسخة محسنة ومطورة

import asyncio
import sqlite3
import base64
import logging
import time
import random
import re
import json
import hashlib
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from Telegram.tdlib_client import TDLibClient
from encryption import decrypt_session
from config import API_ID, API_HASH
from add import safe_db_query

logger = logging.getLogger(__name__)
# استيراد DB_PATH من config.py
try:
    from config import DB_PATH
except ImportError:
    DB_PATH = 'accounts.db'  # قيمة افتراضية

# إعداد نظام تسجيل مفصل للتتبع
detailed_logger = logging.getLogger('detailed_reporter')
detailed_handler = logging.FileHandler('detailed_reports.log', encoding='utf-8')
detailed_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
detailed_handler.setFormatter(detailed_formatter)
detailed_logger.addHandler(detailed_handler)
detailed_logger.setLevel(logging.INFO)

# === الثوابت المحسنة ===
PROXY_CHECK_TIMEOUT = 15  # ثانية
PROXY_RECHECK_INTERVAL = 300  # 5 دقائق
MAX_PROXY_RETRIES = 3
REPORT_CONFIRMATION_TIMEOUT = 10  # ثانية للتأكيد
MAX_REPORTS_PER_SESSION = 50  # الحد الأقصى للبلاغات لكل جلسة

# استثناءات مخصصة محسنة
class ProxyTestFailed(Exception):
    """فشل في اختبار البروكسي"""
    pass

class ReportNotConfirmed(Exception):
    """لم يتم تأكيد وصول البلاغ"""
    pass

class SessionCompromised(Exception):
    """الجلسة معرضة للخطر"""
    pass

class RateLimitExceeded(Exception):
    """تم تجاوز حد المعدل"""
    pass

# === أنواع البلاغات مع معرفات التأكيد ===
# ملاحظة: تم إزالة types لأن TDLib يستخدم نظام مختلف للبلاغات
REPORT_TYPES_ENHANCED = {
    2: ("رسائل مزعجة", "spam", "spam"),
    3: ("إساءة أطفال", "child_abuse", "child_abuse"),
    4: ("محتوى جنسي", "pornography", "pornography"),
    5: ("عنف", "violence", "violence"),
    6: ("انتهاك خصوصية", "privacy", "privacy"),
    7: ("مخدرات", "drugs", "drugs"),
    8: ("حساب مزيف", "fake", "fake"),
    9: ("حقوق النشر", "copyright", "copyright"),
    11: ("أخرى", "other", "other"),
}

class EnhancedProxyChecker:
    """نظام فحص بروكسي محسن مع تتبع مفصل وتحقق حقيقي"""
    
    def __init__(self):
        self.proxy_stats = {}
        self.failed_proxies = set()
        self.last_check_times = {}
        self.concurrent_checks = 3  # عدد الفحوصات المتزامنة
    
    def validate_proxy_data(self, proxy_info: dict) -> bool:
        """التحقق من صحة بيانات البروكسي قبل الاستخدام"""
        try:
            if not proxy_info or not isinstance(proxy_info, dict):
                return False
                
            # التحقق من وجود الحقول المطلوبة
            required_fields = ["server", "port", "secret"]
            for field in required_fields:
                if field not in proxy_info or not proxy_info[field]:
                    detailed_logger.error(f"❌ حقل مفقود في البروكسي: {field}")
                    return False
            
            # التحقق من صحة المنفذ
            port = proxy_info["port"]
            if not isinstance(port, int) or port < 1 or port > 65535:
                detailed_logger.error(f"❌ منفذ غير صالح: {port}")
                return False
            
            # التحقق من صحة السر
            secret = proxy_info["secret"]
            if isinstance(secret, str):
                # طباعة معلومات تشخيصية
                detailed_logger.info(f"🔍 فحص السر: نوع={type(secret)}, طول={len(secret)}, محتوى={secret[:20]}...")
                
                # يجب أن يكون السر سداسي عشري صالح
                if len(secret) % 2 != 0:
                    detailed_logger.error(f"❌ طول السر غير صالح: {len(secret)}")
                    return False
                try:
                    test_bytes = bytes.fromhex(secret)
                    detailed_logger.info(f"✅ السر صالح، تم تحويله إلى {len(test_bytes)} بايت")
                except ValueError as e:
                    detailed_logger.error(f"❌ سر غير صالح (ليس سداسي عشري): {secret[:20]}... - خطأ: {e}")
                    return False
            elif isinstance(secret, bytes):
                # إذا كان bytes، فهو صالح
                pass
            else:
                detailed_logger.error(f"❌ نوع سر غير صالح: {type(secret)}")
                return False
                
            return True
            
        except Exception as e:
            detailed_logger.error(f"❌ خطأ في التحقق من البروكسي: {e}")
            return False
        
    async def deep_proxy_test(self, session_str: str, proxy_info: dict) -> dict:
        """اختبار عميق للبروكسي مع فحوصات متعددة"""
        result = proxy_info.copy()
        client = None
        
        detailed_logger.info(f"🔍 بدء deep_proxy_test للبروكسي {proxy_info.get('server', 'مجهول')}")
        
        # التحقق من صحة البيانات أولاً
        if not self.validate_proxy_data(proxy_info):
            result.update({
                "status": "invalid",
                "error": "بيانات البروكسي غير صالحة",
                "quality_score": 0
            })
            return result
        
        try:
            # إعداد العميل مع timeout صارم
            params = {
                "api_id": API_ID,
                "api_hash": API_HASH,
                "timeout": PROXY_CHECK_TIMEOUT,
                "device_model": "Proxy Test Bot",
                "system_version": "1.0.0",
                "app_version": "1.0.0",
                "lang_code": "ar"
            }
            
            # تحضير السر مع معالجة محسنة
            secret = proxy_info["secret"]
            detailed_logger.info(f"🔍 تحضير السر: نوع={type(secret)}, طول={len(secret) if secret else 0}")
            
            if isinstance(secret, str):
                try:
                    detailed_logger.info(f"🔍 محاولة fromhex على السر: {secret[:20]}...")
                    secret_bytes = bytes.fromhex(secret)
                    detailed_logger.info(f"✅ نجح fromhex، تم إنتاج {len(secret_bytes)} بايت")
                except ValueError as e:
                    # محاولة تشفير السر كـ UTF-8 إذا فشل fromhex
                    detailed_logger.warning(f"⚠️ فشل fromhex ({e}), استخدام UTF-8 encoding للسر: {secret[:20]}...")
                    secret_bytes = secret.encode('utf-8')
            elif isinstance(secret, bytes):
                detailed_logger.info(f"🔍 السر بالفعل bytes: {len(secret)} بايت")
                secret_bytes = secret
            else:
                # إذا لم يكن string أو bytes، محاولة تحويله
                try:
                    detailed_logger.warning(f"⚠️ نوع سر غير متوقع: {type(secret)}, محاولة تحويل...")
                    secret_bytes = bytes(secret)
                except (TypeError, ValueError):
                    raise ProxyTestFailed(f"نوع سر غير مدعوم: {type(secret)}")
                
            # TDLib يتوقع إعدادات البروكسي كـ dict
            proxy_config = {
                "@type": "proxyTypeMtproto",
                "server": proxy_info["server"],
                "port": proxy_info["port"],
                "secret": proxy_info["secret"]
            }
            
            # اختبار الاتصال الأولي
            start_time = time.time()
            client = TDLibClient(API_ID, API_HASH, session_str, proxy=proxy_config)
            
            # اختبار الاتصال مع timeout
            await asyncio.wait_for(client.start(), timeout=PROXY_CHECK_TIMEOUT)
            connection_time = time.time() - start_time
            
            # التحقق من التفويض
            if not await client.is_user_authorized():
                raise ProxyTestFailed("الجلسة غير مفوضة")
            
            # اختبار سرعة الاستجابة
            response_start = time.time()
            me = await asyncio.wait_for(client.get_me(), timeout=PROXY_CHECK_TIMEOUT)
            response_time = time.time() - response_start
            
            # اختبار إضافي: جلب الحوارات
            dialogs_start = time.time()
            async for dialog in client.iter_dialogs(limit=5):
                break
            dialogs_time = time.time() - dialogs_start
            
            # تقييم جودة البروكسي
            ping = int(connection_time * 1000)
            responsiveness = int(response_time * 1000)
            
            quality_score = 100
            if ping > 3000:
                quality_score -= 30
            elif ping > 1500:
                quality_score -= 15
                
            if responsiveness > 2000:
                quality_score -= 20
            elif responsiveness > 1000:
                quality_score -= 10
                
            result.update({
                "status": "active",
                "ping": ping,
                "response_time": responsiveness,
                "dialogs_time": int(dialogs_time * 1000),
                "quality_score": max(0, quality_score),
                "last_check": int(time.time()),
                "user_id": me.id,
                "connection_successful": True,
                "error": None
            })
            
            detailed_logger.info(f"✅ بروكسي نشط: {proxy_info['server']} - ping: {ping}ms - جودة: {quality_score}%")
            
        except asyncio.TimeoutError:
            result.update({
                "status": "timeout",
                "ping": 9999,
                "response_time": 9999,
                "quality_score": 0,
                "last_check": int(time.time()),
                "connection_successful": False,
                "error": "انتهت مهلة الاتصال"
            })
            self.failed_proxies.add(proxy_info["server"])
            
        except ProxyTestFailed as e:
            result.update({
                "status": "failed",
                "ping": 0,
                "response_time": 0,
                "quality_score": 0,
                "last_check": int(time.time()),
                "connection_successful": False,
                "error": str(e)
            })
            self.failed_proxies.add(proxy_info["server"])
            
        except Exception as e:
            import traceback
            detailed_logger.error(f"❌ خطأ عام في deep_proxy_test للبروكسي {proxy_info['server']}: {e}")
            detailed_logger.error(f"📍 تتبع الخطأ الكامل:\n{traceback.format_exc()}")
            result.update({
                "status": "error",
                "ping": 0,
                "response_time": 0,
                "quality_score": 0,
                "last_check": int(time.time()),
                "connection_successful": False,
                "error": str(e)
            })
            logger.error(f"خطأ في فحص البروكسي {proxy_info['server']}: {e}")
            
        finally:
            if client:
                try:
                    await client.stop()
                except:
                    pass
                    
        return result
    
    async def batch_check_proxies(self, session_str: str, proxies: List[dict]) -> List[dict]:
        """فحص مجموعة من البروكسيات بشكل متوازي"""
        semaphore = asyncio.Semaphore(self.concurrent_checks)
        
        async def check_single(proxy):
            async with semaphore:
                return await self.deep_proxy_test(session_str, proxy)
        
        tasks = [check_single(proxy) for proxy in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        valid_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"خطأ في فحص البروكسي {proxies[i]['server']}: {result}")
                proxies[i].update({
                    "status": "error",
                    "error": str(result),
                    "quality_score": 0
                })
                valid_results.append(proxies[i])
            else:
                valid_results.append(result)
                
        return valid_results
    
    def get_best_proxies(self, proxies: List[dict], count: int = 5) -> List[dict]:
        """الحصول على أفضل البروكسيات مرتبة حسب الجودة"""
        active_proxies = [p for p in proxies if p.get('status') == 'active']
        
        # ترتيب حسب نقاط الجودة ثم السرعة
        sorted_proxies = sorted(
            active_proxies,
            key=lambda x: (x.get('quality_score', 0), -x.get('ping', 9999)),
            reverse=True
        )
        
        return sorted_proxies[:count]
    
    def needs_recheck(self, proxy_info: dict) -> bool:
        """تحديد إذا كان البروكسي يحتاج إعادة فحص"""
        last_check = proxy_info.get('last_check', 0)
        return (time.time() - last_check) > PROXY_RECHECK_INTERVAL

class VerifiedReporter:
    """نظام إبلاغ محسن مع تأكيد الإرسال والتحقق من النجاح"""
    
    def __init__(self, client: TDLibClient, context: ContextTypes.DEFAULT_TYPE):
        self.client = client
        self.context = context
        self.stats = {
            "success": 0,
            "failed": 0,
            "confirmed": 0,
            "unconfirmed": 0,
            "last_report": None,
            "report_ids": []
        }
        self.session_reports_count = 0
        self.last_activity = time.time()
        
    async def verify_report_success(self, report_result: Any, target: str, report_type: str) -> bool:
        """التحقق من نجاح البلاغ الفعلي"""
        try:
            # تحليل نتيجة البلاغ
            if hasattr(report_result, 'success') and report_result.success:
                detailed_logger.info(f"✅ تم قبول البلاغ بنجاح - الهدف: {target}")
                return True
                
            # إذا كانت النتيجة True أو None (نجاح ضمني)
            elif report_result is True or report_result is None:
                detailed_logger.info(f"✅ تم إرسال البلاغ (نجاح ضمني) - الهدف: {target}")
                return True
                
            else:
                detailed_logger.warning(f"⚠️ نتيجة غير مؤكدة للبلاغ - الهدف: {target} - النتيجة: {type(report_result)}")
                return False
                
        except Exception as e:
            detailed_logger.error(f"❌ خطأ في التحقق من البلاغ - الهدف: {target} - الخطأ: {e}")
            return False
    
    async def intelligent_delay(self, base_delay: float):
        """تأخير ذكي يتكيف مع نشاط الحساب"""
        if self.stats["last_report"]:
            elapsed = time.time() - self.stats["last_report"]
            
            # تقليل التأخير إذا مر وقت كافي
            if elapsed > 60:  # إذا مر أكثر من دقيقة
                adjusted_delay = base_delay * 0.5
            elif elapsed > 30:  # إذا مر أكثر من 30 ثانية
                adjusted_delay = base_delay * 0.7
            else:
                adjusted_delay = base_delay
                
            # إضافة عشوائية للتنويع
            randomized_delay = adjusted_delay + random.uniform(0, adjusted_delay * 0.3)
            
            if elapsed < randomized_delay:
                wait_time = randomized_delay - elapsed
                detailed_logger.info(f"⏳ تأخير ذكي: {wait_time:.1f} ثانية")
                await asyncio.sleep(wait_time)
                
        self.stats["last_report"] = time.time()
        self.last_activity = time.time()
    
    def validate_username(self, username: str) -> bool:
        """التحقق من صحة اسم المستخدم قبل المحاولة"""
        if not username:
            return False
            
        # إزالة @ إن وُجد
        if username.startswith('@'):
            username = username[1:]
            
        # التحقق من طول الاسم
        if len(username) < 4 or len(username) > 32:
            return False
            
        # التحقق من النمط الصحيح
        import re
        pattern = r"^[a-zA-Z][a-zA-Z0-9_]{2,30}[a-zA-Z0-9]$"
        if not re.match(pattern, username):
            return False
            
        # التحقق من عدم وجود أحرف متتالية غير مسموحة
        if '__' in username or username.endswith('_'):
            return False
            
        return True
    
    async def resolve_target_enhanced(self, target: str | dict) -> dict:
        """حل الهدف مع معلومات إضافية للتتبع"""
        try:
            # استخدم tdlib_client.resolve_target مباشرة
            resolved = await self.client.resolve_target(target)
            if resolved:
                return {"resolved": resolved, "type": "peer"}
            return None
        except Exception as e:
            detailed_logger.error(f"❌ فشل في حل الهدف {target}: {e}")
            return None
    
    def parse_message_link(self, link: str) -> dict | None:
        """تحليل رابط رسالة تليجرام المحسن"""
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
            
            return None
        except Exception as e:
            logger.error(f"خطأ في تحليل رابط الرسالة: {e}")
            return None
    
    async def execute_verified_report(self, target: Any, reason_obj: Any, method_type: str, 
                                    message: str, reports_count: int, cycle_delay: float) -> dict:
        """تنفيذ بلاغ محقق مع تأكيد النجاح"""
        
        # فحص حد البلاغات لكل جلسة
        if self.session_reports_count >= MAX_REPORTS_PER_SESSION:
            raise RateLimitExceeded(f"تم تجاوز الحد الأقصى {MAX_REPORTS_PER_SESSION} بلاغ لكل جلسة")
        
        target_info = await self.resolve_target_enhanced(target)
        if not target_info or not target_info["resolved"]:
            self.stats["failed"] += reports_count
            error_msg = "فشل في حل الهدف - تأكد من صحة اسم المستخدم أو رابط القناة"
            detailed_logger.warning(f"❌ تخطي البلاغ بسبب هدف غير صالح: {target}")
            return {"success": False, "error": error_msg}
        
        report_results = []
        
        for i in range(reports_count):
            if not self.context.user_data.get("active", True):
                break
                
            try:
                await self.intelligent_delay(cycle_delay)
                
                # إنشاء معرف فريد للبلاغ
                report_id = hashlib.md5(
                    f"{target}_{method_type}_{time.time()}_{i}".encode()
                ).hexdigest()[:8]
                
                result = None
                
                if method_type == "peer":
                    result = await self.client.report_peer(
                        chat_id=target_info["resolved"].id,
                        reason=reason_obj,
                        message=message
                    )
                    
                elif method_type == "message":
                    # يجب أن يكون target_info["resolved"] كائن رسالة أو dict
                    chat_id = target_info["resolved"].id if hasattr(target_info["resolved"], 'id') else None
                    msg_id = target["message_id"] if isinstance(target, dict) and "message_id" in target else None
                    if chat_id and msg_id:
                        result = await self.client.report_message(
                            chat_id=chat_id,
                            message_ids=[msg_id],
                            reason=reason_obj,
                            message=message
                        )
                
                # التحقق من نجاح البلاغ
                verified = True if result else False
                
                if verified:
                    self.stats["success"] += 1
                    self.stats["confirmed"] += 1
                    self.session_reports_count += 1
                    
                    report_info = {
                        "id": report_id,
                        "target": str(target),
                        "method": method_type,
                        "timestamp": time.time(),
                        "verified": True
                    }
                    
                    self.stats["report_ids"].append(report_info)
                    report_results.append(report_info)
                    
                    detailed_logger.info(f"✅ بلاغ محقق #{report_id} - الهدف: {target} - الطريقة: {method_type}")
                    
                else:
                    self.stats["unconfirmed"] += 1
                    detailed_logger.warning(f"⚠️ بلاغ غير محقق - الهدف: {target}")
                    
            except Exception as e:
                detailed_logger.error(f"❌ خطأ في البلاغ - الهدف: {target} - الخطأ: {e}")
                self.stats["failed"] += 1
        
        return {
            "success": len(report_results) > 0,
            "verified_reports": len(report_results),
            "total_attempts": reports_count,
            "report_ids": report_results
        }

# === دوال مساعدة محسنة ===

def convert_secret_enhanced(secret: str) -> str | None:
    """تحويل سر البروكسي محسن مع دعم جميع الصيغ"""
    secret = secret.strip()
    
    # إزالة المسافات والأحرف الخاصة
    clean_secret = re.sub(r'[^A-Fa-f0-9]', '', secret)
    
    # فحص الصيغة السداسية
    if re.fullmatch(r'[A-Fa-f0-9]+', clean_secret) and len(clean_secret) % 2 == 0:
        if len(clean_secret) >= 32:  # سر صالح
            return clean_secret.lower()
    
    # محاولة فك base64
    try:
        # إزالة البادئات
        for prefix in ['ee', 'dd', '00']:
            if secret.startswith(prefix):
                secret = secret[len(prefix):]
                break
        
        # تحويل base64 URL-safe
        cleaned = secret.replace('-', '+').replace('_', '/')
        padding = '=' * (-len(cleaned) % 4)
        decoded = base64.b64decode(cleaned + padding)
        
        hex_secret = decoded.hex()
        if len(hex_secret) >= 32:
            return hex_secret
            
    except Exception:
        pass
    
    return None

def parse_proxy_link_enhanced(link: str) -> dict | None:
    """تحليل رابط البروكسي محسن مع دعم صيغ متعددة"""
    try:
        parsed = urlparse(link)
        params = parse_qs(parsed.query)
        
        server = params.get('server', [''])[0]
        port = params.get('port', [''])[0]
        secret = params.get('secret', [''])[0]
        
        if not all([server, port, secret]):
            # محاولة استخراج من المسار
            parts = parsed.path.strip('/').split('/')
            if len(parts) >= 3:
                server, port, secret = parts[0], parts[1], '/'.join(parts[2:])
        
        if not all([server, port, secret]):
            return None
        
        try:
            port = int(port)
        except ValueError:
            return None
        
        hex_secret = convert_secret_enhanced(secret)
        if not hex_secret:
            return None
        
        return {
            'server': server.strip(),
            'port': port,
            'secret': hex_secret,
            'format': 'hex',
            'original_link': link
        }
        
    except Exception as e:
        logger.error(f"خطأ في تحليل رابط البروكسي: {e}")
        return None

# === إنشاء المكونات المحسنة ===
enhanced_proxy_checker = EnhancedProxyChecker()

async def run_enhanced_report_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عملية إبلاغ محسنة مع تتبع مفصل وتأكيد الإرسال - مع الإبلاغ المتزامن"""
    config = context.user_data
    sessions = config.get("accounts", [])
    
    if not sessions:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ لا توجد حسابات صالحة لبدء العملية."
        )
        return
    
    targets = config.get("targets", [])
    reports_per_account = config.get("reports_per_account", 1)
    proxies = config.get("proxies", [])
    cycle_delay = config.get("cycle_delay", 1)
    
    # إحصائيات مفصلة - تحديث حساب المجموع
    total_cycles = reports_per_account  # عدد الدورات = عدد البلاغات المطلوبة
    total_expected = len(sessions) * len(targets) * total_cycles
    config.update({
        "total_reports": total_expected,
        "total_cycles": total_cycles,
        "current_cycle": 0,
        "progress_success": 0,
        "progress_confirmed": 0,
        "progress_failed": 0,
        "active": True,
        "lock": asyncio.Lock(),
        "start_time": time.time(),
        "detailed_stats": {
            "verified_reports": [],
            "failed_sessions": [],
            "proxy_performance": {},
            "cycle_stats": []
        }
    })
    
    # فحص البروكسيات أولاً
    if proxies:
        progress_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔍 جاري فحص البروكسيات بشكل مفصل..."
        )
        
        # استخدام أول جلسة للفحص
        test_session = sessions[0]["session"]
        checked_proxies = await enhanced_proxy_checker.batch_check_proxies(test_session, proxies)
        
        active_proxies = [p for p in checked_proxies if p.get('status') == 'active']
        
        if not active_proxies:
            await progress_msg.edit_text(
                "❌ لا توجد بروكسيات نشطة. سيتم استخدام الاتصال المباشر."
            )
            config["proxies"] = []
        else:
            best_proxies = enhanced_proxy_checker.get_best_proxies(active_proxies, 5)
            config["proxies"] = best_proxies
            
            proxy_summary = "\n".join([
                f"• {p['server']} - جودة: {p['quality_score']}% - ping: {p['ping']}ms"
                for p in best_proxies[:3]
            ])
            
            await progress_msg.edit_text(
                f"✅ تم فحص البروكسيات\n"
                f"نشط: {len(active_proxies)}/{len(proxies)}\n\n"
                f"أفضل البروكسيات:\n{proxy_summary}"
            )
            
            await asyncio.sleep(2)
    
    # بدء عملية الإبلاغ المحسنة المتزامنة
    try:
        progress_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🚀 بدء عملية الإبلاغ الجماعي المتزامن...",
            parse_mode="HTML"
        )
        context.user_data["progress_message"] = progress_message
        
        # تنفيذ دورات الإبلاغ المتزامن
        await execute_simultaneous_mass_reporting(sessions, targets, config, context, progress_message)
        
    except Exception as e:
        logger.error(f"خطأ في عملية الإبلاغ المحسنة: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ خطأ في العملية: {str(e)}"
        )

async def execute_simultaneous_mass_reporting(sessions: list, targets: list, config: dict, 
                                            context: ContextTypes.DEFAULT_TYPE, progress_message: Any):
    """تنفيذ الإبلاغ الجماعي المتزامن - جميع المنشورات من جميع الحسابات في نفس الوقت"""
    total_cycles = config["total_cycles"]
    cycle_delay = config.get("cycle_delay", 1)
    
    detailed_logger.info(f"🚀 بدء {total_cycles} دورة إبلاغ جماعي متزامن")
    
    try:
        for cycle in range(total_cycles):
            # فحص الإلغاء قبل بدء كل دورة
            if not config.get("active", True):
                detailed_logger.info(f"🛑 تم إلغاء العملية قبل الدورة {cycle + 1}")
                break
                
            config["current_cycle"] = cycle + 1
            cycle_start_time = time.time()
            
            detailed_logger.info(f"📊 بدء الدورة {cycle + 1}/{total_cycles}")
            
            # تحديث رسالة التقدم لعرض معلومات الدورة
            await update_cycle_progress(config, progress_message, cycle + 1, "بدء الدورة...")
            
            # إنشاء جميع مهام الإبلاغ للدورة الحالية (جميع الحسابات × جميع المنشورات)
            cycle_tasks = []
            
            for session in sessions:
                for target in targets:
                    # فحص الإلغاء أثناء إنشاء المهام
                    if not config.get("active", True):
                        detailed_logger.info(f"🛑 تم إلغاء العملية أثناء إنشاء مهام الدورة {cycle + 1}")
                        break
                        
                    # إنشاء مهمة إبلاغ واحدة لكل (حساب، منشور)
                    task = asyncio.create_task(
                        execute_single_report_task(session, target, config, context)
                    )
                    cycle_tasks.append(task)
                
                # فحص الإلغاء بين الجلسات
                if not config.get("active", True):
                    break
            
            # إذا تم الإلغاء، ألغي جميع المهام المعلقة
            if not config.get("active", True):
                detailed_logger.info(f"🛑 إلغاء {len(cycle_tasks)} مهمة معلقة...")
                for task in cycle_tasks:
                    if not task.done():
                        task.cancel()
                        
                # انتظار قصير للسماح للمهام بالإلغاء
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*cycle_tasks, return_exceptions=True), 
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    detailed_logger.warning("⚠️ بعض المهام لم تُلغى في الوقت المحدد")
                break
            
            if not cycle_tasks:
                detailed_logger.warning(f"⚠️ لا توجد مهام لتنفيذها في الدورة {cycle + 1}")
                break
                
            detailed_logger.info(f"⚡ تنفيذ {len(cycle_tasks)} مهمة إبلاغ متزامنة في الدورة {cycle + 1}")
            
            # تحديث رسالة التقدم
            await update_cycle_progress(config, progress_message, cycle + 1, f"تنفيذ {len(cycle_tasks)} إبلاغ متزامن...")
            
            # تنفيذ جميع المهام بشكل متزامن مع إمكانية الإلغاء
            try:
                cycle_results = await asyncio.gather(*cycle_tasks, return_exceptions=True)
            except asyncio.CancelledError:
                detailed_logger.info(f"🛑 تم إلغاء مهام الدورة {cycle + 1}")
                break
            
            # فحص الإلغاء بعد اكتمال المهام
            if not config.get("active", True):
                detailed_logger.info(f"🛑 تم إلغاء العملية بعد اكتمال الدورة {cycle + 1}")
                break
            
            # تحليل نتائج الدورة
            cycle_success = 0
            cycle_failed = 0
            
            for result in cycle_results:
                if isinstance(result, Exception):
                    cycle_failed += 1
                    if not isinstance(result, asyncio.CancelledError):
                        detailed_logger.error(f"❌ مهمة فاشلة في الدورة {cycle + 1}: {result}")
                elif isinstance(result, dict) and result.get("success"):
                    cycle_success += result.get("verified_reports", 0)
                else:
                    cycle_failed += 1
            
            # تحديث الإحصائيات
            async with config["lock"]:
                config["progress_success"] += cycle_success
                config["progress_confirmed"] += cycle_success
                config["progress_failed"] += cycle_failed
                
                # إضافة إحصائيات الدورة
                cycle_stats = {
                    "cycle": cycle + 1,
                    "success": cycle_success,
                    "failed": cycle_failed,
                    "duration": time.time() - cycle_start_time,
                    "timestamp": time.time(),
                    "cancelled": not config.get("active", True)
                }
                config["detailed_stats"]["cycle_stats"].append(cycle_stats)
            
            cycle_duration = time.time() - cycle_start_time
            detailed_logger.info(f"✅ اكتملت الدورة {cycle + 1}/{total_cycles} - نجح: {cycle_success}, فشل: {cycle_failed}, المدة: {cycle_duration:.1f}ث")
            
            # تحديث رسالة التقدم بنتائج الدورة
            await update_cycle_progress(config, progress_message, cycle + 1, 
                                      f"نجح: {cycle_success}, فشل: {cycle_failed}")
            
            # انتظار قبل الدورة التالية (إلا إذا كانت آخر دورة أو تم الإلغاء)
            if cycle < total_cycles - 1 and config.get("active", True):
                detailed_logger.info(f"⏳ انتظار {cycle_delay} ثانية قبل الدورة التالية...")
                await update_cycle_progress(config, progress_message, cycle + 1, 
                                          f"انتظار {cycle_delay}ث قبل الدورة التالية...")
                
                # انتظار مع فحص الإلغاء كل ثانية
                for wait_second in range(cycle_delay):
                    if not config.get("active", True):
                        detailed_logger.info(f"🛑 تم إلغاء العملية أثناء الانتظار (ثانية {wait_second + 1}/{cycle_delay})")
                        break
                    await asyncio.sleep(1)
                
                # إذا تم الإلغاء أثناء الانتظار
                if not config.get("active", True):
                    break
    
    except asyncio.CancelledError:
        detailed_logger.info("🛑 تم إلغاء عملية الإبلاغ الجماعي")
        config["active"] = False
    except Exception as e:
        detailed_logger.error(f"❌ خطأ في عملية الإبلاغ الجماعي: {e}")
        config["active"] = False
    finally:
        # عرض النتائج النهائية (سواء اكتملت أو تم إلغاؤها)
        await display_final_mass_report_results(config, progress_message)

async def execute_single_report_task(session: dict, target: any, config: dict, 
                                   context: ContextTypes.DEFAULT_TYPE) -> dict:
    """تنفيذ مهمة إبلاغ واحدة (حساب واحد، منشور واحد) - مع دعم الإلغاء"""
    session_id = session.get("id", "unknown")
    session_str = session.get("session")
    proxies = config.get("proxies", [])
    
    # فحص الإلغاء في بداية المهمة
    if not config.get("active", True):
        return {"success": False, "error": "تم إلغاء العملية", "cancelled": True}
    
    if not session_str:
        return {"success": False, "error": f"جلسة فارغة للحساب {session_id}"}
    
    client = None
    current_proxy = None
    
    try:
        # فحص الإلغاء قبل بدء الاتصال
        if not config.get("active", True):
            return {"success": False, "error": "تم إلغاء العملية قبل الاتصال", "cancelled": True}
        
        # اختيار بروكسي عشوائي
        if proxies:
            current_proxy = random.choice(proxies)
        
        # إعداد العميل
        params = {
            "api_id": API_ID,
            "api_hash": API_HASH,
            "timeout": 30,
            "device_model": f"MassReporter-{session_id}",
            "system_version": "3.0.0",
            "app_version": "3.0.0"
        }
        
        if current_proxy:
            params.update({
                "connection": ConnectionTcpMTProxyRandomizedIntermediate,
                "proxy": (current_proxy["server"], current_proxy["port"], current_proxy["secret"])
            })
        
        # الاتصال مع فحص الإلغاء
        client = TDLibClient(session_str)
        
        # اتصال مع timeout قصير لسرعة الاستجابة للإلغاء
        connect_task = asyncio.create_task(client.connect())
        try:
            await asyncio.wait_for(connect_task, timeout=15)
        except asyncio.TimeoutError:
            return {"success": False, "error": f"انتهت مهلة الاتصال للحساب {session_id}"}
        
        # فحص الإلغاء بعد الاتصال
        if not config.get("active", True):
            return {"success": False, "error": "تم إلغاء العملية بعد الاتصال", "cancelled": True}
        
        if not await client.is_user_authorized():
            return {"success": False, "error": f"الجلسة {session_id} غير مفوضة"}
        
        # إنشاء مبلغ محقق
        reporter = VerifiedReporter(client, context)
        
        # فحص الإلغاء قبل تنفيذ البلاغ
        if not config.get("active", True):
            return {"success": False, "error": "تم إلغاء العملية قبل الإبلاغ", "cancelled": True}
        
        # تنفيذ بلاغ واحد فقط لهذا الهدف
        result = await reporter.execute_verified_report(
            target=target,
            reason_obj=config["reason_obj"],
            method_type=config["method_type"],
            message=config.get("message", ""),
            reports_count=1,  # بلاغ واحد فقط في كل مهمة
            cycle_delay=0     # لا حاجة للتأخير داخل المهمة الواحدة
        )
        
        # فحص الإلغاء بعد البلاغ
        if not config.get("active", True):
            result["cancelled"] = True
        
        return result
        
    except asyncio.CancelledError:
        detailed_logger.info(f"🛑 تم إلغاء مهمة الحساب {session_id}")
        return {"success": False, "error": "تم إلغاء المهمة", "cancelled": True}
    except Exception as e:
        detailed_logger.error(f"❌ فشل مهمة الحساب {session_id} للهدف {target}: {e}")
        return {"success": False, "error": str(e)}
    
    finally:
        if client and client.is_connected():
            try:
                await client.disconnect()
            except Exception as e:
                detailed_logger.warning(f"⚠️ خطأ في قطع الاتصال للحساب {session_id}: {e}")

async def update_cycle_progress(config: dict, progress_message: Any, current_cycle: int, status: str):
    """تحديث رسالة التقدم مع معلومات الدورة"""
    try:
        async with config["lock"]:
            success = config["progress_success"]
            failed = config["progress_failed"]
            total = config["total_reports"]
            total_cycles = config["total_cycles"]
            
        completed = success + failed
        progress_percent = min(100, int((completed / total) * 100))
        
        elapsed = time.time() - config["start_time"]
        
        # شريط التقدم
        filled = int(20 * (progress_percent / 100))
        progress_bar = "█" * filled + "░" * (20 - filled)
        
        # حساب التوقيت المتبقي بناءً على الدورات
        if current_cycle > 1:
            avg_cycle_time = elapsed / (current_cycle - 1)
            remaining_cycles = total_cycles - current_cycle + 1
            eta_seconds = avg_cycle_time * remaining_cycles
            eta_str = str(timedelta(seconds=int(eta_seconds)))
        else:
            eta_str = "حساب..."
        
        text = (
            f"🎯 <b>الإبلاغ الجماعي المتزامن</b>\n\n"
            f"<code>[{progress_bar}]</code> {progress_percent}%\n\n"
            f"📊 <b>الدورة {current_cycle}/{total_cycles}</b>\n"
            f"📈 <b>الإحصائيات:</b>\n"
            f"▫️ المطلوب: {total}\n"
            f"✅ نجح: {success}\n"
            f"❌ فشل: {failed}\n"
            f"⏱ المتبقي: {eta_str}\n"
            f"⏰ المدة: {str(timedelta(seconds=int(elapsed)))}\n\n"
            f"🔄 <b>الحالة:</b> {status}"
        )
        
        await progress_message.edit_text(text, parse_mode="HTML")
        
    except BadRequest:
        pass
    except Exception as e:
        logger.warning(f"خطأ في تحديث رسالة التقدم: {e}")

async def display_final_mass_report_results(config: dict, progress_message: Any):
    """عرض النتائج النهائية للإبلاغ الجماعي"""
    async with config["lock"]:
        final_stats = {
            "success": config["progress_success"],
            "confirmed": config["progress_confirmed"],
            "failed": config["progress_failed"],
            "total_cycles": config["total_cycles"],
            "total_time": time.time() - config["start_time"],
            "cycle_stats": config["detailed_stats"]["cycle_stats"]
        }
    
    # حساب معدلات النجاح لكل دورة
    cycle_summary = ""
    if final_stats["cycle_stats"]:
        cycle_summary = "\n\n📊 <b>ملخص الدورات:</b>\n"
        for cycle_stat in final_stats["cycle_stats"]:
            cycle_num = cycle_stat["cycle"]
            cycle_success = cycle_stat["success"]
            cycle_failed = cycle_stat["failed"]
            cycle_duration = cycle_stat["duration"]
            cycle_summary += f"▫️ الدورة {cycle_num}: ✅{cycle_success} ❌{cycle_failed} ({cycle_duration:.1f}ث)\n"
    
    avg_cycle_time = final_stats["total_time"] / final_stats["total_cycles"] if final_stats["total_cycles"] > 0 else 0
    total_success_rate = (final_stats["success"] / (final_stats["success"] + final_stats["failed"]) * 100) if (final_stats["success"] + final_stats["failed"]) > 0 else 0
    
    final_text = (
        f"🎯 <b>اكتمل الإبلاغ الجماعي المتزامن!</b>\n\n"
        f"📊 <b>النتائج النهائية:</b>\n"
        f"• إجمالي الدورات: {final_stats['total_cycles']}\n"
        f"• البلاغات الناجحة: {final_stats['success']}\n"
        f"• البلاغات الفاشلة: {final_stats['failed']}\n"
        f"• معدل النجاح: {total_success_rate:.1f}%\n"
        f"• متوسط وقت الدورة: {avg_cycle_time:.1f} ثانية\n"
        f"• المدة الإجمالية: {str(timedelta(seconds=int(final_stats['total_time'])))}\n"
        f"{cycle_summary}\n"
        f"📋 تم حفظ تقرير مفصل في detailed_reports.log"
    )
    
    try:
        await progress_message.edit_text(final_text, parse_mode="HTML")
    except Exception:
        await context.bot.send_message(
            chat_id=progress_message.chat_id,
            text=final_text,
            parse_mode="HTML"
        )
    
    # حفظ التقرير المفصل
    detailed_logger.info(f"📋 تقرير الإبلاغ الجماعي المتزامن: {json.dumps(final_stats, indent=2, ensure_ascii=False)}")

async def process_enhanced_session(session: dict, targets: list, reports_per_account: int, 
                                 config: dict, context: ContextTypes.DEFAULT_TYPE):
    """معالجة جلسة واحدة مع تحقق مفصل - محدث للاستخدام التقليدي فقط"""
    # هذه الدالة تُستخدم الآن للطرق التقليدية فقط
    # الإبلاغ الجماعي المتزامن يستخدم execute_simultaneous_mass_reporting
    
    session_id = session.get("id", "unknown")
    session_str = session.get("session")
    proxies = config.get("proxies", [])
    
    if not session_str:
        detailed_logger.error(f"❌ جلسة فارغة للحساب {session_id}")
        return
    
    client = None
    current_proxy = None
    
    try:
        # اختيار أفضل بروكسي
        if proxies:
            current_proxy = random.choice(proxies)
            detailed_logger.info(f"🔗 استخدام البروكسي {current_proxy['server']} للحساب {session_id}")
        
        # إعداد العميل
        params = {
            "api_id": API_ID,
            "api_hash": API_HASH,
            "timeout": 30,
            "device_model": f"ReporterBot-{session_id}",
            "system_version": "2.0.0",
            "app_version": "2.0.0"
        }
        
        if current_proxy:
            # تحضير السر مع فحص النوع
            secret = current_proxy["secret"]
            if isinstance(secret, str):
                try:
                    secret_bytes = bytes.fromhex(secret)
                except ValueError:
                    logger.error(f"سر غير صالح: {secret}")
                    secret_bytes = secret.encode() if isinstance(secret, str) else secret
            else:
                secret_bytes = secret
                
            params.update({
                "connection": ConnectionTcpMTProxyRandomizedIntermediate,
                "proxy": (current_proxy["server"], current_proxy["port"], current_proxy["secret"])
            })
        
        # الاتصال
        client = TDLibClient(session_str)
        await client.connect()
        
        if not await client.is_user_authorized():
            raise SessionCompromised(f"الجلسة {session_id} غير مفوضة")
        
        # إنشاء مبلغ محقق
        reporter = VerifiedReporter(client, context)
        
        # تنفيذ البلاغات (للطرق التقليدية فقط)
        for target in targets:
            if not config.get("active", True):
                break
                
            result = await reporter.execute_verified_report(
                target=target,
                reason_obj=config["reason_obj"],
                method_type=config["method_type"],
                message=config.get("message", ""),
                reports_count=reports_per_account,
                cycle_delay=config.get("cycle_delay", 1)
            )
            
            # تحديث الإحصائيات
            async with config["lock"]:
                config["progress_success"] += result.get("verified_reports", 0)
                config["progress_confirmed"] += result.get("verified_reports", 0)
                
                if result.get("verified_reports", 0) > 0:
                    config["detailed_stats"]["verified_reports"].extend(
                        result.get("report_ids", [])
                    )
        
        detailed_logger.info(f"✅ اكتمل الحساب {session_id} - البلاغات المحققة: {reporter.stats['confirmed']}")
        
    except Exception as e:
        detailed_logger.error(f"❌ فشل الحساب {session_id}: {e}")
        async with config["lock"]:
            config["detailed_stats"]["failed_sessions"].append({
                "session_id": session_id,
                "error": str(e),
                "timestamp": time.time()
            })
    
    finally:
        if client and client.is_connected():
            await client.disconnect()
