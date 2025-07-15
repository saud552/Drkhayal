import asyncio
import os
import logging
from pytdlib import AsyncTDLibClient
from pytdlib.api import functions as td_functions
from pytdlib.api import types as td_types

logger = logging.getLogger(__name__)

# استثناءات مخصصة لـ TDLib للتعامل مع أخطاء API
class TDLibError(Exception):
    """استثناء أساسي لـ TDLib"""
    pass

class SessionPasswordNeededError(TDLibError):
    """يُرفع عندما يكون الحساب محمي بكلمة مرور ثنائية"""
    pass

class PhoneCodeInvalidError(TDLibError):
    """يُرفع عندما يكون رمز التحقق غير صحيح"""
    pass

class PhoneCodeExpiredError(TDLibError):
    """يُرفع عندما ينتهي رمز التحقق"""
    pass

class AuthKeyDuplicatedError(TDLibError):
    """يُرفع عندما يكون مفتاح المصادقة مكرر"""
    pass

class FloodWaitError(TDLibError):
    """يُرفع عندما نحتاج للانتظار بسبب حد المعدل"""
    def __init__(self, seconds):
        self.seconds = seconds
        super().__init__(f"Flood wait for {seconds} seconds")

class PeerFloodError(TDLibError):
    """يُرفع عندما نصل لحد إرسال الرسائل"""
    pass

class ChannelPrivateError(TDLibError):
    """يُرفع عندما تكون القناة خاصة"""
    pass

class UsernameNotOccupiedError(TDLibError):
    """يُرفع عندما لا يكون اسم المستخدم مستخدماً"""
    pass

class PeerIdInvalidError(TDLibError):
    """يُرفع عندما يكون معرف المستخدم غير صالح"""
    pass

class RPCError(TDLibError):
    """استثناء عام لأخطاء RPC"""
    pass

class TDLibClient:
    def __init__(self, api_id, api_hash, phone, session_dir='tdlib_sessions', proxy=None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_dir = session_dir
        self.proxy = self._prepare_proxy(proxy) if proxy else None
        self.client = None
        self.session_path = os.path.join(session_dir, f'{self.phone}')
        os.makedirs(self.session_dir, exist_ok=True)

    def _prepare_proxy(self, proxy_config):
        """تحضير إعدادات البروكسي للاستخدام مع TDLib"""
        if not proxy_config:
            return None
            
        if isinstance(proxy_config, dict):
            if proxy_config.get('type') == 'mtproto':
                return {
                    'type': 'mtproto',
                    'server': proxy_config['server'],
                    'port': int(proxy_config['port']),
                    'secret': proxy_config['secret']
                }
        
        # إذا كان البروكسي في تنسيق tuple قديم
        elif isinstance(proxy_config, (tuple, list)) and len(proxy_config) >= 3:
            return {
                'type': 'mtproto',
                'server': proxy_config[0],
                'port': int(proxy_config[1]),
                'secret': proxy_config[2]
            }
            
        return proxy_config

    async def start(self):
        self.client = AsyncTDLibClient(
            api_id=self.api_id,
            api_hash=self.api_hash,
            phone=self.phone,
            database_directory=self.session_path,
            files_directory=self.session_path,
            use_message_database=True,
            use_secret_chats=False,
            proxy=self.proxy
        )
        await self.client.start()
        logger.info(f"TDLib client started for {self.phone}")
        return self.client

    async def stop(self):
        if self.client:
            await self.client.stop()
            logger.info(f"TDLib client stopped for {self.phone}")

    async def send_code(self):
        try:
            return await self.client.send_code_request(self.phone)
        except Exception as e:
            # تحويل أخطاء TDLib إلى استثناءات مخصصة
            error_msg = str(e).lower()
            if 'phone_code_expired' in error_msg:
                raise PhoneCodeExpiredError()
            elif 'phone_code_invalid' in error_msg:
                raise PhoneCodeInvalidError()
            else:
                raise TDLibError(str(e))

    async def sign_in(self, code, password=None):
        try:
            return await self.client.sign_in(self.phone, code, password=password)
        except Exception as e:
            # تحويل أخطاء TDLib إلى استثناءات مخصصة
            error_msg = str(e).lower()
            if 'password_required' in error_msg or '2fa' in error_msg:
                raise SessionPasswordNeededError()
            elif 'phone_code_invalid' in error_msg:
                raise PhoneCodeInvalidError()
            elif 'phone_code_expired' in error_msg:
                raise PhoneCodeExpiredError()
            elif 'flood_wait' in error_msg:
                # استخراج عدد الثواني من رسالة الخطأ
                import re
                match = re.search(r'(\d+)', error_msg)
                seconds = int(match.group(1)) if match else 60
                raise FloodWaitError(seconds)
            else:
                raise TDLibError(str(e))

    async def get_me(self):
        return await self.client.get_me()

    async def is_user_authorized(self):
        me = await self.get_me()
        return me is not None

    async def get_chats(self, limit=100):
        return await self.client.get_chats(limit=limit)

    async def send_message(self, chat_id, text):
        return await self.client.send_message(chat_id, text)

    async def get_chat(self, chat_id):
        return await self.client.get_chat(chat_id)

    async def get_messages(self, chat_id, limit=10):
        return await self.client.get_messages(chat_id, limit=limit)

    async def resolve_target(self, target):
        # يدعم username أو id أو رابط قناة/مجموعة
        try:
            if isinstance(target, str) and target.startswith('https://t.me/'):
                username = target.split('/')[-1]
                return await self.get_chat(username)
            return await self.get_chat(target)
        except Exception as e:
            logger.error(f"خطأ في حل الهدف: {target} - {e}")
            return None

    async def report_peer(self, chat_id, reason, message=""):
        # إرسال بلاغ على مستخدم/قناة/مجموعة
        try:
            return await self.client.invoke(
                td_functions.reportChat(
                    chat_id=chat_id,
                    reason=reason,
                    text=message
                )
            )
        except Exception as e:
            error_msg = str(e).lower()
            if 'flood_wait' in error_msg:
                import re
                match = re.search(r'(\d+)', error_msg)
                seconds = int(match.group(1)) if match else 60
                raise FloodWaitError(seconds)
            elif 'peer_flood' in error_msg:
                raise PeerFloodError()
            else:
                logger.error(f"خطأ في report_peer: {e}")
                raise RPCError(str(e))

    async def report_message(self, chat_id, message_ids, reason, message=""):
        # إرسال بلاغ على رسالة أو عدة رسائل
        try:
            return await self.client.invoke(
                td_functions.reportChatMessage(
                    chat_id=chat_id,
                    message_ids=message_ids,
                    reason=reason,
                    text=message
                )
            )
        except Exception as e:
            error_msg = str(e).lower()
            if 'flood_wait' in error_msg:
                import re
                match = re.search(r'(\d+)', error_msg)
                seconds = int(match.group(1)) if match else 60
                raise FloodWaitError(seconds)
            elif 'peer_flood' in error_msg:
                raise PeerFloodError()
            else:
                logger.error(f"خطأ في report_message: {e}")
                raise RPCError(str(e))

    # يمكن إضافة المزيد من الدوال حسب الحاجة (إرسال تقارير، إلخ)