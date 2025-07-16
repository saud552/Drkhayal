import asyncio
import os
import logging
from pytdlib import AsyncTDLibClient
from pytdlib.api import functions as td_functions
from pytdlib.api import types as td_types

logger = logging.getLogger(__name__)

class TDLibClient:
    def __init__(self, api_id, api_hash, phone, session_dir='tdlib_sessions', proxy=None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_dir = session_dir
        self.proxy = proxy
        self.client = None
        self.session_path = os.path.join(session_dir, f'{self.phone}')
        os.makedirs(self.session_dir, exist_ok=True)

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
        return await self.client.send_code_request(self.phone)

    async def sign_in(self, code, password=None):
        return await self.client.sign_in(self.phone, code, password=password)

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

    def _get_report_reason(self, reason_str):
        """تحويل string إلى TDLib report reason object"""
        reason_map = {
            "spam": td_types.ChatReportReasonSpam(),
            "child_abuse": td_types.ChatReportReasonChildAbuse(),
            "pornography": td_types.ChatReportReasonPornography(),
            "violence": td_types.ChatReportReasonViolence(),
            "privacy": td_types.ChatReportReasonPersonalDetails(),
            "drugs": td_types.ChatReportReasonIllegalDrugs(),
            "fake": td_types.ChatReportReasonFake(),
            "copyright": td_types.ChatReportReasonCopyright(),
            "other": td_types.ChatReportReasonCustom(),
        }
        return reason_map.get(reason_str, td_types.ChatReportReasonCustom())

    async def report_peer(self, chat_id, reason, message=""):
        # إرسال بلاغ على مستخدم/قناة/مجموعة
        try:
            # تحويل reason إلى object إذا كان string
            if isinstance(reason, str):
                reason = self._get_report_reason(reason)
                
            return await self.client.invoke(
                td_functions.reportChat(
                    chat_id=chat_id,
                    reason=reason,
                    text=message
                )
            )
        except Exception as e:
            logger.error(f"خطأ في report_peer: {e}")
            return None

    async def report_message(self, chat_id, message_ids, reason, message=""):
        # إرسال بلاغ على رسالة أو عدة رسائل
        try:
            # تحويل reason إلى object إذا كان string
            if isinstance(reason, str):
                reason = self._get_report_reason(reason)
                
            return await self.client.invoke(
                td_functions.reportChatMessage(
                    chat_id=chat_id,
                    message_ids=message_ids,
                    reason=reason,
                    text=message
                )
            )
        except Exception as e:
            logger.error(f"خطأ في report_message: {e}")
            return None

    # يمكن إضافة المزيد من الدوال حسب الحاجة (إرسال تقارير، إلخ)