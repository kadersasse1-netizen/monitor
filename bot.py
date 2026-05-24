import asyncio
import logging
import random
import os
import json
import threading
from datetime import datetime, timedelta
import pytz
import aiohttp
from flask import Flask
from telegram import Bot, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
try:
    from motor.motor_asyncio import AsyncIOMotorClient
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False

# ─── إعداد اللوغ ──────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Telethon ─────────────────────────────────────────────────────────────────
try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.channels import GetParticipantsRequest
    from telethon.tl.types import ChannelParticipantsSearch
    TELETHON_AVAILABLE = True
    logger.info("✅ مكتبة Telethon موجودة")
except ImportError:
    TELETHON_AVAILABLE = False
    logger.warning("⚠️ telethon غير مثبت — ميزة جلب الأعضاء الكاملة معطلة")

# ─── الإعدادات ────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN", "ضع_توكن_البوت_هنا")
ALGERIA_TZ = pytz.timezone("Africa/Algiers")
DELETE_DELAY = 1800  # 30 دقيقة بالثواني


# ── قائمة المدن الجزائرية المعروفة (fallback عند فشل الـ API) ─────────────────

# ── التقويم الهجري (بدون مكتبة خارجية) ──────────────────────────────────────
HIJRI_MONTHS = [
    "", "مُحرَّم", "صَفَر", "ربيع الأوَّل", "ربيع الثاني",
    "جُمادى الأولى", "جُمادى الآخرة", "رَجَب", "شَعبان",
    "رَمَضان", "شوَّال", "ذو القَعدة", "ذو الحِجَّة"
]

def gregorian_to_hijri(year: int, month: int, day: int) -> tuple:
    if month <= 2:
        year -= 1
        month += 12
    A  = int(year / 100)
    B  = 2 - A + int(A / 4)
    JD = int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + B - 1524.5
    JD = int(JD - 0.5)
    Z  = JD - 1948438 + 10632
    N  = int((Z - 1) / 10631)
    Z  = Z - 10631 * N + 354
    J  = (int((10985 - Z) / 5316)) * (int((50 * Z) / 17719)) +          (int(Z / 5670)) * (int((43 * Z) / 15238))
    Z  = Z - (int((30 - J) / 15)) * (int((17719 * J) / 50)) -          (int(J / 16)) * (int((15238 * J) / 43)) + 29
    m  = int((24 * Z) / 709)
    d  = Z - int((709 * m) / 24)
    y  = 30 * N + J - 30
    return y, m, d

def get_hijri_str(dt=None) -> str:
    if dt is None:
        dt = datetime.now(ALGERIA_TZ)
    y, m, d = gregorian_to_hijri(dt.year, dt.month, dt.day)
    return f"{d} {HIJRI_MONTHS[m]} {y} هـ"

def is_ramadan(dt=None) -> bool:
    if dt is None:
        dt = datetime.now(ALGERIA_TZ)
    _, m, _ = gregorian_to_hijri(dt.year, dt.month, dt.day)
    return m == 9

def is_friday(dt=None) -> bool:
    if dt is None:
        dt = datetime.now(ALGERIA_TZ)
    return dt.weekday() == 4

KNOWN_ALGERIAN_CITIES: dict = {
    "algiers": ("Algiers", "Algeria"),
    "algeriers": ("Algiers", "Algeria"),  # إصلاح الخطأ الإملائي
    "الجزائر": ("Algiers", "Algeria"),
    "وهران": ("Oran", "Algeria"),
    "قسنطينة": ("Constantine", "Algeria"),
    "عنابة": ("Annaba", "Algeria"),
    "باتنة": ("Batna", "Algeria"),
    "تيارت": ("Tiaret", "Algeria"),
    "تلمسان": ("Tlemcen", "Algeria"),
    "البليدة": ("Blida", "Algeria"),
    "سطيف": ("Setif", "Algeria"),
    "بسكرة": ("Biskra", "Algeria"),

    "oran": ("Oran", "Algeria"),
    "constantine": ("Constantine", "Algeria"),
    "annaba": ("Annaba", "Algeria"),
    "batna": ("Batna", "Algeria"),
    "tlemcen": ("Tlemcen", "Algeria"),
    "tiaret": ("Tiaret", "Algeria"),
    "blida": ("Blida", "Algeria"),
    "setif": ("Setif", "Algeria"),
    "sétif": ("Setif", "Algeria"),
    "biskra": ("Biskra", "Algeria"),
    "bejaia": ("Bejaia", "Algeria"),
    "béjaïa": ("Bejaia", "Algeria"),
    "chlef": ("Chlef", "Algeria"),
    "jijel": ("Jijel", "Algeria"),
    "skikda": ("Skikda", "Algeria"),
    "guelma": ("Guelma", "Algeria"),
    "souk ahras": ("Souk Ahras", "Algeria"),
    "tizi ouzou": ("Tizi Ouzou", "Algeria"),
    "medea": ("Medea", "Algeria"),
    "mascara": ("Mascara", "Algeria"),
    "mostaganem": ("Mostaganem", "Algeria"),
    "msila": ("M'Sila", "Algeria"),
    "djelfa": ("Djelfa", "Algeria"),
    "laghouat": ("Laghouat", "Algeria"),
    "ouargla": ("Ouargla", "Algeria"),
    "ghardaia": ("Ghardaia", "Algeria"),
    "bechar": ("Bechar", "Algeria"),
    "adrar": ("Adrar", "Algeria"),
    "tamanrasset": ("Tamanrasset", "Algeria"),
    "illizi": ("Illizi", "Algeria"),
}

# ADMIN_IDS: معرّفات المالكين مفصولة بفاصلة  مثال: 123456,789012
# يُقرأ من متغير البيئة ADMIN_IDS في Render
_raw_ids = os.getenv("ADMIN_IDS", "")
_env_ids: set = {
    int(x.strip()) for x in _raw_ids.split(",") if x.strip().lstrip("-").isdigit()
}
# ─── المالك الثابت — يعمل دائماً حتى بدون ADMIN_IDS في Render ───────────────
ADMIN_IDS: set = _env_ids | {7176475438}  # @kadersasse

# ─── إعدادات Telethon ────────────────────────────────────────────────────────
TELETHON_API_ID   = int(os.getenv("API_ID", "0"))
TELETHON_API_HASH = os.getenv("API_HASH", "")
TELETHON_SESSION  = os.getenv("SESSION_STRING", "")
_telethon_client  = None   # عميل Telethon العالمي

# ─── ملفات الحفظ ──────────────────────────────────────────────────────────────
# أنواع المحادثات المدعومة
GROUP_TYPES   = ("group", "supergroup")
CHANNEL_TYPES = ("channel",)
ACTIVE_TYPES  = GROUP_TYPES + CHANNEL_TYPES  # كل ما يقبله البوت

DATA_FILE         = "bot_data.json"
DELETE_QUEUE_FILE = "delete_queue.json"
CONFIG_FILE       = "bot_config.json"

# ─── MongoDB ────────────────────────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "")
_mongo_client = None
_mongo_db     = None

def _get_db():
    """يُعيد مجموعة MongoDB أو None إن لم تكن متاحة"""
    return _mongo_db

async def init_mongo():
    global _mongo_client, _mongo_db
    if not MONGO_AVAILABLE or not MONGO_URI:
        logger.warning("⚠️ MongoDB غير مفعّل — سيُستخدم الملف المحلي")
        return
    try:
        _mongo_client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        await _mongo_client.server_info()  # تحقق من الاتصال
        _mongo_db = _mongo_client["islamic_bot"]
        try:
            await db["bot_data"].create_index("_id")
            await db["delete_queue"].create_index("scheduled_at")
            await db["reminders"].create_index("fire_at")  # لتسريع استعلامات التذكيرات
        except Exception:
            pass
        logger.info("✅ MongoDB متصل بنجاح")
    except Exception as e:
        logger.error(f"❌ فشل الاتصال بـ MongoDB: {e} — سيُستخدم الملف المحلي")
        _mongo_db = None

# ─── البيانات في الذاكرة ──────────────────────────────────────────────────────
active_groups:   set  = set()
active_members:  dict = {}
last_chosen:     dict = {}   # محتفظ به للتوافق مع البيانات القديمة فقط
pending_deletes: list = []   # [{chat_id, message_id, delete_at}]
rotation_queue:  dict = {}   # {chat_id: [user_id, ...]} طابور الدوران
group_locations:   dict = {}   # {chat_id: {"city": "...", "country": "..."}} موقع كل مجموعة
city_reminders:    dict = {}   # {chat_id: عدد_التنبيهات} — يتوقف بعد 7
locked_groups:      set  = set()  # مجموعات مقفلة — الأوامر الفورية للأدمن فقط
paused_groups:      set  = set()  # مجموعات موقوفة — لا يرسل البوت لها أي شيء
_city_reminder_running: bool = False  # منع التشغيل المتزامن
_active_tasbih_chats:   set  = set()  # مجموعات لا تزال في سلسلة تسبيح نشطة
_data_ready:            bool = False  # True بعد تحميل MongoDB
group_topics:       dict = {}     # {chat_id: thread_id} — topic محدد لكل مجموعة
_sent_prayer:       dict = {}  # {chat_id: {prayer: "YYYY-MM-DD HH:MM"}} تذكير حلول الصلاة
_sent_prep:         dict = {}  # {chat_id: {prayer: "YYYY-MM-DD"}} تذكير التحضير
_prayer_cache:      dict = {}  # {(city,country,date): timings} لتقليل استدعاءات API

MAX_CITY_REMINDERS = 7         # الحد الأقصى لتنبيهات /setcity

# ══════════════════════════════════════════════════════════════════════════════
# حفظ وتحميل بيانات المجموعات والأعضاء
# ══════════════════════════════════════════════════════════════════════════════

def _user_to_dict(user) -> dict:
    if hasattr(user, 'id'):
        return {"id": user.id, "first_name": user.first_name or "", "username": user.username or ""}
    return user


def _build_save_data() -> dict:
    serializable_members = {
        str(cid): {str(uid): _user_to_dict(u) for uid, u in members.items()}
        for cid, members in active_members.items()
    }
    return {
        "active_groups":  list(active_groups),
        "active_members": serializable_members,
        "last_chosen":    {str(k): v for k, v in last_chosen.items()},
        "rotation_queue":  {str(k): v for k, v in rotation_queue.items()},
        "group_locations": {str(k): v for k, v in group_locations.items()},
        "city_reminders":  {str(k): v for k, v in city_reminders.items()},
        "locked_groups":   list(locked_groups),
        "paused_groups":   list(paused_groups),
        "group_topics":    {str(k): v for k, v in group_topics.items()},
        "saved_at":        datetime.now(ALGERIA_TZ).isoformat(),
    }


def save_data():
    data = _build_save_data()
    # حفظ محلي دائماً كـ fallback
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ خطأ في الحفظ المحلي: {e}")
    # حفظ MongoDB (async — نطلقها كـ task)
    db = _get_db()
    if db is not None:
        async def _mongo_save():
            try:
                await db["bot_data"].replace_one(
                    {"_id": "main"}, {"_id": "main", **data}, upsert=True
                )
            except Exception as e:
                logger.error(f"❌ خطأ في MongoDB save_data: {e}")
        try:
            asyncio.get_running_loop()
            asyncio.create_task(_mongo_save())
        except RuntimeError:
            pass  # لا يوجد loop نشط
        except Exception:
            pass
    # لا نُفرّغ active_members لأن job_tasbih يحتاجه
    # بدلاً من ذلك نحدّ حجمه عند التسجيل فقط (MAX_MEMBERS_PER_GROUP)
    logger.info(f"💾 حُفظ: {len(active_groups)} مجموعة | "
                f"{sum(len(v) for v in active_members.values())} عضو")


def _apply_data(data: dict):
    global active_groups, active_members, last_chosen, rotation_queue, group_locations, city_reminders, locked_groups, group_topics, paused_groups
    active_groups   = set(data.get("active_groups", []))
    raw             = data.get("active_members", {})
    active_members  = {int(cid): {int(uid): u for uid, u in members.items()} for cid, members in raw.items()}
    last_chosen     = {int(k): v for k, v in data.get("last_chosen",    {}).items()}
    rotation_queue  = {int(k): v for k, v in data.get("rotation_queue", {}).items()}
    group_locations = {int(k): v for k, v in data.get("group_locations",{}).items()}
    city_reminders  = {int(k): v for k, v in data.get("city_reminders", {}).items()}
    locked_groups   = set(int(x) for x in data.get("locked_groups", []))
    paused_groups   = set(int(x) for x in data.get("paused_groups", []))
    group_topics    = {int(k): v for k, v in data.get("group_topics",   {}).items()}
    logger.info(f"✅ بيانات محملة | {len(active_groups)} مجموعة | "
                f"{sum(len(v) for v in active_members.values())} عضو | "
                f"آخر حفظ: {data.get('saved_at','؟')}")


async def load_data_async():
    """يحمّل البيانات من MongoDB — يُستدعى عند الإقلاع"""
    db = _get_db()
    if db is not None:
        try:
            doc = await db["bot_data"].find_one({"_id": "main"})
            if doc:
                doc.pop("_id", None)
                _apply_data(doc)
                logger.info("☁️ البيانات محمّلة من MongoDB")
                return
            logger.info("📂 MongoDB فارغة — سيُحاول الملف المحلي")
        except Exception as e:
            logger.error(f"❌ خطأ في MongoDB load: {e}")
    # fallback: الملف المحلي
    load_data()


def load_data():
    global active_groups, active_members, last_chosen, rotation_queue, group_locations, city_reminders, locked_groups
    if not os.path.exists(DATA_FILE):
        logger.info("📂 لا يوجد ملف حفظ سابق")
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _apply_data(data)
    except Exception as e:
        logger.error(f"❌ خطأ في تحميل البيانات: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# نظام الإعدادات الديناميكية — تُحفظ في bot_config.json
# ══════════════════════════════════════════════════════════════════════════════

# الإعدادات الافتراضية للمواعيد
DEFAULT_SCHEDULE = {
    "morning_adhkar":   {"hour": 7,  "minute": 0},
    "evening_adhkar":   {"hour": 16, "minute": 30},
    "dhikr_1":          {"hour": 9,  "minute": 0},
    "dhikr_2":          {"hour": 13, "minute": 30},
    "dhikr_3":          {"hour": 21, "minute": 0},
    "hadith_1":         {"hour": 8,  "minute": 0},
    "hadith_2":         {"hour": 19, "minute": 0},
    "quran_1":          {"hour": 10, "minute": 0},
    "quran_2":          {"hour": 22, "minute": 0},
    "tasbih_1":         {"hour": 11, "minute": 0},
    "tasbih_2":         {"hour": 20, "minute": 0},
}

# أسماء المهام بالعربي للعرض
SCHEDULE_LABELS = {
    "morning_adhkar": "أذكار الصباح",
    "evening_adhkar": "أذكار المساء",
    "dhikr_1":        "ذكر عشوائي (الأول)",
    "dhikr_2":        "ذكر عشوائي (الثاني)",
    "dhikr_3":        "ذكر عشوائي (الثالث)",
    "hadith_1":       "حديث نبوي (الأول)",
    "hadith_2":       "حديث نبوي (الثاني)",
    "quran_1":        "آية قرآنية (الأولى)",
    "quran_2":        "آية قرآنية (الثانية)",
    "tasbih_1":       "تحدي التسبيح (الأول)",
    "tasbih_2":       "تحدي التسبيح (الثاني)",
}

# ربط اسم المهمة بالدالة المقابلة (يُملأ لاحقاً بعد تعريف الدوال)
SCHEDULE_FUNCS = {}

# الإعدادات المحمّلة في الذاكرة
bot_config: dict = {}


def save_config():
    # حفظ محلي
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ خطأ في حفظ الإعدادات محلياً: {e}")
    # MongoDB
    db = _get_db()
    if db is not None:
        async def _save():
            try:
                await db["bot_config"].replace_one(
                    {"_id": "config"}, {"_id": "config", **bot_config}, upsert=True
                )
            except Exception as e:
                logger.error(f"❌ MongoDB save_config: {e}")
        try:
            asyncio.get_running_loop()
            asyncio.create_task(_save())
        except RuntimeError:
            pass
        except Exception:
            pass
    logger.info("💾 الإعدادات حُفظت")


def load_config():
    global bot_config
    if not os.path.exists(CONFIG_FILE):
        bot_config = {
            "schedule":      dict(DEFAULT_SCHEDULE),
            "extra_adhkar":  [],
            "extra_hadiths": [],
            "extra_verses":  [],
        }
        save_config()
        logger.info("📂 إعدادات افتراضية أُنشئت")
        return
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            bot_config = json.load(f)
        # تأكد من وجود كل المفاتيح
        bot_config.setdefault("schedule",      dict(DEFAULT_SCHEDULE))
        bot_config.setdefault("extra_adhkar",  [])
        bot_config.setdefault("extra_hadiths", [])
        bot_config.setdefault("extra_verses",  [])
        # أضف أي مهمة جديدة غائبة
        for k, v in DEFAULT_SCHEDULE.items():
            bot_config["schedule"].setdefault(k, v)
        logger.info("✅ الإعدادات محمّلة")
    except Exception as e:
        logger.error(f"❌ خطأ في تحميل الإعدادات: {e}")
        bot_config = {"schedule": dict(DEFAULT_SCHEDULE),
                      "extra_adhkar": [], "extra_hadiths": [], "extra_verses": []}


def get_all_adhkar() -> list:
    """يجمع الأذكار الأساسية + المُضافة من تيليغرام"""
    return ADHKAR_LIST + bot_config.get("extra_adhkar", [])


def get_all_hadiths() -> list:
    """يجمع الأحاديث الأساسية + المُضافة من تيليغرام"""
    extra = []
    for h in bot_config.get("extra_hadiths", []):
        parts = h.split("|", 1)
        extra.append({"text": parts[0].strip(), "source": parts[1].strip() if len(parts) > 1 else "مُضاف يدوياً"})
    return HADITHS_LIST + extra


def get_all_verses() -> list:
    """يجمع الآيات الأساسية + المُضافة من تيليغرام"""
    extra = []
    for v in bot_config.get("extra_verses", []):
        parts = v.split("|", 1)
        extra.append({"text": parts[0].strip(), "surah": parts[1].strip() if len(parts) > 1 else "مُضاف يدوياً"})
    return QURAN_VERSES + extra


def reschedule_job(scheduler, job_key: str, bot):
    """يُعيد جدولة مهمة واحدة فوراً بدون إعادة تشغيل"""
    if job_key not in SCHEDULE_FUNCS:
        return False
    t    = bot_config["schedule"].get(job_key, DEFAULT_SCHEDULE.get(job_key, {}))
    hour = t.get("hour", 0)
    minute = t.get("minute", 0)
    func = SCHEDULE_FUNCS[job_key]
    # احذف القديمة إن وجدت
    if scheduler.get_job(job_key):
        scheduler.remove_job(job_key)
    scheduler.add_job(
        func,
        trigger="cron",
        hour=hour, minute=minute,
        timezone=ALGERIA_TZ,
        args=[bot],
        id=job_key,
    )
    logger.info(f"🔄 أُعيدت جدولة '{job_key}' → {hour:02d}:{minute:02d}")
    return True

# ══════════════════════════════════════════════════════════════════════════════
# قائمة الحذف الدائمة — تصمد بعد إعادة التشغيل
# ══════════════════════════════════════════════════════════════════════════════

def save_delete_queue():
    try:
        with open(DELETE_QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(pending_deletes, f)
    except Exception as e:
        logger.error(f"❌ خطأ في حفظ قائمة الحذف محلياً: {e}")
    db = _get_db()
    if db is not None:
        async def _save():
            try:
                await db["delete_queue"].replace_one(
                    {"_id": "queue"}, {"_id": "queue", "items": pending_deletes}, upsert=True
                )
            except Exception as e:
                logger.error(f"❌ MongoDB save_delete_queue: {e}")
        try:
            asyncio.get_running_loop()
            asyncio.create_task(_save())
        except RuntimeError:
            pass
        except Exception:
            pass


def load_delete_queue():
    global pending_deletes
    if not os.path.exists(DELETE_QUEUE_FILE):
        return
    try:
        with open(DELETE_QUEUE_FILE, "r", encoding="utf-8") as f:
            pending_deletes = json.load(f)
        now = datetime.now(ALGERIA_TZ).timestamp()
        pending_deletes = [i for i in pending_deletes if i["delete_at"] > now - 7200]
        logger.info(f"📋 قائمة الحذف: {len(pending_deletes)} رسالة معلقة")
    except Exception as e:
        logger.error(f"❌ خطأ في تحميل قائمة الحذف: {e}")


def add_to_delete_queue(chat_id: int, message_id: int):
    """أضف رسالة لقائمة الحذف مع timestamp الحذف"""
    delete_at = (datetime.now(ALGERIA_TZ) + timedelta(seconds=DELETE_DELAY)).timestamp()
    pending_deletes.append({"chat_id": chat_id, "message_id": message_id, "delete_at": delete_at})
    save_delete_queue()


# سجل عمليات الحذف (للمراجعة)
delete_log: list = []   # [{"msg_id", "chat_id", "status", "at"}]
MAX_DELETE_LOG = 200    # احتفظ بآخر 200 عملية فقط
MAX_MEMBERS_PER_GROUP = 200  # حد أقصى للأعضاء في RAM


async def process_delete_queue(bot: Bot):
    """
    يُشغَّل كل دقيقة — يحذف كل رسالة حان وقتها.
    يعمل حتى بعد إعادة التشغيل لأن القائمة محفوظة في ملف.
    يُسجّل نتيجة كل حذف (نجاح / فشل) في delete_log.
    """
    if not pending_deletes:
        return
    now  = datetime.now(ALGERIA_TZ).timestamp()
    done = []
    for item in list(pending_deletes):
        if now >= item["delete_at"]:
            retries = item.get("retries", 0)
            try:
                await bot.delete_message(
                    chat_id=item["chat_id"], message_id=item["message_id"]
                )
                logger.info(f"🗑️ حُذفت {item['message_id']} من {item['chat_id']}")
                delete_log.append({
                    "msg_id":  item["message_id"],
                    "chat_id": item["chat_id"],
                    "status":  "✅ حُذفت",
                    "at":      datetime.now(ALGERIA_TZ).strftime("%H:%M:%S"),
                })
                done.append(item)
            except TelegramError as e:
                err = str(e).lower()
                if "message to delete not found" in err or "message can't be deleted" in err:
                    # الرسالة لم تعد موجودة — أزلها من القائمة
                    delete_log.append({
                        "msg_id":  item["message_id"],
                        "chat_id": item["chat_id"],
                        "status":  f"⚠️ غير موجودة",
                        "at":      datetime.now(ALGERIA_TZ).strftime("%H:%M:%S"),
                    })
                    done.append(item)
                elif retries < 3:
                    # أعد المحاولة لاحقاً (بعد دقيقتين)
                    item["retries"]   = retries + 1
                    item["delete_at"] = now + 120
                    logger.warning(
                        f"⚠️ فشل حذف {item['message_id']} (محاولة {retries+1}/3): {e}"
                    )
                else:
                    # تجاوز الحد — سجّل وأزل
                    delete_log.append({
                        "msg_id":  item["message_id"],
                        "chat_id": item["chat_id"],
                        "status":  f"❌ فشل نهائي: {e}",
                        "at":      datetime.now(ALGERIA_TZ).strftime("%H:%M:%S"),
                    })
                    logger.error(
                        f"❌ تخلّي عن حذف {item['message_id']} بعد 3 محاولات: {e}"
                    )
                    done.append(item)
    if done:
        for item in done:
            if item in pending_deletes:
                pending_deletes.remove(item)
        save_delete_queue()
    # تحديد حجم السجل
    if len(delete_log) > MAX_DELETE_LOG:
        del delete_log[:len(delete_log) - MAX_DELETE_LOG]

# ══════════════════════════════════════════════════════════════════════════════
# نظام الدوران الكامل (Round Robin)
# ══════════════════════════════════════════════════════════════════════════════

def _rebuild_queue(chat_id: int, valid_ids: list):
    """أعد بناء طابور الدوران مخلوطاً عشوائياً"""
    q = list(valid_ids)
    random.shuffle(q)
    rotation_queue[chat_id] = q


def pick_member_round_robin(chat_id: int, valid_members: list):
    """
    يختار العضو التالي في دورة الدوران.
    - لا يُكرر أحداً حتى يمر على كل الأعضاء.
    - عند انتهاء الدورة يبدأ دورة جديدة مخلوطة.
    - يتجاهل من غادر المجموعة تلقائياً.
    """
    if not valid_members:
        return None

    members_by_id = {}
    for u in valid_members:
        uid = u.get("id") if isinstance(u, dict) else u.id
        members_by_id[uid] = u

    valid_ids = list(members_by_id.keys())

    queue = rotation_queue.get(chat_id, [])
    queue = [uid for uid in queue if uid in members_by_id]

    if not queue:
        _rebuild_queue(chat_id, valid_ids)
        queue = rotation_queue[chat_id]
        logger.info(f"🔄 دورة جديدة في {chat_id} — {len(queue)} عضو")

    chosen_id = queue.pop(0)
    rotation_queue[chat_id] = queue
    save_data()
    return members_by_id[chosen_id]

# ══════════════════════════════════════════════════════════════════════════════
# Telethon — جلب قائمة الأعضاء الكاملة
# ══════════════════════════════════════════════════════════════════════════════

async def get_telethon_client():
    """يُنشئ عميل Telethon أو يُعيد الموجود"""
    global _telethon_client
    if not TELETHON_AVAILABLE:
        return None
    if not TELETHON_API_ID or not TELETHON_API_HASH or not TELETHON_SESSION:
        logger.warning("⚠️ متغيرات Telethon ناقصة (API_ID / API_HASH / SESSION_STRING)")
        return None
    try:
        if _telethon_client is None or not _telethon_client.is_connected():
            _telethon_client = TelegramClient(
                StringSession(TELETHON_SESSION), TELETHON_API_ID, TELETHON_API_HASH
            )
            await _telethon_client.connect()
            if not await _telethon_client.is_user_authorized():
                logger.error("❌ جلسة Telethon منتهية أو غير صالحة")
                _telethon_client = None
                return None
            logger.info("✅ Telethon متصل بنجاح")
        return _telethon_client
    except Exception as e:
        logger.error(f"❌ خطأ Telethon: {e}")
        _telethon_client = None
        return None


# رموز الأخطاء المعروفة عند حجب الأعضاء
MEMBERS_HIDDEN_ERRORS = (
    "ChatAdminRequiredError",
    "ChannelPrivateError",
    "ChatWriteForbiddenError",
    "UserNotParticipantError",
    "chat_admin_required",
    "channel_private",
)


async def fetch_all_members(chat_id: int) -> tuple:
    """
    يجلب كل أعضاء المجموعة عبر Telethon.
    يُعيد (list, error_reason):
      - (members, None)       → نجح الجلب
      - ([], "hidden")        → المجموعة تحجب الأعضاء
      - ([], "no_client")     → Telethon غير متاح
      - ([], "error: ...")    → خطأ غير متوقع
    """
    client = await get_telethon_client()
    if not client:
        return [], "no_client"
    try:
        result = []
        offset = 0
        limit  = 200
        while True:
            participants = await client(GetParticipantsRequest(
                channel=chat_id,
                filter=ChannelParticipantsSearch(""),
                offset=offset,
                limit=limit,
                hash=0,
            ))
            if not participants.users:
                break
            for user in participants.users:
                if not user.bot and not user.deleted:
                    result.append({
                        "id":         user.id,
                        "first_name": user.first_name or "",
                        "username":   user.username or "",
                    })
            offset += len(participants.users)
            if offset >= participants.count:
                break
        logger.info(f"👥 Telethon جلب {len(result)} عضو من {chat_id}")
        return result, None

    except Exception as e:
        err_str = type(e).__name__ + " " + str(e)
        # هل السبب هو حجب الأعضاء؟
        if any(h.lower() in err_str.lower() for h in MEMBERS_HIDDEN_ERRORS):
            logger.warning(f"🔒 المجموعة {chat_id} تحجب قائمة الأعضاء — سيُجمعون تدريجياً")
            return [], "hidden"
        logger.error(f"❌ Telethon — خطأ في جلب أعضاء {chat_id}: {e}")
        return [], f"error: {e}"


async def sync_group_members(chat_id: int) -> dict:
    """
    يزامن أعضاء مجموعة ويُعيد dict:
      {"count": N, "reason": None | "hidden" | "no_client" | "channel" | "error:..."}
    القنوات تُتخطى تلقائياً — لا أعضاء تُجمَع منها.
    """
    # تحقق من النوع عبر Telethon client إن أمكن
    client = await get_telethon_client()
    if client:
        try:
            from telethon.tl.types import Channel as TLChannel
            entity = await client.get_entity(chat_id)
            if isinstance(entity, TLChannel) and entity.broadcast:
                logger.info(f"📢 تخطي القناة في المزامنة: {chat_id}")
                return {"count": 0, "reason": "channel"}
        except Exception:
            pass  # إذا فشل التحقق، نكمل بشكل طبيعي
    members, reason = await fetch_all_members(chat_id)
    if members:
        if chat_id not in active_members:
            active_members[chat_id] = {}
        for m in members:
            active_members[chat_id][m["id"]] = m
        active_groups.add(chat_id)
        save_data()
    return {"count": len(members), "reason": reason}


async def sync_all_groups() -> dict:
    """يزامن كل المجموعات النشطة — يُشغَّل كل يوم الساعة 3:00"""
    results = {}
    for chat_id in list(active_groups):
        results[chat_id] = await sync_group_members(chat_id)
    total   = sum(r["count"] for r in results.values())
    hidden  = sum(1 for r in results.values() if r["reason"] == "hidden")
    logger.info(f"🔄 مزامنة يومية: {len(results)} مجموعة | {total} عضو | {hidden} محجوبة")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# وظائف مساعدة عامة
# ══════════════════════════════════════════════════════════════════════════════

_scheduler: AsyncIOScheduler = None


def get_user_display(user_data) -> str:
    if hasattr(user_data, 'id'):
        uid, first_name, username = user_data.id, user_data.first_name or "عضو", user_data.username or ""
    else:
        uid, first_name, username = user_data.get("id", 0), user_data.get("first_name", "عضو"), user_data.get("username", "")
    if username:
        return f'<a href="https://t.me/{username}">@{username}</a>'
    return f'<a href="tg://user?id={uid}">{first_name}</a>'


async def broadcast(bot: Bot, text: str, parse_mode: str = "Markdown", force: bool = False):
    """يُرسل لكل المجموعات — يراعي الـ Topic + rate limiting"""
    logger.info(f"📢 broadcast: {len(active_groups)} مجموعة، موقوفة: {len(paused_groups)}")
    dead = set()
    for chat_id in list(active_groups):
        if chat_id in paused_groups:
            logger.debug(f"  ⏸ {chat_id} موقوف — تخطّ")
            continue  # مجموعة موقوفة — تخطّ
        try:
            thread_id = get_group_thread(chat_id)
            kwargs = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            msg = await bot.send_message(**kwargs)
            add_to_delete_queue(chat_id, msg.message_id)
            await asyncio.sleep(0.05)
        except TelegramError as e:
            if any(k in str(e).lower() for k in ("kicked", "not found", "blocked", "deactivated")):
                dead.add(chat_id)
    if dead:
        active_groups.difference_update(dead)
        for cid in dead:
            active_members.pop(cid, None)
        save_data()


DEFAULT_CITY    = "Tiaret"
DEFAULT_COUNTRY = "Algeria"



def get_group_thread(chat_id: int):
    """يُرجع thread_id للمجموعة إذا كانت لها topic محدد، وإلا None"""
    return group_topics.get(chat_id)

def get_group_location(chat_id: int) -> tuple:
    """يُعيد (city, country) لمجموعة معينة أو القيم الافتراضية"""
    loc = group_locations.get(chat_id, {})
    return loc.get("city", DEFAULT_CITY), loc.get("country", DEFAULT_COUNTRY)


async def get_prayer_times(city: str = DEFAULT_CITY, country: str = DEFAULT_COUNTRY) -> dict | None:
    """يجلب مواقيت الصلاة لمدينة وبلد محددين — مع cache + retry تلقائي"""
    global _prayer_cache
    today = datetime.now(ALGERIA_TZ).strftime("%Y-%m-%d")
    cache_key = (city.lower(), country.lower(), today)
    if cache_key in _prayer_cache:
        return _prayer_cache[cache_key]
    url = "https://api.aladhan.com/v1/timingsByCity"
    # قائمة المحاولات: الأولى بالبلد المحدد، الثانية بـ Algeria كـ fallback
    attempts = [(city, country)]
    if country != "Algeria":
        attempts.append((city, "Algeria"))
    # بعض المدن الجزائرية تحتاج اسم مختلف في API
    city_aliases = {
        "Tiaret": "Tiaret", "Annaba": "Annaba", "Oran": "Oran",
        "Constantine": "Constantine", "Algiers": "Algiers",
        "Blida": "Blida", "Setif": "Sétif", "Biskra": "Biskra",
        "Tlemcen": "Tlemcen", "Batna": "Batna",
    }
    alt = city_aliases.get(city)
    if alt and alt != city:
        attempts.append((alt, country))
    for c, co in attempts:
        params = {"city": c, "country": co, "method": 2, "school": 0}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
                async with s.get(url, params=params) as r:
                    data = await r.json()
                    if data.get("code") == 200:
                        logger.info(f"✅ مواقيت {c} ({co}) جُلبت بنجاح")
                        _prayer_cache[cache_key] = data["data"]["timings"]
                        # احتفظ بالكاش ليوم واحد فقط — نظّف القديم
                        _prayer_cache = {k: v for k, v in _prayer_cache.items() if k[2] == today}
                        return data["data"]["timings"]
                    logger.warning(f"⚠️ API: code={data.get('code')} لـ {c}, {co}")
        except Exception as e:
            logger.error(f"❌ خطأ في جلب المواقيت ({c}): {e}")
    return None


async def validate_city(city: str, country: str) -> bool:
    """يتحقق أن المدينة صالحة — أولاً القائمة الثابتة، ثم API"""
    # فحص القائمة الثابتة أولاً (سريع بدون شبكة)
    if city.lower() in KNOWN_ALGERIAN_CITIES:
        return True
    # محاولة عبر API
    result = await get_prayer_times(city, country)
    return result is not None


async def verify_groups(bot: Bot):
    """يتحقق من المجموعات المحفوظة ويزيل المعطوبة"""
    if not active_groups:
        return
    logger.info(f"🔍 التحقق من {len(active_groups)} مجموعة محفوظة...")
    dead = set()
    for chat_id in list(active_groups):
        try:
            chat = await bot.get_chat(chat_id)
            logger.info(f"  ✅ {chat.title} ({chat_id})")
        except TelegramError as e:
            logger.warning(f"  ❌ مجموعة {chat_id}: {e}")
            dead.add(chat_id)
    if dead:
        active_groups.difference_update(dead)
        for cid in dead:
            active_members.pop(cid, None)
        save_data()



async def discover_all_groups(bot: Bot):
    """
    عند الإقلاع وactive_groups فارغة:
    يستخدم Telethon لاكتشاف كل المجموعات/القنوات التي البوت فيها أدمن،
    ويسجّلها تلقائياً دون الحاجة لـ /start من أي شخص.
    """
    if active_groups:
        logger.info(f"📋 {len(active_groups)} مجموعة محفوظة — لا حاجة للاكتشاف")
        return

    logger.info("🔍 active_groups فارغة — بدء الاكتشاف التلقائي عبر Telethon...")
    client = await get_telethon_client()
    if not client:
        logger.warning("⚠️ Telethon غير متاح — الاكتشاف التلقائي معطّل")
        logger.info("💡 اطلب من أعضاء كل مجموعة إرسال /start لإعادة التسجيل")
        return

    found = 0
    try:
        from telethon.tl.types import Channel, Chat
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            chat_id = None

            # مجموعة عادية
            if isinstance(entity, Chat):
                chat_id = -entity.id
            # سوبرجروب أو قناة
            elif isinstance(entity, Channel):
                chat_id = int(f"-100{entity.id}")
            else:
                continue

            # تحقق أن البوت أدمن فيها
            try:
                me = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
                if me.status not in ("administrator", "creator", "member"):
                    continue
            except Exception:
                continue

            active_groups.add(chat_id)
            found += 1
            logger.info(f"  ✅ اكتُشفت: {dialog.name} ({chat_id})")

            # جلب الأدمن كأعضاء أساسيين
            try:
                admins = await bot.get_chat_administrators(chat_id)
                if chat_id not in active_members:
                    active_members[chat_id] = {}
                for admin in admins:
                    if not admin.user.is_bot:
                        active_members[chat_id][admin.user.id] = _user_to_dict(admin.user)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"❌ خطأ في الاكتشاف التلقائي: {e}")

    if found:
        save_data()
        logger.info(f"✅ اكتُشفت وسُجّلت {found} مجموعة/قناة تلقائياً")

        # أرسل رسالة استئناف لمجموعة الاختبار فقط
        try:
            await bot.send_message(
                chat_id=-1003677077673,
                text="🔄 <b>البوت عاد للعمل</b> ✅\n\nتم استئناف كل الخدمات تلقائياً 🤲",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        logger.warning("⚠️ لم يُعثر على أي مجموعة — اطلب من الأعضاء إرسال /start")

async def auto_register_admin_groups(bot: Bot):
    """
    يبحث عن أي مجموعة يكون البوت فيها أدمن لكنها غير مسجّلة،
    ويضيفها تلقائياً للقائمة النشطة عند كل بدء تشغيل.
    يعتمد على الأوامر الأخيرة التي تلقّاها البوت من المجموعات.
    """
    # هذا سيُنفَّذ مرة واحدة عند الإقلاع
    # لا يمكن جلب قائمة المجموعات من Telegram API مباشرةً،
    # لكن يمكن التحقق من المجموعات المعروفة في active_groups
    # وإرسال رسالة ترحيب في أي مجموعة جديدة تضاف لاحقاً
    added = 0
    for chat_id in list(active_groups):
        try:
            me     = await bot.get_me()
            member = await bot.get_chat_member(chat_id, me.id)
            if member.status == "administrator":
                logger.info(f"  👑 البوت أدمن في: {chat_id}")
            else:
                logger.info(f"  👤 البوت عضو في: {chat_id}")
        except TelegramError:
            pass
    logger.info(f"🔍 فحص الصلاحيات: {len(active_groups)} مجموعة")
    return added

# ══════════════════════════════════════════════════════════════════════════════
# النصوص
# ══════════════════════════════════════════════════════════════════════════════

PRAYER_NAMES = {
    "Fajr":    "🌙 الفجر",
    "Sunrise": "🌄 الشروق",
    "Dhuhr":   "☀️ الظهر",
    "Asr":     "🌤️ العصر",
    "Maghrib": "🌅 المغرب",
    "Isha":    "🌙 العشاء",
}
PRAYERS_TO_REMIND = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]

PRAYER_MESSAGES = {
    "Fajr": (
        "🌙 *حان وقت صلاة الفجر* ⏰\n\n"
        "_الصلاةُ خيرٌ من النوم_\n"
        "الصلاة الصلاة رحمكم الله 🤲\n\n"
        "💎 قال ﷺ: *«مَنْ صَلَّى الْبَرْدَيْنِ دَخَلَ الْجَنَّةَ»*\n"
        "_متفق عليه_"
    ),
    "Dhuhr": (
        "☀️ *حان وقت صلاة الظهر* ⏰\n\n"
        "_حيَّ على الصلاة.. حيَّ على الفلاح_\n"
        "الصلاة الصلاة رحمكم الله 🤲\n\n"
        "💎 قال تعالى: *﴿ إِنَّ الصَّلَاةَ كَانَتْ عَلَى الْمُؤْمِنِينَ كِتَابًا مَّوْقُوتًا ﴾*\n"
        "_سورة النساء — 103_"
    ),
    "Asr": (
        "🌤️ *حان وقت صلاة العصر* ⏰\n\n"
        "_حيَّ على الصلاة.. حيَّ على الفلاح_\n"
        "الصلاة الصلاة رحمكم الله 🤲\n\n"
        "💎 قال ﷺ: *«مَنْ فَاتَتْهُ صَلَاةُ الْعَصْرِ فَكَأَنَّمَا وُتِرَ أَهْلَهُ وَمَالَهُ»*\n"
        "_متفق عليه_"
    ),
    "Maghrib": (
        "🌅 *حان وقت صلاة المغرب* ⏰\n\n"
        "_حيَّ على الصلاة.. حيَّ على الفلاح_\n"
        "الصلاة الصلاة رحمكم الله 🤲\n\n"
        "💎 قال تعالى: *﴿ وَأَقِمِ الصَّلَاةَ طَرَفَيِ النَّهَارِ وَزُلَفًا مِّنَ اللَّيْلِ ﴾*\n"
        "_سورة هود — 114_"
    ),
    "Isha": (
        "🌙 *حان وقت صلاة العشاء* ⏰\n\n"
        "_حيَّ على الصلاة.. حيَّ على الفلاح_\n"
        "الصلاة الصلاة رحمكم الله 🤲\n\n"
        "💎 قال ﷺ: *«لَوْ يَعْلَمُ النَّاسُ مَا فِي النِّدَاءِ وَالصَّفِّ الْأَوَّلِ ثُمَّ لَمْ يَجِدُوا إِلَّا أَنْ يَسْتَهِمُوا عَلَيْهِ لَاسْتَهَمُوا»*\n"
        "_متفق عليه_"
    ),
}

ADHKAR_LIST = [
    "سُبْحَانَ اللهِ وَبِحَمْدِهِ ١٠٠ مرة",
    "سُبْحَانَ اللهِ الْعَظِيمِ",
    "لَا إِلَهَ إِلَّا اللهُ وَحْدَهُ لَا شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَى كُلِّ شَيْءٍ قَدِيرٌ",
    "اللَّهُمَّ صَلِّ وَسَلِّمْ وَبَارِكْ عَلَى نَبِيِّنَا مُحَمَّدٍ",
    "أَسْتَغْفِرُ اللهَ وَأَتُوبُ إِلَيْهِ",
    "سُبْحَانَ اللهِ وَالْحَمْدُ لِلَّهِ وَلَا إِلَهَ إِلَّا اللهُ وَاللهُ أَكْبَرُ",
    "لَا حَوْلَ وَلَا قُوَّةَ إِلَّا بِاللَّهِ الْعَلِيِّ الْعَظِيمِ",
    "رَبِّ اغْفِرْ لِي وَتُبْ عَلَيَّ إِنَّكَ أَنْتَ التَّوَّابُ الرَّحِيمُ",
    "اللَّهُمَّ إِنِّي أَسْأَلُكَ الْعَفْوَ وَالْعَافِيَةَ فِي الدُّنْيَا وَالآخِرَةِ",
    "اللَّهُمَّ أَعِنِّي عَلَى ذِكْرِكَ وَشُكْرِكَ وَحُسْنِ عِبَادَتِكَ",
    "اللَّهُمَّ إِنِّي أَعُوذُ بِكَ مِنَ الْهَمِّ وَالْحَزَنِ، وَأَعُوذُ بِكَ مِنَ الْعَجْزِ وَالْكَسَلِ",
    "حَسْبِيَ اللهُ لَا إِلَهَ إِلَّا هُوَ، عَلَيْهِ تَوَكَّلْتُ وَهُوَ رَبُّ الْعَرْشِ الْعَظِيمِ",
    "اللَّهُمَّ بَارِكْ لَنَا فِيمَا رَزَقْتَنَا وَقِنَا عَذَابَ النَّارِ",
    "سُبْحَانَكَ اللَّهُمَّ وَبِحَمْدِكَ، أَشْهَدُ أَنْ لَا إِلَهَ إِلَّا أَنْتَ، أَسْتَغْفِرُكَ وَأَتُوبُ إِلَيْكَ",
    "اللَّهُمَّ أَنْتَ رَبِّي لَا إِلَهَ إِلَّا أَنْتَ، خَلَقْتَنِي وَأَنَا عَبْدُكَ",
    "يَا حَيُّ يَا قَيُّومُ بِرَحْمَتِكَ أَسْتَغِيثُ، أَصْلِحْ لِي شَأْنِي كُلَّهُ وَلَا تَكِلْنِي إِلَى نَفْسِي طَرْفَةَ عَيْنٍ",
]

HADITHS_LIST = [
    {"text": "إِنَّمَا الأَعْمَالُ بِالنِّيَّاتِ، وَإِنَّمَا لِكُلِّ امْرِئٍ مَا نَوَى", "source": "متفق عليه"},
    {"text": "الْمُسْلِمُ مَنْ سَلِمَ الْمُسْلِمُونَ مِنْ لِسَانِهِ وَيَدِهِ", "source": "متفق عليه"},
    {"text": "مَنْ كَانَ يُؤْمِنُ بِاللهِ وَالْيَوْمِ الآخِرِ فَلْيَقُلْ خَيْرًا أَوْ لِيَصْمُتْ", "source": "متفق عليه"},
    {"text": "لَا يُؤْمِنُ أَحَدُكُمْ حَتَّى يُحِبَّ لأَخِيهِ مَا يُحِبُّ لِنَفْسِهِ", "source": "متفق عليه"},
    {"text": "إِنَّ اللهَ لَا يَنْظُرُ إِلَى صُوَرِكُمْ وَأَمْوَالِكُمْ، وَلَكِنْ يَنْظُرُ إِلَى قُلُوبِكُمْ وَأَعْمَالِكُمْ", "source": "رواه مسلم"},
    {"text": "أَحَبُّ الأَعْمَالِ إِلَى اللهِ أَدْوَمُهَا وَإِنْ قَلَّ", "source": "متفق عليه"},
    {"text": "الدُّنْيَا سِجْنُ الْمُؤْمِنِ وَجَنَّةُ الْكَافِرِ", "source": "رواه مسلم"},
    {"text": "مَنْ سَلَكَ طَرِيقًا يَلْتَمِسُ فِيهِ عِلْمًا، سَهَّلَ اللهُ لَهُ بِهِ طَرِيقًا إِلَى الْجَنَّةِ", "source": "رواه مسلم"},
    {"text": "تَبَسُّمُكَ فِي وَجْهِ أَخِيكَ صَدَقَةٌ", "source": "رواه الترمذي"},
    {"text": "إِنَّ مِنْ أَحَبِّكُمْ إِلَيَّ وَأَقْرَبِكُمْ مِنِّي مَجْلِسًا يَوْمَ الْقِيَامَةِ أَحَاسِنَكُمْ أَخْلَاقًا", "source": "رواه الترمذي"},
    {"text": "اتَّقِ اللهَ حَيْثُمَا كُنْتَ، وَأَتْبِعِ السَّيِّئَةَ الْحَسَنَةَ تَمْحُهَا، وَخَالِقِ النَّاسَ بِخُلُقٍ حَسَنٍ", "source": "رواه الترمذي"},
    {"text": "خَيْرُكُمْ مَنْ تَعَلَّمَ الْقُرْآنَ وَعَلَّمَهُ", "source": "رواه البخاري"},
    {"text": "مَنْ قَرَأَ حَرْفًا مِنْ كِتَابِ اللهِ فَلَهُ بِهِ حَسَنَةٌ، وَالْحَسَنَةُ بِعَشْرِ أَمْثَالِهَا", "source": "رواه الترمذي"},
    {"text": "الصَّلَوَاتُ الْخَمْسُ وَالْجُمْعَةُ إِلَى الْجُمْعَةِ كَفَّارَةٌ لِمَا بَيْنَهُنَّ مَا لَمْ تُغْشَ الْكَبَائِرُ", "source": "رواه مسلم"},
    {"text": "مَنْ صَلَّى الْبَرْدَيْنِ دَخَلَ الْجَنَّةَ", "source": "متفق عليه"},
    {"text": "طَلَبُ الْعِلْمِ فَرِيضَةٌ عَلَى كُلِّ مُسْلِمٍ", "source": "رواه ابن ماجه"},
    {"text": "إِذَا مَاتَ الإِنْسَانُ انْقَطَعَ عَنْهُ عَمَلُهُ إِلَّا مِنْ ثَلَاثَةٍ: صَدَقَةٍ جَارِيَةٍ، أَوْ عِلْمٍ يُنْتَفَعُ بِهِ، أَوْ وَلَدٍ صَالِحٍ يَدْعُو لَهُ", "source": "رواه مسلم"},
    {"text": "الرَّاحِمُونَ يَرْحَمُهُمُ الرَّحْمَنُ، ارْحَمُوا مَنْ فِي الأَرْضِ يَرْحَمْكُمْ مَنْ فِي السَّمَاءِ", "source": "رواه الترمذي"},
]

QURAN_VERSES = [
    {"text": "وَمَن يَتَّقِ اللَّهَ يَجْعَل لَّهُ مَخْرَجًا ۝ وَيَرْزُقْهُ مِنْ حَيْثُ لَا يَحْتَسِبُ", "surah": "سورة الطلاق — الآيتان 2-3"},
    {"text": "أَلَا بِذِكْرِ اللَّهِ تَطْمَئِنُّ الْقُلُوبُ", "surah": "سورة الرعد — الآية 28"},
    {"text": "إِنَّ مَعَ الْعُسْرِ يُسْرًا", "surah": "سورة الشرح — الآية 6"},
    {"text": "وَإِذَا سَأَلَكَ عِبَادِي عَنِّي فَإِنِّي قَرِيبٌ ۖ أُجِيبُ دَعْوَةَ الدَّاعِ إِذَا دَعَانِ", "surah": "سورة البقرة — الآية 186"},
    {"text": "فَإِنَّ مَعَ الْعُسْرِ يُسْرًا ۝ إِنَّ مَعَ الْعُسْرِ يُسْرًا", "surah": "سورة الشرح — الآيتان 5-6"},
    {"text": "وَلَا تَيْأَسُوا مِن رَّوْحِ اللَّهِ ۖ إِنَّهُ لَا يَيْأَسُ مِن رَّوْحِ اللَّهِ إِلَّا الْقَوْمُ الْكَافِرُونَ", "surah": "سورة يوسف — الآية 87"},
    {"text": "وَعَسَىٰ أَن تَكْرَهُوا شَيْئًا وَهُوَ خَيْرٌ لَّكُمْ", "surah": "سورة البقرة — الآية 216"},
    {"text": "إِنَّ اللَّهَ مَعَ الصَّابِرِينَ", "surah": "سورة البقرة — الآية 153"},
    {"text": "وَمَا تَوْفِيقِي إِلَّا بِاللَّهِ ۚ عَلَيْهِ تَوَكَّلْتُ وَإِلَيْهِ أُنِيبُ", "surah": "سورة هود — الآية 88"},
    {"text": "رَبَّنَا آتِنَا فِي الدُّنْيَا حَسَنَةً وَفِي الْآخِرَةِ حَسَنَةً وَقِنَا عَذَابَ النَّارِ", "surah": "سورة البقرة — الآية 201"},
    {"text": "وَقُل رَّبِّ زِدْنِي عِلْمًا", "surah": "سورة طه — الآية 114"},
    {"text": "حَسْبُنَا اللَّهُ وَنِعْمَ الْوَكِيلُ", "surah": "سورة آل عمران — الآية 173"},
    {"text": "يَا أَيُّهَا الَّذِينَ آمَنُوا اسْتَعِينُوا بِالصَّبْرِ وَالصَّلَاةِ ۚ إِنَّ اللَّهَ مَعَ الصَّابِرِينَ", "surah": "سورة البقرة — الآية 153"},
    {"text": "وَمَن يَتَوَكَّلْ عَلَى اللَّهِ فَهُوَ حَسْبُهُ", "surah": "سورة الطلاق — الآية 3"},
    {"text": "إِنَّ اللَّهَ لَا يُضِيعُ أَجْرَ الْمُحْسِنِينَ", "surah": "سورة التوبة — الآية 120"},
    {"text": "وَاللَّهُ غَالِبٌ عَلَىٰ أَمْرِهِ وَلَٰكِنَّ أَكْثَرَ النَّاسِ لَا يَعْلَمُونَ", "surah": "سورة يوسف — الآية 21"},
]

MORNING_ADHKAR = """\
🌅 <b>أذكار الصباح المباركة</b>

• أَعُوذُ بِاللهِ مِنَ الشَّيْطَانِ الرَّجِيمِ
• آية الكرسي <i>(مرة واحدة)</i>
• قُل هُوَ اللهُ أَحَدٌ / الْفَلَقُ / النَّاسُ <i>(3 مرات)</i>
• أَصْبَحْنَا وَأَصْبَحَ الْمُلْكُ لِلَّهِ، وَالْحَمْدُ لِلَّهِ
• اللَّهُمَّ بِكَ أَصْبَحْنَا وَبِكَ أَمْسَيْنَا وَبِكَ نَحْيَا وَبِكَ نَمُوتُ وَإِلَيْكَ النُّشُورُ <i>(مرة)</i>
• سُبْحَانَ اللهِ وَبِحَمْدِهِ <i>(100 مرة)</i>
• اللَّهُمَّ إِنِّي أَسْأَلُكَ عِلْمًا نَافِعًا وَرِزْقًا طَيِّبًا وَعَمَلًا مُتَقَبَّلًا <i>(مرة)</i>

<i>اللهم اجعل صباحنا صباح خير وبركة وتوفيق</i> 🤲
"""

EVENING_ADHKAR = """\
🌆 <b>أذكار المساء المباركة</b>

• أَعُوذُ بِاللهِ مِنَ الشَّيْطَانِ الرَّجِيمِ
• آية الكرسي <i>(مرة واحدة)</i>
• قُل هُوَ اللهُ أَحَدٌ / الْفَلَقُ / النَّاسُ <i>(3 مرات)</i>
• أَمْسَيْنَا وَأَمْسَى الْمُلْكُ لِلَّهِ، وَالْحَمْدُ لِلَّهِ
• أَعُوذُ بِكَلِمَاتِ اللهِ التَّامَّاتِ مِنْ شَرِّ مَا خَلَقَ <i>(3 مرات)</i>
• اللَّهُمَّ بِكَ أَمْسَيْنَا وَبِكَ أَصْبَحْنَا وَبِكَ نَحْيَا وَبِكَ نَمُوتُ وَإِلَيْكَ الْمَصِيرُ <i>(مرة)</i>
• اللَّهُمَّ إِنِّي أَمْسَيْتُ أُشْهِدُكَ وَأُشْهِدُ حَمَلَةَ عَرْشِكَ وَمَلَائِكَتَكَ أَنَّكَ أَنْتَ اللهُ لَا إِلَهَ إِلَّا أَنْتَ <i>(4 مرات)</i>

<i>اللهم اجعل مساءنا مساء خير وبركة وعافية</i> 🤲
"""

DHIKR_CHALLENGES = [
    ("التَّسبيح",          "سُبْحَانَ اللهِ",                                       "33 مرة"),
    ("الاستغفار",          "أَسْتَغْفِرُ اللهَ وَأَتُوبُ إِلَيْهِ",                 "100 مرة"),
    ("التَّحميد",          "الْحَمْدُ لِلَّهِ",                                     "33 مرة"),
    ("التَّكبير",          "اللهُ أَكْبَرُ",                                         "33 مرة"),
    ("الصلاة على النبي ﷺ", "اللَّهُمَّ صَلِّ وَسَلِّمْ عَلَى نَبِيِّنَا مُحَمَّدٍ", "100 مرة"),
    ("الحوقلة",            "لَا حَوْلَ وَلَا قُوَّةَ إِلَّا بِاللهِ",               "33 مرة"),
    ("الهيللة",            "لَا إِلَهَ إِلَّا اللهُ",                               "100 مرة"),
    ("البسملة",            "بِسْمِ اللهِ الرَّحْمَنِ الرَّحِيمِ",                   "21 مرة"),
    ("الدعاء للنبي ﷺ",    "اللَّهُمَّ صَلِّ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ", "50 مرة"),
    ("الاستغفار المُضاعف", "أَسْتَغْفِرُ اللهَ الْعَظِيمَ الَّذِي لَا إِلَهَ إِلَّا هُوَ الْحَيَّ الْقَيُّومَ وَأَتُوبُ إِلَيْهِ", "10 مرات"),
]

# ══════════════════════════════════════════════════════════════════════════════
# المهام المجدولة
# ══════════════════════════════════════════════════════════════════════════════

PRAYER_PREP_MESSAGES = {
    "Fajr": (
        "🌙 *تنبيه: صلاة الفجر بعد 5 دقائق* ⏳\n\n"
        "_استعدوا للصلاة وتوضؤوا رحمكم الله_ 🤲\n\n"
        "• السواك ✅\n"
        "• الوضوء ✅\n"
        "• الاتجاه للقبلة ✅"
    ),
    "Dhuhr": (
        "☀️ *تنبيه: صلاة الظهر بعد 5 دقائق* ⏳\n\n"
        "_استعدوا للصلاة وتوضؤوا رحمكم الله_ 🤲\n\n"
        "• السواك ✅\n"
        "• الوضوء ✅\n"
        "• الاتجاه للقبلة ✅"
    ),
    "Asr": (
        "🌤️ *تنبيه: صلاة العصر بعد 5 دقائق* ⏳\n\n"
        "_استعدوا للصلاة وتوضؤوا رحمكم الله_ 🤲\n\n"
        "• السواك ✅\n"
        "• الوضوء ✅\n"
        "• الاتجاه للقبلة ✅"
    ),
    "Maghrib": (
        "🌅 *تنبيه: صلاة المغرب بعد 5 دقائق* ⏳\n\n"
        "_استعدوا للصلاة وتوضؤوا رحمكم الله_ 🤲\n\n"
        "• السواك ✅\n"
        "• الوضوء ✅\n"
        "• الاتجاه للقبلة ✅"
    ),
    "Isha": (
        "🌃 *تنبيه: صلاة العشاء بعد 5 دقائق* ⏳\n\n"
        "_استعدوا للصلاة وتوضؤوا رحمكم الله_ 🤲\n\n"
        "• السواك ✅\n"
        "• الوضوء ✅\n"
        "• الاتجاه للقبلة ✅"
    ),
}

PRAYER_PREP_MINUTES = 5  # عدد دقائق التنبيه المسبق



async def job_prayer_prep(bot, prayer: str):
    """
    if not _data_ready:
        return

    يُرسل تنبيه التحضير للصلاة قبل 5 دقائق من وقتها الحقيقي.
    كل مجموعة تتلقى التنبيه في الوقت الصحيح لمدينتها.
    """
    dead = set()
    for chat_id in list(active_groups):
        if chat_id in paused_groups:
            continue  # موقوف
        try:
            city, country = get_group_location(chat_id)
            timings = await get_prayer_times(city, country)
            if not timings:
                continue
            raw = timings.get(prayer, "")
            if not raw:
                continue

            # احسب الوقت الحقيقي للصلاة في هذه المجموعة
            try:
                p_hour, p_min = map(int, raw.split(":")[:2])
            except ValueError:
                continue

            now   = datetime.now(ALGERIA_TZ)
            p_dt  = now.replace(hour=p_hour, minute=p_min, second=0, microsecond=0)
            diff  = (p_dt - now).total_seconds()

            # أرسل فقط في نافذة: بين 4 و 6 دقائق قبل الصلاة (PRAYER_PREP_MINUTES±1)
            prep_min = PRAYER_PREP_MINUTES * 60  # 300 ثانية
            if not (prep_min - 60 <= diff <= prep_min + 60):
                continue
            # deduplication: مرة واحدة فقط لكل صلاة لكل يوم
            today_str = now.strftime("%Y-%m-%d")
            prep_key  = f"{today_str} {p_hour:02d}:{p_min:02d}"
            if _sent_prep.get(chat_id, {}).get(prayer) == prep_key:
                continue
            _sent_prep.setdefault(chat_id, {})[prayer] = prep_key
            text = PRAYER_PREP_MESSAGES.get(
                prayer,
                f"⏳ *تنبيه: الصلاة بعد {PRAYER_PREP_MINUTES} دقائق* 🕌"
            )
            # ── رمضان: تنبيه الإفطار قبل 5 دقائق ───────────────────────────
            ramadan_prep = ""
            if is_ramadan(now) and prayer == "Maghrib":
                ramadan_prep = "\n\n🌙 *استعدوا للإفطار* — بقي 5 دقائق 🤲"
            full = f"{text}\n\n⏰ الوقت: *{raw}* ({city}){ramadan_prep}"
            _t = get_group_thread(chat_id)
            _kw = {"chat_id": chat_id, "text": full, "parse_mode": "Markdown"}
            if _t: _kw["message_thread_id"] = _t
            msg  = await bot.send_message(**_kw)
            add_to_delete_queue(chat_id, msg.message_id)
            await asyncio.sleep(0.05)
            logger.info(f"⏳ prep {prayer} → {chat_id} ({city}) {raw}")

        except TelegramError as e:
            if any(k in str(e).lower() for k in ("kicked", "not found", "blocked", "deactivated")):
                dead.add(chat_id)
    if dead:
        active_groups.difference_update(dead)
        save_data()


async def job_prayer_for_group(bot, chat_id: int, prayer: str):
    """يرسل تذكير الصلاة لمجموعة/قناة واحدة بوقتها المحلي"""
    city, country = get_group_location(chat_id)
    timings = await get_prayer_times(city, country)
    if not timings:
        return
    raw = timings.get(prayer, "")
    if not raw:
        return
    text = PRAYER_MESSAGES.get(prayer, "🕌 *حان وقت الصلاة*")
    full = f"{text}\n\n⏰ الوقت: *{raw}* ({city})"
    try:
        msg = await bot.send_message(chat_id=chat_id, text=full, parse_mode="Markdown")
        add_to_delete_queue(chat_id, msg.message_id)
    except TelegramError as e:
        if any(k in str(e).lower() for k in ("kicked", "not found", "blocked", "deactivated")):
            active_groups.discard(chat_id)
            save_data()



async def _send_to_group(bot, chat_id: int, text: str, parse_mode: str = "Markdown") -> bool:
    """يُرسل رسالة لمجموعة — يراعي الـ topic إذا كان محدداً"""
    try:
        thread_id = get_group_thread(chat_id)
        kwargs = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if thread_id:
            kwargs["message_thread_id"] = thread_id
        msg = await bot.send_message(**kwargs)
        add_to_delete_queue(chat_id, msg.message_id)
        return True
    except TelegramError as e:
        err = str(e).lower()
        if any(k in err for k in ("kicked", "not found", "blocked", "deactivated", "forbidden")):
            return False
        logger.warning(f"⚠️ send error {chat_id}: {e}")
        return False

async def job_prayer(bot, prayer, time_str):
    """
    تذكير الصلاة — يُشغَّل كل دقيقة.
    يرسل لكل مجموعة عندما يحين وقت صلاتها بمدينتها الخاصة.
    نافذة التسامح: ±2 دقيقة من الوقت الفعلي.
    """
    if not _data_ready:
        return
    now  = datetime.now(ALGERIA_TZ)
    dead = set()
    for chat_id in list(active_groups):
        if chat_id in paused_groups:
            continue  # موقوف
        try:
            city, country = get_group_location(chat_id)
            timings = await get_prayer_times(city, country)
            if not timings:
                continue
            raw = timings.get(prayer, "")
            if not raw:
                continue
            try:
                p_hour, p_min = map(int, raw.split(":")[:2])
            except ValueError:
                continue
            p_dt   = now.replace(hour=p_hour, minute=p_min, second=0, microsecond=0)
            diff   = (now - p_dt).total_seconds()
            # أرسل فقط في النافذة: من 0 إلى 90 ثانية بعد وقت الصلاة
            if not (0 <= diff <= 90):
                continue
            # deduplication: لا تُرسل مرتين لنفس الصلاة في نفس اليوم والوقت
            sent_key = f"{now.strftime('%Y-%m-%d')} {p_hour:02d}:{p_min:02d}"
            if _sent_prayer.get(chat_id, {}).get(prayer) == sent_key:
                continue
            _sent_prayer.setdefault(chat_id, {})[prayer] = sent_key
            text = PRAYER_MESSAGES.get(prayer, "🕌 *حان وقت الصلاة*")
            # ── إضافة يوم الجمعة ─────────────────────────────────────────────
            friday_line = "\n\n🌟 *يوم الجمعة المبارك* — أكثروا من الصلاة على النبي ﷺ" if is_friday(now) else ""
            # ── إضافة رمضان عند المغرب ──────────────────────────────────────
            ramadan_line = ""
            if is_ramadan(now) and prayer == "Maghrib":
                ramadan_line = "\n\n🌙 *رمضان كريم* — حان وقت الإفطار، اللهم تقبّل صيامنا 🤲"
            # ── التاريخ الهجري ───────────────────────────────────────────────
            hijri_line = f"\n📅 {get_hijri_str(now)}"
            full = f"{text}\n\n⏰ الوقت: *{raw}* ({city}){hijri_line}{friday_line}{ramadan_line}"
            thread_id = get_group_thread(chat_id)
            send_kwargs = {"chat_id": chat_id, "text": full, "parse_mode": "Markdown"}
            if thread_id:
                send_kwargs["message_thread_id"] = thread_id
            msg  = await bot.send_message(**send_kwargs)
            add_to_delete_queue(chat_id, msg.message_id)
            logger.info(f"🕌 {prayer} → {chat_id} ({city}) {raw}")
            await asyncio.sleep(0.05)
        except TelegramError as e:
            if any(k in str(e).lower() for k in ("kicked", "not found", "blocked", "deactivated")):
                dead.add(chat_id)
    if dead:
        active_groups.difference_update(dead)
        save_data()

async def job_morning_adhkar(bot):
    if not _data_ready:
        return

    await broadcast(bot, MORNING_ADHKAR, parse_mode="HTML")

async def job_evening_adhkar(bot):
    logger.info(f"🌆 job_evening_adhkar بدأ — مجموعات: {len(active_groups)} — موقوفة: {len(paused_groups)}")
    if not _data_ready:
        logger.warning("⚠️ job_evening_adhkar: _data_ready=False — تخطّي")
        return
    try:
        await broadcast(bot, EVENING_ADHKAR, parse_mode="HTML")
        logger.info("✅ job_evening_adhkar اكتمل")
    except Exception as e:
        logger.error(f"❌ job_evening_adhkar خطأ: {e}", exc_info=True)

async def job_random_dhikr(bot):
    logger.info(f"📿 job_random_dhikr بدأ — مجموعات: {len(active_groups)}")
    if not _data_ready:
        logger.warning("⚠️ job_random_dhikr: _data_ready=False")
        return
    dhikr = random.choice(get_all_adhkar())
    await broadcast(bot, f"📿 *ذِكر اليوم*\n\n_{dhikr}_\n\n_اذكروا الله كثيراً لعلكم تفلحون_ 🤲")


async def job_hadith(bot):
    if not _data_ready:
        return
    h    = random.choice(get_all_hadiths())
    text = (
        "📖 *حديث اليوم*\n\n"
        "قال رسول الله ﷺ:\n\n"
        f"❝ _{h['text']}_ ❞\n\n"
        f"📚 _{h['source']}_\n\n"
        "<i>اللهم صلِّ وسلم وبارك على نبينا محمد</i> 🤍"
    )
    await broadcast(bot, text)


async def job_quran_verse(bot):
    if not _data_ready:
        return
    v    = random.choice(get_all_verses())
    text = (
        "🌿 *آية من كتاب الله*\n\n"
        f"﴿ {v['text']} ﴾\n\n"
        f"📖 _{v['surah']}_\n\n"
        "<i>اللهم اجعل القرآن ربيع قلوبنا</i> 🤲"
    )
    await broadcast(bot, text)


async def _send_tasbih_to_one(bot, chat_id: int, valid_members: list, chain_count: int = 0):
    """
    يرسل تحدي التسبيح لشخص واحد.
    بعد حذف الرسالة (DELETE_DELAY) + 5 دقائق إضافية → يرسل للشخص التالي.
    """
    if not valid_members or chain_count >= len(valid_members):
        _active_tasbih_chats.discard(chat_id)  # انتهت السلسلة
        return

    chosen    = pick_member_round_robin(chat_id, valid_members)
    challenge = random.choice(DHIKR_CHALLENGES)
    mention   = get_user_display(chosen)

    remaining = len(valid_members) - chain_count - 1
    footer    = "\n⏳ <i>بعد انتهاء الوقت سيُختار شخص آخر</i> 🔔" if remaining > 0 else ""

    text = (
        f"🎯 <b>تحدي الذِّكر</b> 🎯\n\n"
        f"تم اختيارك {mention} 🌟\n"
        f"اليوم لتقوم بـ <b>{challenge[0]}</b>\n\n"
        f"┌ الذِّكر: <i>{challenge[1]}</i>\n"
        f"└ العدد: <b>{challenge[2]}</b>\n\n"
        f"<i>اللهم تقبل منا ومنكم</i> 🤲"
        f"{footer}"
    )
    try:
        _t = get_group_thread(chat_id)
        _kw = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if _t: _kw["message_thread_id"] = _t
        msg = await bot.send_message(**_kw)
        add_to_delete_queue(chat_id, msg.message_id)

        if remaining > 0:
            # انتظر حتى تُحذف الرسالة + 5 دقائق إضافية
            wait_seconds = DELETE_DELAY + 300  # 30 دقيقة + 5 دقائق
            async def _next():
                await asyncio.sleep(wait_seconds)
                await _send_tasbih_to_one(bot, chat_id, valid_members, chain_count + 1)
            asyncio.create_task(_next())

    except TelegramError as e:
        _active_tasbih_chats.discard(chat_id)  # أزل عند الخطأ
        if any(k in str(e).lower() for k in ("kicked", "not found", "blocked")):
            active_groups.discard(chat_id)
            active_members.pop(chat_id, None)
            save_data()


async def job_tasbih_challenge(bot):
    """يُشغَّل في المواعيد المجدولة — يبدأ سلسلة تحدي التسبيح في كل مجموعة"""
    if not _data_ready:
        return

    dead = set()
    for chat_id in list(active_groups):
        try:
            # تخطي القنوات — تحدي التسبيح للمجموعات فقط
            try:
                chat_info = await bot.get_chat(chat_id)
                if chat_info.type in CHANNEL_TYPES:
                    continue
            except TelegramError:
                pass
            members_raw = list(active_members.get(chat_id, {}).values())
            if not members_raw:
                logger.info(f"⏭️ لا أعضاء بعد في {chat_id} — تحدي التسبيح متوقف مؤقتاً")
                continue

            valid_members = []
            for user_data in members_raw:
                try:
                    uid    = user_data.get("id") if isinstance(user_data, dict) else user_data.id
                    member = await bot.get_chat_member(chat_id, uid)
                    if member.status not in ("left", "kicked"):
                        valid_members.append(user_data)
                except TelegramError:
                    pass

            if not valid_members:
                continue

            # تجنب بدء سلسلة جديدة إذا كانت سلسلة نشطة في هذه المجموعة
            if chat_id in _active_tasbih_chats:
                logger.info(f"⏭️ تحدي التسبيح في {chat_id} نشط بالفعل — تخطّي")
                continue

            # ابدأ السلسلة من أول شخص
            _active_tasbih_chats.add(chat_id)
            await _send_tasbih_to_one(bot, chat_id, valid_members, chain_count=0)

        except TelegramError as e:
            if any(k in str(e).lower() for k in ("kicked", "not found", "blocked")):
                dead.add(chat_id)
    if dead:
        active_groups.difference_update(dead)
        for cid in dead:
            active_members.pop(cid, None)
        save_data()





async def job_city_reminder(bot):
    """
    يُذكّر المجموعات التي لا تزال على المدينة الافتراضية (تيارت).
    يُرسَل كل جمعة — يتوقف تلقائياً بعد MAX_CITY_REMINDERS تنبيه.
    بعد التوقف، يبقى البوت يعمل بتوقيت تيارت الافتراضي.
    """
    global _city_reminder_running
    if not _data_ready:
        logger.warning("⏳ job_city_reminder: البيانات لم تُحمَّل بعد — تخطّي")
        return
    if _city_reminder_running:
        logger.warning("⚠️ job_city_reminder: تشغيل آخر لا يزال جارياً — تخطّي")
        return
    _city_reminder_running = True
    sent_this_run = set()  # منع الإرسال مرتين لنفس المجموعة في نفس الجلسة
    try:
      for chat_id in list(active_groups):
        if chat_id in sent_this_run:
            continue
        city, country = get_group_location(chat_id)
        if city != DEFAULT_CITY or country != DEFAULT_COUNTRY:
            # المجموعة ضبطت مدينتها — أعد العداد وتخطَّ
            city_reminders.pop(chat_id, None)
            continue

        count = city_reminders.get(chat_id, 0)
        if count >= MAX_CITY_REMINDERS:
            # تجاوز الحد — توقف عن التنبيه، استمر بتيارت
            logger.info(f"⏹️ {chat_id} تجاوز {MAX_CITY_REMINDERS} تنبيه — تم إيقاف التذكير")
            continue

        remaining = MAX_CITY_REMINDERS - count
        try:
            msg = await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"📍 <b>تذكير ضبط المدينة</b> ({count + 1}/{MAX_CITY_REMINDERS})\n\n"
                    f"البوت يعمل حالياً بمواقيت <b>تيارت</b> الافتراضية.\n"
                    f"{'⚠️ هذا <b>آخر</b> تذكير — بعده سيبقى البوت على تيارت تلقائياً.' if remaining == 1 else f'يتبقى <b>{remaining - 1}</b> تذكير بعد هذا.'}\n\n"
                    "لضبط مواقيت مدينتكم الدقيقة:\n"
                    "<code>/setcity اسم المدينة</code>\n\n"
                    "أمثلة:\n"
                    "• <code>/setcity Algiers</code> — الجزائر العاصمة\n"
                    "• <code>/setcity Oran</code> — وهران\n"
                    "• <code>/setcity Constantine</code> — قسنطينة\n"
                    "• <code>/setcity Annaba</code> — عنابة\n"
                    "• <code>/setcity Tlemcen</code> — تلمسان\n"
                    "• <code>/setcity Biskra</code> — بسكرة\n"
                    "• <code>/setcity Blida</code> — البليدة\n"
                    "• <code>/setcity Setif</code> — سطيف\n\n"
                    "_أي عضو يمكنه ضبط المدينة_ 🤲"
                ),
                parse_mode="HTML"
            )
            add_to_delete_queue(chat_id, msg.message_id)
            city_reminders[chat_id] = count + 1
            sent_this_run.add(chat_id)
            save_data()
        except TelegramError:
            pass
    finally:
        _city_reminder_running = False
        # حفظ lock في MongoDB حتى لا يُرسَل مرة أخرى هذا الأسبوع
        if db is not None:
            try:
                await db["meta"].replace_one(
                    {"_id": "city_reminder_lock"},
                    {"_id": "city_reminder_lock", "week": week_key,
                     "sent_at": datetime.now(ALGERIA_TZ).isoformat()},
                    upsert=True
                )
                logger.info(f"🔒 city_reminder_lock حُفظ للأسبوع {week_key}")
            except Exception as e:
                logger.error(f"❌ حفظ city_reminder_lock: {e}")



async def job_cleanup_db():
    """
    يُشغَّل كل أحد 03:30 — يُنظّف البيانات المؤقتة فقط.
    ⚠️ لا يمس: active_groups / active_members / group_locations
    يُنظّف فقط:
      • rotation_queue  — طوابير الدوران للمجموعات غير الموجودة
      • city_reminders  — للمجموعات التي ضبطت مدينتها
      • delete_log      — يحتفظ بآخر 30 عملية
      • pending_deletes — يحذف ما فات موعده بأكثر من ساعتين
    """
    logger.info("🧹 بدء التنظيف الدوري (البيانات المؤقتة فقط)...")
    cleaned = {"queues": 0, "reminders": 0, "log": 0, "pending": 0}

    # 1. rotation_queue — فقط للمجموعات غير الموجودة في active_groups
    dead_queues = [cid for cid in list(rotation_queue) if cid not in active_groups]
    for cid in dead_queues:
        rotation_queue.pop(cid, None)
        cleaned["queues"] += 1

    # 2. city_reminders — للمجموعات التي ضبطت مدينتها (لم تعد بحاجة للتذكير)
    for cid in list(city_reminders):
        city, country = get_group_location(cid)
        if city != DEFAULT_CITY or country != DEFAULT_COUNTRY:
            city_reminders.pop(cid, None)
            cleaned["reminders"] += 1

    # 3. delete_log — احتفظ بآخر 30 فقط
    global delete_log
    if len(delete_log) > 30:
        removed = len(delete_log) - 30
        delete_log = delete_log[-30:]
        cleaned["log"] += removed

    # 4. pending_deletes — احذف ما فات موعده بأكثر من ساعتين (لن تُحذف أصلاً)
    now = datetime.now(ALGERIA_TZ).timestamp()
    before = len(pending_deletes)
    pending_deletes[:] = [i for i in pending_deletes if now - i["delete_at"] < 7200]
    cleaned["pending"] += before - len(pending_deletes)

    # حفظ التغييرات (لكن active_groups و active_members محفوظة كما هي)
    save_data()
    save_delete_queue()

    total = sum(cleaned.values())
    logger.info(
        f"🧹 تنظيف مكتمل: "
        f"طوابير={cleaned['queues']} | "
        f"تذكيرات={cleaned['reminders']} | "
        f"سجل={cleaned['log']} | "
        f"حذف منتهي={cleaned['pending']} | "
        f"المجموع={total}"
    )

async def reschedule_prayers(bot, scheduler):
    """
    يُسجّل job_prayer كل دقيقة لكل صلاة.
    كل job يتحقق بنفسه هل حان وقت الصلاة في كل مجموعة حسب مدينتها.
    job_prayer_prep يعمل بنفس الطريقة (كل دقيقة).
    """
    for prayer in PRAYERS_TO_REMIND:
        # ── تذكير حلول الصلاة — كل دقيقة ──
        job_id = f"prayer_{prayer}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        scheduler.add_job(
            job_prayer,
            trigger="cron",
            minute="*",          # كل دقيقة
            timezone=ALGERIA_TZ,
            args=[bot, prayer, ""],
            id=job_id,
        )
        # ── تنبيه التحضير — كل دقيقة ──
        prep_id = f"prayer_prep_{prayer}"
        if scheduler.get_job(prep_id):
            scheduler.remove_job(prep_id)
        scheduler.add_job(
            job_prayer_prep,
            trigger="cron",
            minute="*",          # كل دقيقة
            timezone=ALGERIA_TZ,
            args=[bot, prayer],
            id=prep_id,
        )
        logger.info(f"✅ صلاة {prayer}: مُجدوَلة كل دقيقة (بمدينة كل مجموعة)")

# ══════════════════════════════════════════════════════════════════════════════
# معالجات الأوامر
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_user:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    chat = update.effective_chat
    if chat.type in GROUP_TYPES:
        active_groups.add(chat.id)
        save_data()
        async def _bg_sync():
            result = await sync_group_members(chat.id)
            if result["reason"] == "hidden":
                logger.info(f"🔒 {chat.id} تحجب الأعضاء — سيُجمعون تدريجياً")
        asyncio.create_task(_bg_sync())
        text = (
            "🕌 *بسم الله الرحمن الرحيم*\n\n"
            "السلام عليكم ورحمة الله وبركاته 👋\n\n"
            "أنا *بوتُ الذِّكر والصلاة* لمجموعتكم!\n\n"
            "📋 *ما سأفعله:*\n"
            "• ⏰ تذكيركم بمواقيت الصلاة حسب ولاية تيارت وماجاورها\n"
            "• 🌅 أذكار الصباح والمساء يومياً\n"
            "• 📿 أذكار متنوعة على مدار اليوم\n"
            "• 📖 أحاديث نبوية شريفة يومياً\n"
            "• 🌿 آيات قرآنية كريمة يومياً\n"
            "• 🎯 تحديات التسبيح والاستغفار\n"
            "• 🗑️ حذف الرسائل تلقائياً بعد 30 دقيقة\n\n"
            "/awkat — مواقيت الصلاة اليوم\n"
            "/dhikr — ذكر عشوائي\n"
            "/hadith — حديث نبوي شريف\n"
            "/aya — آية قرآنية كريمة\n"
            "/tasbih — تحدي تسبيح\n\n"
            "📍 *خطوة مهمة:* أخبرني بمدينتكم لأعطيكم مواقيت صلاة دقيقة!\n"
            "`/setcity Oran` — مثال لوهران\n"
            "`/setcity` — لرؤية قائمة المدن\n\n"
            "_بارك الله فيكم_ 🤲"
        )
    elif chat.type in CHANNEL_TYPES:
        active_groups.add(chat.id)
        save_data()
        text = (
            "🕌 *بسم الله الرحمن الرحيم*\n\n"
            "تم تفعيل بوت الذِّكر والصلاة في قناتكم! 🌟\n\n"
            "📋 *ما سيُرسَل تلقائياً:*\n"
            "• ⏰ تذكير الصلوات اليومية\n"
            "• 🌅 أذكار الصباح والمساء\n"
            "• 📿 أذكار متنوعة\n"
            "• 📖 أحاديث نبوية\n"
            "• 🌿 آيات قرآنية\n"
            "• 🗑️ حذف تلقائي بعد 30 دقيقة\n\n"
            "<i>ملاحظة: تحدي التسبيح لا يعمل في القنوات</i>\n"
            "<i>بارك الله فيكم</i> 🤲"
        )
    else:
        # ── رسالة الخاص مع أزرار ──────────────────────────────────────────────
        keyboard = InlineKeyboardMarkup([
            [
                # زر إضافة البوت للمجموعة — يفتح صفحة الاختيار مباشرة
                InlineKeyboardButton(
                    "➕ أضفني إلى مجموعتك",
                    url=f"https://t.me/{bot_username}?startgroup=true"
                ),
            ],
            [
                InlineKeyboardButton("📖 حديث نبوي",    callback_data="private_hadith"),
                InlineKeyboardButton("🌿 آية قرآنية",   callback_data="private_aya"),
            ],
            [
                InlineKeyboardButton("📿 ذِكر",          callback_data="private_dhikr"),
                InlineKeyboardButton("⏰ مواقيت الصلاة", callback_data="private_awkat"),
            ],
            [
                InlineKeyboardButton("📩 إبلاغ عن مشكلة", callback_data="private_report"),
            ],
        ])
        text = (
            "🌙 <b>أهلاً بك في المُذَكِّر الإسلامي</b>\n\n"
            "يمكنني مساعدتك في:\n"
            "• 📖 أحاديث نبوية شريفة\n"
            "• 🌿 آيات قرآنية كريمة\n"
            "• 📿 أذكار متنوعة\n"
            "• ⏰ مواقيت الصلاة\n\n"
            "لإضافتي لمجموعتك: امنحني صلاحية <b>الأدمن</b> وأرسل /start\n\n"
            "اختر ما تريد 👇"
        )
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        return
    msg = await update.effective_message.reply_text(text, parse_mode="HTML")
    if chat.type in ACTIVE_TYPES:
        add_to_delete_queue(chat.id, msg.message_id)


async def cmd_setcity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setcity <المدينة> [البلد]
    مثال: /setcity Oran Algeria
    مثال: /setcity Constantine
    يضبط مدينة المجموعة لجلب مواقيت الصلاة الخاصة بها.
    يعمل لأي عضو في المجموعة (ليس فقط المالك).
    """
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    chat = update.effective_chat

    if not context.args:
        city, country = get_group_location(chat.id)
        await update.effective_message.reply_text(
            f"📍 *المدينة الحالية:* {city} ({country})\n\n"
            "لتغييرها: `/setcity اسم المدينة`\n"
            "أو مع البلد: `/setcity Oran Algeria`\n\n"
            "أمثلة على المدن الجزائرية:\n"
            "`/setcity Algiers` — الجزائر العاصمة\n"
            "`/setcity Oran` — وهران\n"
            "`/setcity Constantine` — قسنطينة\n"
            "`/setcity Annaba` — عنابة\n"
            "`/setcity Batna` — باتنة\n"
            "`/setcity Tlemcen` — تلمسان\n"
            "`/setcity Tiaret` — تيارت\n"
            "`/setcity Blida` — البليدة\n"
            "`/setcity Setif` — سطيف\n"
            "`/setcity Biskra` — بسكرة",
            parse_mode="Markdown"
        )
        return

    new_city    = context.args[0].strip()
    new_country = context.args[1].strip() if len(context.args) > 1 else DEFAULT_COUNTRY

    # أرسل رسالة انتظار
    wait_msg = await update.effective_message.reply_text(
        f"⏳ جارٍ التحقق من *{new_city}*...", parse_mode="Markdown"
    )

    # تحقق من صلاحية المدينة
    valid = await validate_city(new_city, new_country)

    if valid:
        # استخدم الاسم الموحّد من القائمة إن وُجد
        if new_city.lower() in KNOWN_ALGERIAN_CITIES:
            new_city, new_country = KNOWN_ALGERIAN_CITIES[new_city.lower()]
        group_locations[chat.id] = {"city": new_city, "country": new_country}
        city_reminders.pop(chat.id, None)   # أعد العداد عند ضبط المدينة
        active_groups.add(chat.id)
        save_data()
        try:
            await wait_msg.delete()
        except TelegramError:
            pass
        msg = await update.effective_message.reply_text(
            f"✅ *تم ضبط المدينة بنجاح!*\n\n"
            f"📍 {new_city} ({new_country})\n\n"
            "سيُحسب وقت الصلاة لمجموعتكم حسب هذه المدينة\n"
            "اكتب /awkat لرؤية المواقيت الجديدة 🕌",
            parse_mode="Markdown"
        )
        add_to_delete_queue(chat.id, msg.message_id)
        logger.info(f"📍 {chat.title} ({chat.id}) → {new_city}, {new_country}")
    else:
        try:
            await wait_msg.delete()
        except TelegramError:
            pass
        msg = await update.effective_message.reply_text(
            f"❌ *لم يُعثر على المدينة:* `{new_city}`\n\n"
            "تأكد من:\n"
            "• كتابة الاسم بالإنجليزية (مثل: Oran لا وهران)\n"
            "• إضافة اسم البلد: `/setcity Sfax Tunisia`\n\n"
            "أرسل `/setcity` بدون اسم لرؤية أمثلة",
            parse_mode="Markdown"
        )
        add_to_delete_queue(chat.id, msg.message_id)


async def cmd_mycity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    """/mycity — عرض المدينة المضبوطة للمجموعة الحالية"""
    chat          = update.effective_chat
    city, country = get_group_location(chat.id)
    is_default    = city == DEFAULT_CITY and country == DEFAULT_COUNTRY

    timings = await get_prayer_times(city, country)
    fajr    = timings.get("Fajr", "؟") if timings else "؟"
    maghrib = timings.get("Maghrib", "؟") if timings else "؟"

    status = "<i>الافتراضية — لم تُضبط بعد</i>" if is_default else "✅ مضبوطة"
    msg = await update.effective_message.reply_text(
        f"📍 *مدينة هذه المجموعة:*\n\n"
        f"🏙️ المدينة: *{city}*\n"
        f"🌍 البلد: *{country}*\n"
        f"📊 الحالة: {status}\n\n"
        f"🕌 أوقات اليوم:\n"
        f"  • الفجر: `{fajr}`\n"
        f"  • المغرب: `{maghrib}`\n\n"
        "لتغيير المدينة: `/setcity اسم المدينة`",
        parse_mode="Markdown"
    )
    add_to_delete_queue(chat.id, msg.message_id)


async def cmd_awkat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat.id in locked_groups:
        if not await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل في هذه المجموعة — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    chat_id       = update.effective_chat.id
    city, country = get_group_location(chat_id)
    timings       = await get_prayer_times(city, country)
    if timings:
        today = datetime.now(ALGERIA_TZ).strftime("%A %d/%m/%Y")
        lines = [f"🕌 <b>مواقيت الصلاة — {city}</b> 🇩🇿\n📅 <i>{today}</i>\n"]
        for key, arabic in PRAYER_NAMES.items():
            if key in timings:
                lines.append(f"{arabic}: <code>{timings[key]}</code>")
        if city == DEFAULT_CITY:
            lines.append("\n<i>لتغيير المدينة: /setcity اسم المدينة</i>")
        else:
            lines.append(f"\n<i>📍 مضبوطة على: {city} — لتغييرها: /setcity اسم المدينة</i>")
        text = "\n".join(lines)
    else:
        text = (
            f"❌ تعذّر جلب المواقيت لـ <b>{city}</b>\n"
            "تأكد من اسم المدينة أو غيّرها بـ /setcity اسم_المدينة"
        )
    try:
        msg = await update.effective_message.reply_text(text, parse_mode="HTML")
        add_to_delete_queue(update.effective_chat.id, msg.message_id)
    except Exception as e:
        logger.error(f"❌ cmd_awkat خطأ: {e}")
        try:
            msg = await update.effective_message.reply_text(
                f"🕌 مواقيت الصلاة — {city}\n" +
                "\n".join(f"{a}: {timings.get(k,'')}" for k,a in PRAYER_NAMES.items() if timings and k in timings)
                if timings else "❌ تعذّر جلب المواقيت، حاول لاحقاً."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
        except Exception:
            pass


async def cmd_dhikr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat.id in locked_groups:
        if not await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل في هذه المجموعة — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    dhikr = random.choice(get_all_adhkar())
    msg = await update.effective_message.reply_text(f"📿 <b>ذِكر</b>\n\n<i>{dhikr}</i> 🤲", parse_mode="HTML")
    add_to_delete_queue(update.effective_chat.id, msg.message_id)


async def cmd_hadith(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat.id in locked_groups:
        if not await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل في هذه المجموعة — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    h    = random.choice(get_all_hadiths())
    text = (
        "📖 <b>حديث نبوي شريف</b>\n\n"
        "قال رسول الله ﷺ:\n\n"
        f"❝ <i>{h['text']}</i> ❞\n\n"
        f"📚 <i>{h['source']}</i>\n\n"
        "<i>اللهم صلِّ وسلم وبارك على نبينا محمد</i> 🤍"
    )
    msg = await update.effective_message.reply_text(text, parse_mode="HTML")
    add_to_delete_queue(update.effective_chat.id, msg.message_id)


async def cmd_aya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat.id in locked_groups:
        if not await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل في هذه المجموعة — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    v    = random.choice(get_all_verses())
    text = (
        "🌿 <b>آية من كتاب الله</b>\n\n"
        f"﴿ {v['text']} ﴾\n\n"
        f"📖 <i>{v['surah']}</i>\n\n"
        "<i>اللهم اجعل القرآن ربيع قلوبنا</i> 🤲"
    )
    msg = await update.effective_message.reply_text(text, parse_mode="HTML")
    add_to_delete_queue(update.effective_chat.id, msg.message_id)



async def _is_group_admin(bot, chat_id: int, user_id: int) -> bool:
    """يتحقق إذا كان المستخدم أدمن في المجموعة"""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        return False


async def cmd_tasbih(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يُشغّل تحدي التسبيح في المجموعة الحالية فقط — فورياً.
    يُسجّل المُرسِل فوراً، ويحاول جلب الأعضاء من عدة مصادر.
    """
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat.id in locked_groups:
        if not await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل في هذه المجموعة — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in GROUP_TYPES:
        await update.effective_message.reply_text(
            "⚠️ هذا الأمر يعمل في المجموعات فقط."
        )
        return

    chat_id = chat.id
    active_groups.add(chat_id)

    # سجّل المُرسِل فوراً حتى لو لم يرسل من قبل
    if chat_id not in active_members:
        active_members[chat_id] = {}
    if user and not user.is_bot:
        active_members[chat_id][user.id] = _user_to_dict(user)
        save_data()

    members_raw = list(active_members.get(chat_id, {}).values())

    # إذا عضو واحد فقط → حاول جلب الأدمن + Telethon
    if len(members_raw) <= 1:
        wait = await update.effective_message.reply_text("⏳ جارٍ جلب قائمة الأعضاء...")
        try:
            # جلب الأدمن عبر Telegram API (يعمل بدون Telethon)
            admins = await context.bot.get_chat_administrators(chat_id)
            for admin in admins:
                if not admin.user.is_bot:
                    active_members[chat_id][admin.user.id] = _user_to_dict(admin.user)
        except TelegramError:
            pass
        # جلب Telethon إن أمكن
        result = await sync_group_members(chat_id)
        save_data()
        try:
            await wait.delete()
        except TelegramError:
            pass
        members_raw = list(active_members.get(chat_id, {}).values())

    if not members_raw:
        msg = await update.effective_message.reply_text(
            "⚠️ لا يوجد أعضاء مسجّلون.\n"
            "اطلب من الأعضاء إرسال أي رسالة في المجموعة أولاً."
        )
        add_to_delete_queue(chat_id, msg.message_id)
        return

    # تحقق من الأعضاء النشطين
    valid_members = []
    for user_data in members_raw:
        try:
            uid    = user_data.get("id") if isinstance(user_data, dict) else user_data.id
            member = await context.bot.get_chat_member(chat_id, uid)
            if member.status not in ("left", "kicked"):
                valid_members.append(user_data)
        except TelegramError:
            pass

    if not valid_members:
        msg = await update.effective_message.reply_text(
            "⚠️ لا يوجد أعضاء نشطون في هذه المجموعة."
        )
        add_to_delete_queue(chat_id, msg.message_id)
        return

    await _send_tasbih_to_one(context.bot, chat_id, valid_members, chain_count=0)


# ══════════════════════════════════════════════════════════════════════════════
# أوامر المالك
# ══════════════════════════════════════════════════════════════════════════════


async def cmd_lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/lock — يقفل المجموعة: الأوامر الفورية للأدمن فقط."""
    if not update.effective_message:
        return
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in GROUP_TYPES:
        await update.effective_message.reply_text("⚠️ هذا الأمر للمجموعات فقط.")
        return
    is_owner = user.id in ADMIN_IDS
    is_admin = await _is_group_admin(context.bot, chat.id, user.id)
    if not is_owner and not is_admin:
        msg = await update.effective_message.reply_text("⛔ هذا الأمر للأدمن فقط.")
        add_to_delete_queue(chat.id, msg.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
        return
    locked_groups.add(chat.id)
    save_data()
    msg = await update.effective_message.reply_text(
        "🔒 <b>تم قفل البوت</b>\n\n"
        "الأوامر الفورية (<code>/dhikr</code> <code>/hadith</code> "
        "<code>/aya</code> <code>/tasbih</code> <code>/awkat</code>) "
        "أصبحت للأدمن فقط.\n\n"
        "لرفع القفل: <code>/unlock</code>",
        parse_mode="HTML"
    )
    add_to_delete_queue(chat.id, msg.message_id)


async def cmd_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/unlock — يرفع قفل المجموعة: الأوامر للجميع."""
    if not update.effective_message:
        return
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in GROUP_TYPES:
        await update.effective_message.reply_text("⚠️ هذا الأمر للمجموعات فقط.")
        return
    is_owner = user.id in ADMIN_IDS
    is_admin = await _is_group_admin(context.bot, chat.id, user.id)
    if not is_owner and not is_admin:
        msg = await update.effective_message.reply_text("⛔ هذا الأمر للأدمن فقط.")
        add_to_delete_queue(chat.id, msg.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
        return
    locked_groups.discard(chat.id)
    save_data()
    msg = await update.effective_message.reply_text(
        "🔓 <b>تم رفع قفل البوت</b>\n\n"
        "الأوامر الفورية متاحة للجميع الآن ✅",
        parse_mode="HTML"
    )
    add_to_delete_queue(chat.id, msg.message_id)




async def callback_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعالج أزرار inline في الخاص"""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data
    chat_id = query.message.chat_id
    logger.info(f"🔘 callback: {data} من {chat_id}")

    try:
        if data == "private_hadith":
            hadiths = get_all_hadiths()
            h = random.choice(hadiths) if hadiths else {"text": "اطلبوا العلم", "source": "حديث"}
            await query.message.reply_text(
                f"📖 <b>حديث نبوي شريف</b>\n\n"
                f"قال رسول الله ﷺ:\n\n"
                f"❝ <i>{h['text']}</i> ❞\n\n"
                f"📚 <i>{h['source']}</i>\n\n"
                f"<i>اللهم صلِّ وسلم وبارك على نبينا محمد</i> 🤍",
                parse_mode="HTML"
            )

        elif data == "private_aya":
            verses = get_all_verses()
            v = random.choice(verses) if verses else {"text": "إِنَّ مَعَ الْعُسْرِ يُسْرًا", "surah": "الشرح"}
            await query.message.reply_text(
                f"🌿 <b>آية من كتاب الله</b>\n\n"
                f"﴿ {v['text']} ﴾\n\n"
                f"📖 <i>{v['surah']}</i>\n\n"
                f"<i>اللهم اجعل القرآن ربيع قلوبنا</i> 🤲",
                parse_mode="HTML"
            )

        elif data == "private_dhikr":
            adhkar = get_all_adhkar()
            dhikr = random.choice(adhkar) if adhkar else "سبحان الله وبحمده سبحان الله العظيم"
            await query.message.reply_text(
                f"📿 <b>ذِكر</b>\n\n<i>{dhikr}</i> 🤲",
                parse_mode="HTML"
            )

        elif data == "private_awkat":
            await query.message.reply_text("⏳ جارٍ جلب المواقيت...")
            timings = await get_prayer_times(DEFAULT_CITY, DEFAULT_COUNTRY)
            hijri   = get_hijri_str()
            if timings:
                friday_note  = "\n🌟 <b>يوم الجمعة المبارك</b>\n" if is_friday() else ""
                ramadan_note = "\n🌙 <b>رمضان كريم</b> — اللهم تقبّل صيامنا 🤲\n" if is_ramadan() else ""
                await query.message.reply_text(
                    f"🕌 <b>مواقيت الصلاة</b> — {DEFAULT_CITY}\n"
                    f"📅 {hijri}{friday_note}{ramadan_note}\n"
                    f"🌄 الفجر:   <b>{timings.get('Fajr','—')}</b>\n"
                    f"🌅 الشروق: <b>{timings.get('Sunrise','—')}</b>\n"
                    f"☀️ الظهر:   <b>{timings.get('Dhuhr','—')}</b>\n"
                    f"🌤 العصر:   <b>{timings.get('Asr','—')}</b>\n"
                    f"🌆 المغرب: <b>{timings.get('Maghrib','—')}</b>\n"
                    f"🌙 العشاء:  <b>{timings.get('Isha','—')}</b>",
                    parse_mode="HTML"
                )
            else:
                await query.message.reply_text("❌ تعذّر جلب المواقيت — حاول لاحقاً")

        elif data == "private_report":
            await query.message.reply_text(
                "📩 <b>للإبلاغ عن مشكلة أرسل:</b>\n\n"
                "<code>/report وصف المشكلة هنا</code>",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"❌ callback_private [{data}]: {e}", exc_info=True)
        try:
            await query.message.reply_text("❌ حدث خطأ — حاول مرة أخرى")
        except Exception:
            pass




async def cmd_checktopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض الـ topic المحدد حالياً لهذه المجموعة"""
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    chat = update.effective_chat
    thread_id = group_topics.get(chat.id)
    current_msg_thread = update.effective_message.message_thread_id
    await update.effective_message.reply_text(
        f"📌 <b>معلومات الـ Topic</b>\n\n"
        f"🔢 Topic المحدد في البوت: <code>{thread_id if thread_id else 'غير محدد'}</code>\n"
        f"🔢 Topic الرسالة الحالية: <code>{current_msg_thread if current_msg_thread else 'General'}</code>\n\n"
        f"{'✅ البوت سيرسل هنا' if thread_id == current_msg_thread else '⚠️ البوت سيرسل في topic مختلف' if thread_id else '📢 البوت يرسل للعام'}",
        parse_mode="HTML"
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /pause — يوقف البوت مؤقتاً في هذه المجموعة
    لا يُرسل أي رسائل تلقائية حتى /resume
    للمالك وأدمن المجموعة فقط
    """
    if not update.effective_message or not update.effective_user:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in GROUP_TYPES:
        await update.effective_message.reply_text("⚠️ هذا الأمر للمجموعات فقط.")
        return
    is_owner = user.id in ADMIN_IDS
    is_admin = await _is_group_admin(context.bot, chat.id, user.id)
    if not is_owner and not is_admin:
        msg = await update.effective_message.reply_text("⛔ هذا الأمر للأدمن فقط.")
        add_to_delete_queue(chat.id, msg.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
        return
    if chat.id in paused_groups:
        msg = await update.effective_message.reply_text(
            "⚠️ البوت موقوف مسبقاً في هذه المجموعة.\n"
            "لاستئنافه: /resume"
        )
        add_to_delete_queue(chat.id, msg.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
        return
    paused_groups.add(chat.id)
    save_data()
    msg = await update.effective_message.reply_text(
        "⏸ <b>تم إيقاف البوت مؤقتاً</b>\n\n"
        "لن يُرسل البوت أي رسائل تلقائية في هذه المجموعة.\n"
        "لاستئناف العمل: /resume",
        parse_mode="HTML"
    )
    add_to_delete_queue(chat.id, msg.message_id)
    add_to_delete_queue(chat.id, update.effective_message.message_id)
    logger.info(f"⏸ {chat.title} ({chat.id}) → موقوف")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /resume — يستأنف عمل البوت في هذه المجموعة
    للمالك وأدمن المجموعة فقط
    """
    if not update.effective_message or not update.effective_user:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in GROUP_TYPES:
        await update.effective_message.reply_text("⚠️ هذا الأمر للمجموعات فقط.")
        return
    is_owner = user.id in ADMIN_IDS
    is_admin = await _is_group_admin(context.bot, chat.id, user.id)
    if not is_owner and not is_admin:
        msg = await update.effective_message.reply_text("⛔ هذا الأمر للأدمن فقط.")
        add_to_delete_queue(chat.id, msg.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
        return
    if chat.id not in paused_groups:
        msg = await update.effective_message.reply_text(
            "ℹ️ البوت يعمل بشكل طبيعي في هذه المجموعة."
        )
        add_to_delete_queue(chat.id, msg.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
        return
    paused_groups.discard(chat.id)
    save_data()
    msg = await update.effective_message.reply_text(
        "▶️ <b>تم استئناف البوت</b>\n\n"
        "سيعود البوت لإرسال جميع الرسائل التلقائية. 🤲",
        parse_mode="HTML"
    )
    add_to_delete_queue(chat.id, msg.message_id)
    add_to_delete_queue(chat.id, update.effective_message.message_id)
    logger.info(f"▶️ {chat.title} ({chat.id}) → مستأنف")


async def cmd_settopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /settopic — يُضبط الـ topic الحالي كوجهة لرسائل البوت في هذه المجموعة
    استخدم الأمر داخل الـ topic المطلوب مباشرة
    للمالك وأدمن المجموعة فقط
    """
    if not update.effective_message or not update.effective_user:
        return
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in GROUP_TYPES:
        await update.effective_message.reply_text("⚠️ هذا الأمر للمجموعات فقط.")
        return
    # ── فحص القفل ─────────────────────────────────────────────────────────────
    if chat.id in locked_groups:
        is_owner_l = user.id in ADMIN_IDS
        is_admin_l = await _is_group_admin(context.bot, chat.id, user.id)
        if not is_owner_l and not is_admin_l:
            msg = await update.effective_message.reply_text("🔒 البوت مقفل — الأوامر للأدمن فقط.")
            add_to_delete_queue(chat.id, msg.message_id)
            add_to_delete_queue(chat.id, update.effective_message.message_id)
            return

    is_owner = user.id in ADMIN_IDS
    is_admin = await _is_group_admin(context.bot, chat.id, user.id)
    if not is_owner and not is_admin:
        msg = await update.effective_message.reply_text("⛔ هذا الأمر للأدمن فقط.")
        add_to_delete_queue(chat.id, msg.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
        return

    thread_id = update.effective_message.message_thread_id
    logger.info(f"📌 /settopic → chat={chat.id} thread_id={thread_id} is_forum={getattr(chat, "is_forum", None)}")

    if not thread_id:
        msg = await update.effective_message.reply_text(
            "⚠️ <b>لم يُكتشف الـ Topic</b>\n\n"
            "تأكد من:\n"
            "• أن المجموعة مفعّل فيها خاصية <b>Topics</b>\n"
            "• أنك أرسلت الأمر <b>داخل الـ topic</b> المطلوب وليس في العام\n"
            "• أن البوت أدمن في المجموعة",
            parse_mode="HTML"
        )
        add_to_delete_queue(chat.id, msg.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
        return

    group_topics[chat.id] = thread_id
    save_data()
    logger.info(f"✅ group_topics[{chat.id}] = {thread_id} | كل topics: {group_topics}")

    msg = await update.effective_message.reply_text(
        f"✅ <b>تم تحديد الـ Topic</b>\n\n"
        f"سيُرسل البوت كل رسائله في هذا الـ topic فقط.\n"
        f"🔢 Thread ID: <code>{thread_id}</code>\n\n"
        f"لإلغاء التحديد: /cleartopic",
        parse_mode="HTML"
    )
    add_to_delete_queue(chat.id, msg.message_id)
    add_to_delete_queue(chat.id, update.effective_message.message_id)
    logger.info(f"📌 {chat.title} ({chat.id}) → topic {thread_id}")


async def cmd_cleartopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cleartopic — يُلغي تحديد الـ topic ويُرسل البوت للمجموعة العامة
    للمالك وأدمن المجموعة فقط
    """
    if not update.effective_message or not update.effective_user:
        return
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in GROUP_TYPES:
        await update.effective_message.reply_text("⚠️ هذا الأمر للمجموعات فقط.")
        return
    # ── فحص القفل ─────────────────────────────────────────────────────────────
    if chat.id in locked_groups:
        is_owner_l = user.id in ADMIN_IDS
        is_admin_l = await _is_group_admin(context.bot, chat.id, user.id)
        if not is_owner_l and not is_admin_l:
            msg = await update.effective_message.reply_text("🔒 البوت مقفل — الأوامر للأدمن فقط.")
            add_to_delete_queue(chat.id, msg.message_id)
            add_to_delete_queue(chat.id, update.effective_message.message_id)
            return

    is_owner = user.id in ADMIN_IDS
    is_admin = await _is_group_admin(context.bot, chat.id, user.id)
    if not is_owner and not is_admin:
        msg = await update.effective_message.reply_text("⛔ هذا الأمر للأدمن فقط.")
        add_to_delete_queue(chat.id, msg.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
        return

    if chat.id in group_topics:
        del group_topics[chat.id]
        save_data()
        msg = await update.effective_message.reply_text(
            "✅ تم إلغاء تحديد الـ Topic — سيُرسل البوت للمجموعة العامة."
        )
    else:
        msg = await update.effective_message.reply_text(
            "ℹ️ لم يُحدَّد topic لهذه المجموعة أصلاً."
        )
    add_to_delete_queue(chat.id, msg.message_id)
    add_to_delete_queue(chat.id, update.effective_message.message_id)



# ══════════════════════════════════════════════════════════════════════════════
# نظام التذكيرات المخصصة
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# نظام التذكيرات — محفوظة في MongoDB + تُحذف بعد الإرسال
# ══════════════════════════════════════════════════════════════════════════════

async def _save_reminder(reminder: dict):
    """يحفظ تذكير في MongoDB"""
    db = _get_db()
    if db is not None:
        try:
            await db["reminders"].insert_one(reminder)
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ التذكير: {e}")

async def _delete_reminder(reminder_id: str):
    """يحذف التذكير من MongoDB بعد إرساله"""
    db = _get_db()
    if db is not None:
        try:
            from bson import ObjectId
            await db["reminders"].delete_one({"_id": ObjectId(reminder_id)})
            logger.info(f"🗑️ تذكير {reminder_id} حُذف من MongoDB")
        except Exception as e:
            logger.error(f"❌ خطأ في حذف التذكير: {e}")

async def _load_and_schedule_reminders(bot):
    """يُحمَّل عند startup — يُجدول التذكيرات المعلقة من MongoDB"""
    db = _get_db()
    if db is None:
        return
    try:
        now_ts = datetime.now(ALGERIA_TZ).timestamp()
        cursor = db["reminders"].find({})
        loaded = 0
        async for r in cursor:
            fire_at  = r.get("fire_at", 0)
            delay    = max(0, fire_at - now_ts)
            asyncio.create_task(
                _execute_reminder(bot, r, delay, str(r["_id"]))
            )
            loaded += 1
        if loaded:
            logger.info(f"📅 {loaded} تذكير معلق أُعيد جدولته من MongoDB")
    except Exception as e:
        logger.error(f"❌ خطأ في تحميل التذكيرات: {e}")

async def _execute_reminder(bot, r: dict, delay: float, reminder_id: str):
    """ينتظر ثم يُرسل التذكير ويحذفه من MongoDB"""
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        chat_id   = r["chat_id"]
        text      = r["text"]
        thread_id = r.get("thread_id")
        is_group  = r.get("is_group", True)
        user_name = r.get("user_name", "")

        kw = {"chat_id": chat_id, "parse_mode": "HTML"}
        if thread_id:
            kw["message_thread_id"] = thread_id

        if is_group:
            reminder_text = (
                f"🔔 <b>تذكير للمجموعة</b>\n\n"
                f"📝 {text}\n\n"
                f"<i>من: {user_name} | المُذَكِّر الإسلامي 🌙</i>"
            )
        else:
            reminder_text = (
                f"🔔 <b>تذكيرك الشخصي!</b>\n\n"
                f"📝 {text}\n\n"
                f"<i>المُذَكِّر الإسلامي 🌙</i>"
            )

        msg = await bot.send_message(text=reminder_text, **kw)
        if is_group and chat_id in active_groups:
            add_to_delete_queue(chat_id, msg.message_id)

    except Exception as e:
        logger.error(f"❌ خطأ في إرسال التذكير {reminder_id}: {e}")
    finally:
        # احذف دائماً بعد الإرسال أو الخطأ
        await _delete_reminder(reminder_id)


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /remind <وقت> <نص>
    تذكير للمجموعة كلها — للأدمن والمالك فقط
    """
    if not update.effective_message or not update.effective_user:
        return
    chat = update.effective_chat
    user = update.effective_user

    is_owner = user.id in ADMIN_IDS
    is_admin = await _is_group_admin(context.bot, chat.id, user.id) if chat.type in GROUP_TYPES else False

    if not is_owner and not is_admin:
        msg = await update.effective_message.reply_text("⛔ هذا الأمر للأدمن والمالك فقط.")
        if chat.type in GROUP_TYPES:
            add_to_delete_queue(chat.id, msg.message_id)
            add_to_delete_queue(chat.id, update.effective_message.message_id)
        return

    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text(
            "📅 <b>أمر التذكير</b>\n\n"
            "الصيغة: <code>/remind &lt;وقت&gt; &lt;نص&gt;</code>\n\n"
            "<b>أمثلة:</b>\n"
            "• <code>/remind 30m صلاة العصر</code>\n"
            "• <code>/remind 2h اجتماع المجموعة</code>\n"
            "• <code>/remind 1d موعد غداً</code>\n\n"
            "<b>وحدات:</b> <code>m</code>=دقائق | <code>h</code>=ساعات | <code>d</code>=أيام",
            parse_mode="HTML"
        )
        return

    import re as _re
    match = _re.match(r"(\d+)(m|h|d)", context.args[0].lower())
    if not match:
        await update.effective_message.reply_text(
            "❌ صيغة خاطئة — مثال: <code>30m</code> أو <code>2h</code> أو <code>1d</code>",
            parse_mode="HTML"
        )
        return

    amount  = int(match.group(1))
    unit    = match.group(2)
    seconds = amount * (60 if unit=="m" else 3600 if unit=="h" else 86400)
    text    = " ".join(context.args[1:])

    if seconds > 30 * 86400:
        await update.effective_message.reply_text("❌ الحد الأقصى 30 يوم.")
        return

    unit_ar  = "دقيقة" if unit=="m" else "ساعة" if unit=="h" else "يوم"
    fire_at  = datetime.now(ALGERIA_TZ).timestamp() + seconds
    # التنبيهات دائماً في الـ General (topic_id=None)
    # thread_id= None دائماً للتنبيهات

    # حفظ في MongoDB
    reminder = {
        "chat_id":   chat.id,
        "text":      text,
        "fire_at":   fire_at,
        "thread_id": None,  # الـ General دائماً
        "is_group":  True,
        "user_name": user.full_name,
        "created_at": datetime.now(ALGERIA_TZ).isoformat(),
    }
    await _save_reminder(reminder)

    # جدولة الإرسال
    reminder_id = str(reminder.get("_id", "temp"))
    asyncio.create_task(_execute_reminder(context.bot, reminder, seconds, reminder_id))

    confirm = await update.effective_message.reply_text(
        f"✅ <b>تم ضبط التذكير</b>\n\n"
        f"⏰ بعد <b>{amount} {unit_ar}</b>\n"
        f"📝 {text}",
        parse_mode="HTML"
    )
    if chat.type in GROUP_TYPES:
        add_to_delete_queue(chat.id, confirm.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
    logger.info(f"📅 /remind: {chat.id} — {amount}{unit} — {text[:30]}")


async def cmd_remindme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /remindme <وقت> <نص>
    تذكير شخصي في الخاص — للأدمن والمالك فقط في المجموعات
    """
    if not update.effective_message or not update.effective_user:
        return
    user = update.effective_user
    chat = update.effective_chat

    is_owner = user.id in ADMIN_IDS
    if chat.type in GROUP_TYPES:
        is_admin = await _is_group_admin(context.bot, chat.id, user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text("⛔ هذا الأمر للأدمن والمالك فقط.")
            add_to_delete_queue(chat.id, msg.message_id)
            add_to_delete_queue(chat.id, update.effective_message.message_id)
            return

    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text(
            "📅 <b>تذكير شخصي</b>\n\n"
            "الصيغة: <code>/remindme &lt;وقت&gt; &lt;نص&gt;</code>\n\n"
            "سأرسل لك في الخاص 🤫",
            parse_mode="HTML"
        )
        return

    import re as _re
    match = _re.match(r"(\d+)(m|h|d)", context.args[0].lower())
    if not match:
        await update.effective_message.reply_text(
            "❌ صيغة خاطئة — مثال: <code>30m</code> أو <code>2h</code>",
            parse_mode="HTML"
        )
        return

    amount  = int(match.group(1))
    unit    = match.group(2)
    seconds = amount * (60 if unit=="m" else 3600 if unit=="h" else 86400)
    text    = " ".join(context.args[1:])

    if seconds > 30 * 86400:
        await update.effective_message.reply_text("❌ الحد الأقصى 30 يوم.")
        return

    unit_ar = "دقيقة" if unit=="m" else "ساعة" if unit=="h" else "يوم"
    fire_at = datetime.now(ALGERIA_TZ).timestamp() + seconds

    # حفظ في MongoDB
    reminder = {
        "chat_id":    user.id,
        "text":       text,
        "fire_at":    fire_at,
        "thread_id":  None,
        "is_group":   False,
        "user_name":  user.full_name,
        "created_at": datetime.now(ALGERIA_TZ).isoformat(),
    }
    await _save_reminder(reminder)

    reminder_id = str(reminder.get("_id", "temp"))
    asyncio.create_task(_execute_reminder(context.bot, reminder, seconds, reminder_id))

    confirm = await update.effective_message.reply_text(
        f"✅ <b>تذكير شخصي مضبوط</b>\n\n"
        f"⏰ بعد <b>{amount} {unit_ar}</b>\n"
        f"📝 {text}\n\n"
        f"<i>سأرسل لك في الخاص 🤫</i>",
        parse_mode="HTML"
    )
    if chat.type in GROUP_TYPES:
        add_to_delete_queue(chat.id, confirm.message_id)
        add_to_delete_queue(chat.id, update.effective_message.message_id)
    logger.info(f"📅 /remindme: {user.id} — {amount}{unit} — {text[:30]}")



async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """  # locked_groups: أمر المالك — لا يتأثر بالقفل
    /broadcast <نص> — نص فقط
    أو أرسل صورة/فيديو والتعليق /broadcast
    للمالك فقط.
    """
    if not update.effective_message or not update.effective_user:
        return
    if update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text("⛔ هذا الأمر للمالك فقط.")
        return

    msg      = update.effective_message
    has_photo  = bool(msg.photo)
    has_video  = bool(msg.video)
    has_doc    = bool(msg.document)
    caption    = msg.caption or (" ".join(context.args) if context.args else "")

    # رسالة مساعدة إذا لا يوجد محتوى
    if not context.args and not has_photo and not has_video and not has_doc:
        await msg.reply_text(
            "📢 <b>استخدام أمر البث:</b>\n\n"
            "📝 <b>نص:</b> <code>/broadcast رسالتك</code>\n"
            "🖼 <b>صورة:</b> أرسل صورة والتعليق <code>/broadcast وصف</code>\n"
            "🎥 <b>فيديو:</b> أرسل فيديو والتعليق <code>/broadcast وصف</code>\n"
            "📄 <b>ملف:</b> أرسل ملف والتعليق <code>/broadcast وصف</code>",
            parse_mode="HTML"
        )
        return

    sent = 0
    failed = 0
    dead = set()

    status_msg = await msg.reply_text(
        f"⏳ جارٍ البث لـ {len(active_groups)} مجموعة/قناة..."
    )

    # تحضير التعليق
    full_caption = (
        f"📢 <b>رسالة من مشرف البوت</b>\n\n"
        f"{caption}\n\n"
        f"<i>المُذَكِّر الإسلامي 🌙</i>"
    ) if caption else "📢 <b>المُذَكِّر الإسلامي</b> 🌙"

    for chat_id in list(active_groups):
        try:
            thread_id = get_group_thread(chat_id)
            kw = {"chat_id": chat_id}
            if thread_id:
                kw["message_thread_id"] = thread_id

            if has_photo:
                photo_id = msg.photo[-1].file_id
                await context.bot.send_photo(
                    **kw, photo=photo_id,
                    caption=full_caption, parse_mode="HTML"
                )
            elif has_video:
                await context.bot.send_video(
                    **kw, video=msg.video.file_id,
                    caption=full_caption, parse_mode="HTML"
                )
            elif has_doc:
                await context.bot.send_document(
                    **kw, document=msg.document.file_id,
                    caption=full_caption, parse_mode="HTML"
                )
            else:
                full_msg = (
                    f"📢 <b>رسالة من مشرف البوت</b>\n\n"
                    f"{' '.join(context.args)}\n\n"
                    f"<i>المُذَكِّر الإسلامي 🌙</i>"
                )
                await context.bot.send_message(
                    **kw, text=full_msg, parse_mode="HTML"
                )

            sent += 1
            await asyncio.sleep(0.05)

        except TelegramError as e:
            err = str(e).lower()
            if any(k in err for k in ("kicked", "not found", "blocked", "deactivated", "forbidden")):
                dead.add(chat_id)
            failed += 1

    if dead:
        active_groups.difference_update(dead)
        save_data()

    await status_msg.edit_text(
        f"✅ <b>اكتمل البث</b>\n\n"
        f"📤 أُرسلت: {sent}\n"
        f"❌ فشل: {failed}\n"
        f"🗑️ مجموعات أُزيلت: {len(dead)}",
        parse_mode="HTML"
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """  # locked_groups: أمر المالك — لا يتأثر بالقفل
    /report <وصف المشكلة>
    يُرسل رسالة مباشرة للمالك عند وجود مشكلة.
    يعمل في الخاص فقط.
    """
    if not update.effective_message or not update.effective_user:
        return
    user = update.effective_user
    if not context.args:
        await update.effective_message.reply_text(
            "📩 <b>للإبلاغ عن مشكلة:</b>\n\n"
            "<code>/report وصف المشكلة هنا</code>\n\n"
            "مثال:\n"
            "<code>/report البوت لا يرسل مواقيت الصلاة</code>",
            parse_mode="HTML"
        )
        return
    problem = " ".join(context.args)
    # أرسل للمالك
    owner_id = 7176475438
    report_text = (
        f"📩 <b>بلاغ جديد</b>\n\n"
        f"👤 المُرسِل: {user.full_name}"
        f"{(' (@' + user.username + ')') if user.username else ''}\n"
        f"🆔 المعرّف: <code>{user.id}</code>\n\n"
        f"📝 <b>المشكلة:</b>\n{problem}"
    )
    try:
        await context.bot.send_message(chat_id=owner_id, text=report_text, parse_mode="HTML")
        await update.effective_message.reply_text(
            "✅ <b>تم إرسال بلاغك للمطوّر</b>\n\n"
            "سيتم الرد عليك قريباً إن شاء الله 🤲",
            parse_mode="HTML"
        )
    except TelegramError:
        await update.effective_message.reply_text(
            "❌ تعذّر إرسال البلاغ — يمكنك التواصل مباشرة عبر @kadersasse"
        )


def _is_owner(update: Update) -> bool:
    uid = update.effective_user.id
    if not ADMIN_IDS:
        logger.warning(
            f"⚠️ ADMIN_IDS فارغ! معرّف المرسل: {uid} — "
            f"تأكد من ضبط متغير البيئة ADMIN_IDS في Render"
        )
    result = uid in ADMIN_IDS
    if not result:
        logger.warning(
            f"⛔ رفض أمر المالك — معرّف المرسل: {uid} | ADMIN_IDS الحالية: {ADMIN_IDS}"
        )
    return result


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    """
    /myid — يعرض معرّف المستخدم الحالي وحالة صلاحياته
    مفيد للتشخيص والتأكد من ضبط ADMIN_IDS بشكل صحيح
    """
    uid = update.effective_user.id
    is_admin = uid in ADMIN_IDS
    status   = "✅ أنت مالك البوت" if is_admin else "❌ لست في قائمة المالكين"
    await update.effective_message.reply_text(
        f"🆔 معرّفك: `{uid}`\n"
        f"👑 الحالة: {status}\n"
        f"📋 ADMIN_IDS المسجّلة: `{ADMIN_IDS if ADMIN_IDS else 'فارغة ⚠️'}`\n\n"
        f"_إذا كانت ADMIN_IDS فارغة، أضف معرّفك في متغيرات البيئة على Render_",
        parse_mode="Markdown"
    )


# ══════════════════════════════════════════════════════════════════════════════
# أوامر التحكم الديناميكي (للمالك فقط)
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    """
    /schedule — عرض كل مواعيد الإرسال الحالية
    """
    if not _is_owner(update):
        await update.effective_message.reply_text("⛔ هذا الأمر للمالك فقط.")
        return

    sched = bot_config.get("schedule", DEFAULT_SCHEDULE)
    lines = ["⏰ *مواعيد الإرسال الحالية:*\n"]
    for key, label in SCHEDULE_LABELS.items():
        t = sched.get(key, DEFAULT_SCHEDULE[key])
        lines.append(f"• `{key}` — {label}\n  🕐 {t['hour']:02d}:{t['minute']:02d}")

    lines.append(
        "\n📝 *لتغيير موعد:*\n"
        "`/settime <المفتاح> <HH:MM>`\n"
        "مثال: `/settime morning_adhkar 08:30`"
    )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    """
    /settime <job_key> <HH:MM>
    مثال: /settime morning_adhkar 08:30
    يُغيّر موعد الإرسال فوراً بدون إعادة تشغيل
    """
    if not _is_owner(update):
        await update.effective_message.reply_text("⛔ هذا الأمر للمالك فقط.")
        return

    if len(context.args) < 2:
        keys_list = "\n".join(f"  • `{k}` — {v}" for k, v in SCHEDULE_LABELS.items())
        await update.effective_message.reply_text(
            "الاستخدام: `/settime <المفتاح> <HH:MM>`\n"
            "المفاتيح المتاحة:\n" + keys_list,
            parse_mode="Markdown"
        )
        return

    job_key  = context.args[0].strip()
    time_str = context.args[1].strip()

    if job_key not in DEFAULT_SCHEDULE:
        await update.effective_message.reply_text(
            f"❌ المفتاح `{job_key}` غير موجود."
            "أرسل /schedule لرؤية المفاتيح الصحيحة.",
            parse_mode="Markdown"
        )
        return

    try:
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ صيغة الوقت خاطئة، استخدم HH:MM مثل `08:30`", parse_mode="Markdown")
        return

    # حفظ الإعداد الجديد
    bot_config["schedule"][job_key] = {"hour": hour, "minute": minute}
    save_config()

    # تطبيق فوري على المجدول
    ok = reschedule_job(_scheduler, job_key, context.bot)

    label = SCHEDULE_LABELS.get(job_key, job_key)
    if ok:
        await update.effective_message.reply_text(
            f"✅ تم التغيير فوراً!"
            f"📌 {label}"
            f"🕐 الموعد الجديد: *{hour:02d}:{minute:02d}*",
            parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            f"⚠️ حُفظ الإعداد لكن لم تُطبَّق الجدولة."
            f"ستُطبَّق في إعادة التشغيل القادمة."
        )


async def cmd_addcontent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    if not _is_owner(update):
        await update.effective_message.reply_text("⛔ هذا الأمر للمالك فقط.")
        return
    if len(context.args) < 2:
        help_msg = (
            "📝 *كيفية إضافة محتوى جديد:*\n\n"
            "• ذكر: `/addcontent dhikr النص`\n"
            "• حديث: `/addcontent hadith النص|المصدر`\n"
            "• آية: `/addcontent aya النص|اسم السورة`\n\n"
            "مثال:\n"
            "`/addcontent dhikr سبحان الله وبحمده سبحان الله العظيم`\n"
            "`/addcontent hadith خيركم من تعلم القرآن وعلمه|رواه البخاري`\n"
            "`/addcontent aya ربنا آتنا في الدنيا حسنة|سورة البقرة`"
        )
        await update.effective_message.reply_text(help_msg, parse_mode="Markdown")
        return
    content_type = context.args[0].strip().lower()
    raw = " ".join(context.args[1:]).strip()
    if content_type == "dhikr":
        if not raw:
            await update.effective_message.reply_text("❌ أدخل نص الذكر.")
            return
        bot_config["extra_adhkar"].append(raw)
        save_config()
        count = len(bot_config["extra_adhkar"])
        await update.effective_message.reply_text(
            f"✅ أُضيف الذكر بنجاح!\n📿 _{raw}_\n\nإجمالي الأذكار المُضافة: {count}",
            parse_mode="Markdown"
        )
    elif content_type == "hadith":
        parts  = raw.split("|", 1)
        text   = parts[0].strip()
        source = parts[1].strip() if len(parts) > 1 else "مُضاف يدوياً"
        if not text:
            await update.effective_message.reply_text("❌ أدخل نص الحديث.")
            return
        bot_config["extra_hadiths"].append(f"{text}|{source}")
        save_config()
        count = len(bot_config["extra_hadiths"])
        await update.effective_message.reply_text(
            f"✅ أُضيف الحديث بنجاح!\n📖 _{text}_\n📚 {source}\n\nإجمالي الأحاديث المُضافة: {count}",
            parse_mode="Markdown"
        )
    elif content_type == "aya":
        parts = raw.split("|", 1)
        text  = parts[0].strip()
        surah = parts[1].strip() if len(parts) > 1 else "مُضاف يدوياً"
        if not text:
            await update.effective_message.reply_text("❌ أدخل نص الآية.")
            return
        bot_config["extra_verses"].append(f"{text}|{surah}")
        save_config()
        count = len(bot_config["extra_verses"])
        await update.effective_message.reply_text(
            f"✅ أُضيفت الآية بنجاح!\n🌿 ﴿ {text} ﴾\n📖 {surah}\n\nإجمالي الآيات المُضافة: {count}",
            parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            "❌ النوع غير صحيح.\nاستخدم: `dhikr` أو `hadith` أو `aya`",
            parse_mode="Markdown"
        )


async def cmd_listcontent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    if not _is_owner(update):
        await update.effective_message.reply_text("⛔ هذا الأمر للمالك فقط.")
        return
    adhkar  = bot_config.get("extra_adhkar",  [])
    hadiths = bot_config.get("extra_hadiths", [])
    verses  = bot_config.get("extra_verses",  [])
    if not adhkar and not hadiths and not verses:
        await update.effective_message.reply_text("📭 لا يوجد محتوى مُضاف بعد.\nاستخدم /addcontent للإضافة.")
        return
    parts = ["📋 *المحتوى المُضاف يدوياً:*\n"]
    if adhkar:
        parts.append(f"📿 *أذكار ({len(adhkar)}):*")
        for i, d in enumerate(adhkar, 1):
            parts.append(f"  {i}. {d}")
    if hadiths:
        parts.append(f"\n📖 *أحاديث ({len(hadiths)}):*")
        for i, h in enumerate(hadiths, 1):
            p = h.split("|", 1)
            parts.append(f"  {i}. {p[0]}")
            if len(p) > 1:
                parts.append(f"     _{p[1]}_")
    if verses:
        parts.append(f"\n🌿 *آيات ({len(verses)}):*")
        for i, v in enumerate(verses, 1):
            p = v.split("|", 1)
            parts.append(f"  {i}. ﴿{p[0]}﴾")
            if len(p) > 1:
                parts.append(f"     _{p[1]}_")
    parts.append("\n🗑️ للحذف: `/delcontent <النوع> <الرقم>`\nمثال: `/delcontent dhikr 1`")
    full_text = "\n".join(parts)
    if len(full_text) > 4000:
        full_text = full_text[:4000] + "\n...(مقطوع)"
    await update.effective_message.reply_text(full_text, parse_mode="Markdown")


async def cmd_delcontent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    if not _is_owner(update):
        await update.effective_message.reply_text("⛔ هذا الأمر للمالك فقط.")
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "الاستخدام: `/delcontent <النوع> <الرقم>`\nمثال: `/delcontent dhikr 1`\nالنوع: `dhikr` | `hadith` | `aya`",
            parse_mode="Markdown"
        )
        return
    content_type = context.args[0].strip().lower()
    try:
        index = int(context.args[1].strip()) - 1
    except ValueError:
        await update.effective_message.reply_text("❌ الرقم غير صحيح.")
        return
    key_map = {"dhikr": "extra_adhkar", "hadith": "extra_hadiths", "aya": "extra_verses"}
    if content_type not in key_map:
        await update.effective_message.reply_text("❌ النوع غير صحيح: `dhikr` | `hadith` | `aya`", parse_mode="Markdown")
        return
    lst = bot_config.get(key_map[content_type], [])
    if index < 0 or index >= len(lst):
        await update.effective_message.reply_text(f"❌ الرقم خارج النطاق. المتاح: 1 إلى {len(lst)}")
        return
    removed = lst.pop(index)
    save_config()
    preview = removed.split("|")[0][:60]
    await update.effective_message.reply_text(
        f"✅ تم الحذف بنجاح!\n🗑️ المحذوف: _{preview}_",
        parse_mode="Markdown"
    )


async def cmd_checkdeletes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    if not _is_owner(update):
        await update.effective_message.reply_text("⛔ هذا الأمر للمالك فقط.")
        return
    now      = datetime.now(ALGERIA_TZ).timestamp()
    overdue  = [i for i in pending_deletes if now > i["delete_at"]]
    upcoming = [i for i in pending_deletes if now <= i["delete_at"]]
    lines = ["🗑️ *حالة قائمة الحذف:*\n"]
    lines.append(f"• ⏳ معلقة (لم يحن وقتها): *{len(upcoming)}*")
    lines.append(f"• ⚠️ متأخرة (فات وقتها): *{len(overdue)}*")
    lines.append(f"• 📋 إجمالي: *{len(pending_deletes)}*\n")
    if delete_log:
        lines.append("📜 *آخر عمليات الحذف:*")
        for entry in reversed(delete_log[-10:]):
            lines.append(
                f"  {entry['status']} | رسالة `{entry['msg_id']}` "
                f"في `{entry['chat_id']}` — {entry['at']}"
            )
    else:
        lines.append("<i>لا توجد سجلات بعد</i>")
    lines.append("\n💡 إذا كانت هناك رسائل متأخرة كثيرة، تأكد أن البوت أدمن وله صلاحية حذف الرسائل.")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    """/sync — يزامن أعضاء كل المجموعات يدوياً عبر Telethon (للمالك فقط)"""
    if not _is_owner(update):
        await update.effective_message.reply_text("⛔ هذا الأمر للمالك فقط.")
        return
    if not active_groups:
        await update.effective_message.reply_text("📭 لا توجد مجموعات نشطة.")
        return
    await update.effective_message.reply_text(f"⏳ جارٍ مزامنة {len(active_groups)} مجموعة...")
    results = await sync_all_groups()
    lines   = [f"✅ *نتائج المزامنة ({len(results)} مجموعة):*\n"]
    for chat_id, res in results.items():
        try:
            chat  = await context.bot.get_chat(chat_id)
            title = chat.title
        except TelegramError:
            title = str(chat_id)
        count  = res["count"]
        reason = res["reason"]
        if count > 0:
            status = f"{count} عضو ✅"
        elif reason == "hidden":
            status = "🔒 الأعضاء محجوبون"
        elif reason == "no_client":
            status = "⚠️ Telethon غير مفعّل"
        else:
            status = f"❌ خطأ"
        lines.append(f"• {title}: {status}")
    total = sum(r["count"] for r in results.values())
    lines.append(f"\n📊 الإجمالي: {total} عضو")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    """
    /addgroup -100xxxxxxxxxx
    يضيف مجموعة أو قناة يدوياً للقائمة النشطة.
    يعمل حتى لو لم يكن البوت أدميناً بعد.
    """
    if not _is_owner(update):
        await update.effective_message.reply_text("⛔ هذا الأمر للمالك فقط.")
        return

    # دعم الإدخال بطريقتين: context.args أو تقسيم النص يدوياً
    chat_id_str = ""
    if context.args:
        chat_id_str = context.args[0]
    else:
        parts = (update.effective_message.text or "").split()
        if len(parts) >= 2:
            chat_id_str = parts[1]

    if not chat_id_str:
        await update.effective_message.reply_text(
            "الاستخدام: /addgroup -100xxxxxxxxxx\n"
            "احصل على معرّف المجموعة بإضافة @userinfobot إليها."
        )
        return

    try:
        chat_id = int(chat_id_str)
    except ValueError:
        await update.effective_message.reply_text("❌ معرّف غير صالح، تأكد أنه رقم مثل: -1002992929079")
        return

    # ✅ أضف المجموعة فوراً بدون أي تحقق قد يفشل
    active_groups.add(chat_id)
    save_data()

    # حاول جلب معلومات المجموعة كخطوة اختيارية
    title        = f"#{chat_id}"
    member_count = "؟"
    try:
        chat         = await context.bot.get_chat(chat_id)
        title        = chat.title or title
        try:
            member_count = await context.bot.get_chat_member_count(chat_id)
        except TelegramError:
            pass
    except TelegramError:
        # البوت ليس في المجموعة بعد — لا بأس، تمت الإضافة بالفعل
        pass

    # تحديد نوع المحادثة
    chat_kind = "قناة 📢"
    try:
        c_info = await context.bot.get_chat(chat_id)
        chat_kind = "قناة 📢" if c_info.type in CHANNEL_TYPES else "مجموعة 👥"
    except TelegramError:
        pass

    await update.effective_message.reply_text(
        f"✅ أُضيف/ت بنجاح!\n"
        f"📛 الاسم: {title}\n"
        f"📌 النوع: {chat_kind}\n"
        f"🆔 المعرّف: {chat_id}\n"
        f"👥 الأعضاء (Telegram): {member_count}\n\n"
        f"⏳ جارٍ جلب قائمة الأعضاء الكاملة..."
    )
    # مزامنة فورية عبر Telethon
    result = await sync_group_members(chat_id)
    synced = result["count"]
    reason = result["reason"]

    if synced > 0:
        await update.effective_message.reply_text(
            f"✅ تمت المزامنة بنجاح!\n"
            f"👥 تم جلب *{synced}* عضو عبر Telethon 🎉",
            parse_mode="Markdown"
        )
    elif reason == "hidden":
        await update.effective_message.reply_text(
            "🔒 *المجموعة تحجب قائمة الأعضاء*\n\n"
            "البوت سيعمل بشكل طبيعي ✅\n"
            "سيُضاف الأعضاء تلقائياً عبر:\n"
            "• رسائلهم في المجموعة\n"
            "• انضمامهم أو مغادرتهم",
            parse_mode="Markdown"
        )
    elif reason == "no_client":
        await update.effective_message.reply_text(
            "⚠️ Telethon غير مفعّل\n"
            "تأكد من ضبط: <code>API_ID</code> و <code>API_HASH</code> و <code>SESSION_STRING</code> في Render",
            parse_mode="HTML"
        )
    else:
        await update.effective_message.reply_text(
            f"⚠️ خطأ في جلب الأعضاء:\n`{reason}`\n"
            f"<i>سيُضاف الأعضاء تدريجياً</i>",
            parse_mode="Markdown"
        )
    logger.info(f"👑 المالك أضاف مجموعة: {title} ({chat_id}) | {synced} عضو | سبب: {reason}")


async def cmd_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    """
    /groups — يعرض كل المجموعات النشطة مع حالتها
    """
    if not _is_owner(update):
        return
    if not active_groups:
        await update.effective_message.reply_text("📭 لا توجد مجموعات نشطة حتى الآن.")
        return
    lines = [f"📋 *المجموعات والقنوات النشطة ({len(active_groups)}):*\n"]
    for chat_id in sorted(active_groups):
        members_count = len(active_members.get(chat_id, {}))
        try:
            chat_info   = await context.bot.get_chat(chat_id)
            title       = chat_info.title
            is_chan      = chat_info.type in CHANNEL_TYPES
            icon        = "📢" if is_chan else "👥"
            members_str = "قناة" if is_chan else f"{members_count} عضو مسجّل"
        except TelegramError:
            title       = "غير متاحة ⚠️"
            icon        = "❓"
            members_str = f"{members_count} عضو"
        lines.append(f"• {icon} {title}\n  🆔 `{chat_id}` | 👤 {members_str}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_removegroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    # ── فحص القفل ──────────────────────────────────────────────────────────────
    if update.effective_chat and update.effective_chat.id in locked_groups:
        is_owner = update.effective_user.id in ADMIN_IDS
        is_admin = await _is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id)
        if not is_owner and not is_admin:
            msg = await update.effective_message.reply_text(
                "🔒 البوت مقفل — الأوامر للأدمن فقط."
            )
            add_to_delete_queue(update.effective_chat.id, msg.message_id)
            add_to_delete_queue(update.effective_chat.id, update.effective_message.message_id)
            return
    """
    /removegroup -100xxxxxxxxxx — يزيل مجموعة من القائمة
    """
    if not _is_owner(update):
        return

    if not context.args:
        await update.effective_message.reply_text("الاستخدام: /removegroup -100xxxxxxxxxx")
        return

    chat_id_str = context.args[0]

    try:
        chat_id = int(chat_id_str)
        if chat_id in active_groups:
            active_groups.discard(chat_id)
            active_members.pop(chat_id, None)
            rotation_queue.pop(chat_id, None)
            save_data()
            await update.effective_message.reply_text(f"✅ حُذفت المجموعة {chat_id} من القائمة.")
        else:
            await update.effective_message.reply_text("⚠️ هذه المجموعة غير موجودة في القائمة.")
    except (ValueError, IndexError):
        await update.effective_message.reply_text("❌ معرّف غير صالح.")


async def track_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يُطلق عند كل منشور في قناة البوت فيها أدمن.
    يسجّل القناة في active_groups إن لم تكن مسجّلة.
    """
    chat = update.effective_chat
    if not chat or chat.type not in CHANNEL_TYPES:
        return
    if chat.id not in active_groups:
        active_groups.add(chat.id)
        save_data()
        logger.info(f"📢 قناة جديدة اكتُشفت تلقائياً: {chat.title} ({chat.id})")


async def track_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تتبع الأعضاء الذين يرسلون رسائل — مصدر احتياطي"""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or user.is_bot:
        return
    if chat.type not in GROUP_TYPES:
        return
    was_new_group  = chat.id not in active_groups
    was_new_member = user.id not in active_members.get(chat.id, {})
    active_groups.add(chat.id)
    if chat.id not in active_members:
        active_members[chat.id] = {}
    active_members[chat.id][user.id] = _user_to_dict(user)
    if was_new_group or was_new_member:
        save_data()


async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يُطلق عند أي تغيير في عضوية أي شخص:
    - انضم  → أضفه للقائمة
    - غادر أو طُرد → احذفه من القائمة فوراً
    """
    if not update.chat_member:
        return
    chat       = update.effective_chat
    new_member = update.chat_member.new_chat_member
    user       = new_member.user

    if chat.type not in GROUP_TYPES or user.is_bot:
        return

    if new_member.status in ("member", "administrator", "restricted"):
        if chat.id not in active_members:
            active_members[chat.id] = {}
        active_members[chat.id][user.id] = _user_to_dict(user)
        active_groups.add(chat.id)
        logger.info(f"➕ انضم: {user.first_name} إلى {chat.title}")
        save_data()

    elif new_member.status in ("left", "kicked"):
        if chat.id in active_members:
            active_members[chat.id].pop(user.id, None)
            if chat.id in rotation_queue:
                rotation_queue[chat.id] = [
                    uid for uid in rotation_queue[chat.id] if uid != user.id
                ]
            logger.info(f"➖ غادر: {user.first_name} من {chat.title}")
            save_data()


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يُطلق عند إضافة البوت أو إزالته من أي مجموعة أو قناة.
    القناة: تُسجَّل تلقائياً عند إضافة البوت أدميناً.
    المجموعة: تُسجَّل وتُرسَل رسالة ترحيب.
    """
    if not update.my_chat_member:
        return
    chat      = update.effective_chat
    status    = update.my_chat_member.new_chat_member.status
    is_channel = chat.type in CHANNEL_TYPES

    # تجاهل كل ما ليس مجموعة أو قناة
    if chat.type not in ACTIVE_TYPES:
        return

    if status in ("administrator", "member"):
        active_groups.add(chat.id)
        save_data()
        kind = "قناة" if is_channel else "مجموعة"
        logger.info(f"➕ أُضيف البوت إلى {kind}: {chat.title} ({chat.id})")

        # جلب الأدمن فوراً عند الانضمام
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
            if chat.id not in active_members:
                active_members[chat.id] = {}
            for admin in admins:
                if not admin.user.is_bot:
                    active_members[chat.id][admin.user.id] = _user_to_dict(admin.user)
            save_data()
        except TelegramError:
            pass
        if status == "administrator":
            if is_channel:
                # في القنوات البوت يرسل مباشرة (لا يحتاج /start)
                text = (
                    "🕌 *بسم الله الرحمن الرحيم*\n\n"
                    "تم تفعيل بوت الذِّكر والصلاة في قناتكم! 🌟\n\n"
                    "📋 *ما سيُرسَل تلقائياً:*\n"
                    "• ⏰ تذكير الصلوات الخمس يومياً\n"
                    "• 🌅 أذكار الصباح والمساء\n"
                    "• 📿 أذكار متنوعة على مدار اليوم\n"
                    "• 📖 أحاديث نبوية شريفة\n"
                    "• 🌿 آيات قرآنية كريمة\n"
                    "• 🗑️ حذف الرسائل تلقائياً بعد 30 دقيقة\n\n"
                    "<i>بارك الله فيكم</i> 🤲"
                )
            else:
                text = (
                    "🕌 <b>بسم الله الرحمن الرحيم</b>\n\n"
                    "جزاكم الله خيراً على إضافتي أدميناً! 🌟\n"
                    "أرسل /start لرؤية الخيارات 🤲"
                )
            try:
                msg = await context.bot.send_message(chat.id, text, parse_mode="HTML")
                add_to_delete_queue(chat.id, msg.message_id)
            except TelegramError as e:
                logger.warning(f"⚠️ لم أستطع الإرسال إلى {chat.id}: {e}")

    elif status in ("kicked", "left"):
        active_groups.discard(chat.id)
        active_members.pop(chat.id, None)
        rotation_queue.pop(chat.id, None)
        save_data()
        kind = "قناة" if is_channel else "مجموعة"
        logger.info(f"➖ غادر البوت من {kind}: {chat.title} ({chat.id})")


# ══════════════════════════════════════════════════════════════════════════════
# Flask Health-check
# ══════════════════════════════════════════════════════════════════════════════

flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return (
        f"🕌 البوت يعمل | "
        f"المجموعات: {len(active_groups)} | "
        f"الأعضاء: {sum(len(v) for v in active_members.values())} | "
        f"حذف معلق: {len(pending_deletes)}"
    ), 200


def run_flask():
    from werkzeug.serving import make_server
    port   = int(os.getenv("PORT", 10000))
    server = make_server("0.0.0.0", port, flask_app)
    logger.info(f"🌐 Flask جاهز على المنفذ {port}")
    server.serve_forever()
  # دالة التحويل المعدلة
async def forward_to_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_chat:
        return
    if not MONITORED_GROUP or not MONITOR_OWNER_ID:
        return
    if update.effective_chat.id != MONITORED_GROUP:
        return
    if not monitoring_active:
        return
    try:
        await context.bot.forward_message(
            chat_id=MONITOR_OWNER_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.effective_message.message_id,
        )
    except TelegramError:
        pass

# ══════════════════════════════════════════════════════════════════════════════
# الدالة الرئيسية بعد الدمج
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    logger.info("🚀 بدء تشغيل البوت الإسلامي...")

    load_delete_queue()
    load_config()
    await init_mongo()
    await load_data_async()
    # تحميل delete_queue و config من MongoDB
    db = _get_db()
    if db is not None:
        try:
            dq = await db["delete_queue"].find_one({"_id": "queue"})
            if dq and "items" in dq:
                global pending_deletes
                pending_deletes = dq["items"]
                logger.info(f"☁️ قائمة الحذف من MongoDB: {len(pending_deletes)}")
            cfg = await db["bot_config"].find_one({"_id": "config"})
            if cfg:
                cfg.pop("_id", None)
                global bot_config
                bot_config.update(cfg)
                logger.info("☁️ الإعدادات من MongoDB")
        except Exception as e:
            logger.error(f"❌ خطأ في تحميل MongoDB الإضافي: {e}")

    global _data_ready
    _data_ready = True
    logger.info("✅ البيانات جاهزة — jobs مفعّلة")
    threading.Thread(target=run_flask, daemon=True).start()
    # اكتشاف المجموعات تلقائياً إذا كانت القائمة فارغة
    # (يعمل عند الترقية من مشروع قديم بدون قاعدة بيانات)
    # سيُستدعى بعد بناء app ليحصل على bot object

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("setcity",     cmd_setcity))
    app.add_handler(CommandHandler("mycity",      cmd_mycity))
    app.add_handler(CommandHandler("awkat",       cmd_awkat))
    app.add_handler(CommandHandler("dhikr",       cmd_dhikr))
    app.add_handler(CommandHandler("hadith",      cmd_hadith))
    app.add_handler(CommandHandler("aya",         cmd_aya))
    app.add_handler(CommandHandler("tasbih",      cmd_tasbih))
    app.add_handler(CommandHandler("myid",        cmd_myid))
    app.add_handler(CommandHandler("schedule",    cmd_schedule))
    app.add_handler(CommandHandler("settime",     cmd_settime))
    app.add_handler(CommandHandler("addcontent",  cmd_addcontent))
    app.add_handler(CommandHandler("listcontent", cmd_listcontent))
    app.add_handler(CommandHandler("delcontent",  cmd_delcontent))
    app.add_handler(CommandHandler("checkdeletes", cmd_checkdeletes))
    app.add_handler(CommandHandler("sync",        cmd_sync))
    app.add_handler(CommandHandler("addgroup",    cmd_addgroup))
    app.add_handler(CommandHandler("groups",      cmd_groups))
    app.add_handler(CommandHandler("removegroup", cmd_removegroup))
    app.add_handler(CommandHandler("pause",        cmd_pause))
    app.add_handler(CommandHandler("resume",       cmd_resume))
    app.add_handler(CommandHandler("lock",         cmd_lock))
    app.add_handler(CommandHandler("unlock",       cmd_unlock))
    app.add_handler(CommandHandler("settopic",     cmd_settopic))
    app.add_handler(CommandHandler("checktopic",   cmd_checktopic))
    app.add_handler(CommandHandler("cleartopic",   cmd_cleartopic))
    app.add_handler(CommandHandler("broadcast",    cmd_broadcast))
    app.add_handler(CommandHandler("report",       cmd_report))
    app.add_handler(CommandHandler("remind",       cmd_remind))
    app.add_handler(CommandHandler("remindme",     cmd_remindme))
    app.add_handler(CallbackQueryHandler(callback_private, pattern="^private_"))
    app.add_handler(ChatMemberHandler(on_my_chat_member,     ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    
    from telegram.ext import MessageHandler, filters
    app.add_handler(MessageHandler((filters.TEXT | filters.COMMAND) & filters.ChatType.GROUPS, track_member))
    # معالج منشورات القناة — يسجّل القناة تلقائياً عند أول منشور
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, track_channel_post))

    # 🟢 [تم الدمج هنا]: معالج تحويل الرسائل للمالك من أي نوع ومن أي توبيك داخل المجموعات
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, forward_to_owner))

    global _scheduler
    scheduler = AsyncIOScheduler(
        timezone=ALGERIA_TZ,
        job_defaults={
            "coalesce":           True,   # لا تُشغّل job مرتين إذا فاتت
            "max_instances":      1,      # منع التشغيل المتزامن
            "misfire_grace_time": 60,     # تجاهل المهام التي فات وقتها أكثر من دقيقة
        }
    )
    _scheduler = scheduler

    # ربط مفاتيح الجدول بالدوال المقابلة
    SCHEDULE_FUNCS.update({
        "morning_adhkar": job_morning_adhkar,
        "evening_adhkar": job_evening_adhkar,
        "dhikr_1":        job_random_dhikr,
        "dhikr_2":        job_random_dhikr,
        "dhikr_3":        job_random_dhikr,
        "hadith_1":       job_hadith,
        "hadith_2":       job_hadith,
        "quran_1":        job_quran_verse,
        "quran_2":        job_quran_verse,
        "tasbih_1":       job_tasbih_challenge,
        "tasbih_2":       job_tasbih_challenge,
    })

    scheduler.add_job(reschedule_prayers,   "cron",     hour=0,  minute=5,  args=[app.bot, scheduler], id="reschedule_daily")

    # المواعيد المأخوذة من bot_config (قابلة للتعديل من تيليغرام)
    def _s(key):
        return bot_config["schedule"].get(key, DEFAULT_SCHEDULE[key])

    scheduler.add_job(job_morning_adhkar,   "cron", id="morning_adhkar", timezone=ALGERIA_TZ, hour=_s("morning_adhkar")["hour"], minute=_s("morning_adhkar")["minute"], args=[app.bot])
    scheduler.add_job(job_evening_adhkar,   "cron", id="evening_adhkar", timezone=ALGERIA_TZ, hour=_s("evening_adhkar")["hour"], minute=_s("evening_adhkar")["minute"], args=[app.bot])
    scheduler.add_job(job_random_dhikr,     "cron", id="dhikr_1",        timezone=ALGERIA_TZ, hour=_s("dhikr_1")["hour"],        minute=_s("dhikr_1")["minute"],        args=[app.bot])
    scheduler.add_job(job_random_dhikr,     "cron", id="dhikr_2",        timezone=ALGERIA_TZ, hour=_s("dhikr_2")["hour"],        minute=_s("dhikr_2")["minute"],        args=[app.bot])
    scheduler.add_job(job_random_dhikr,     "cron", id="dhikr_3",        timezone=ALGERIA_TZ, hour=_s("dhikr_3")["hour"],        minute=_s("dhikr_3")["minute"],        args=[app.bot])
    scheduler.add_job(job_hadith,           "cron", id="hadith_1",       timezone=ALGERIA_TZ, hour=_s("hadith_1")["hour"],       minute=_s("hadith_1")["minute"],       args=[app.bot])
    scheduler.add_job(job_hadith,           "cron", id="hadith_2",       timezone=ALGERIA_TZ, hour=_s("hadith_2")["hour"],       minute=_s("hadith_2")["minute"],       args=[app.bot])
    scheduler.add_job(job_quran_verse,      "cron", id="quran_1",        timezone=ALGERIA_TZ, hour=_s("quran_1")["hour"],        minute=_s("quran_1")["minute"],        args=[app.bot])
    scheduler.add_job(job_quran_verse,      "cron", id="quran_2",        timezone=ALGERIA_TZ, hour=_s("quran_2")["hour"],        minute=_s("quran_2")["minute"],        args=[app.bot])
    scheduler.add_job(job_tasbih_challenge, "cron", id="tasbih_1",       timezone=ALGERIA_TZ, hour=_s("tasbih_1")["hour"],       minute=_s("tasbih_1")["minute"],       args=[app.bot])
    scheduler.add_job(job_tasbih_challenge, "cron", id="tasbih_2",       timezone=ALGERIA_TZ, hour=_s("tasbih_2")["hour"],       minute=_s("tasbih_2")["minute"],       args=[app.bot])
    scheduler.add_job(process_delete_queue, "interval", minutes=1,          args=[app.bot], id="delete_processor")
    scheduler.add_job(save_data,            "interval", minutes=10,         id="periodic_save")
    scheduler.add_job(sync_all_groups,      "cron",     hour=3,  minute=0,  id="daily_sync")
    scheduler.add_job(auto_register_admin_groups, "interval", hours=6, args=[app.bot], id="admin_check")
    scheduler.add_job(job_city_reminder, "cron", day_of_week="fri", hour=9, minute=0, args=[app.bot], id="city_reminder")
    scheduler.add_job(job_cleanup_db,    "cron", day_of_week="sun", hour=3, minute=30, id="weekly_cleanup")

    scheduler.start()
    logger.info("⏰ المجدول يعمل")

    async with app:
        await app.start()
        # تهيئة Telethon
        _tg = await get_telethon_client()
        if _tg:
            logger.info("✅ Telethon جاهز — جلب الأعضاء الكاملة مفعّل")
        else:
            logger.warning("⚠️ Telethon غير متاح — سيُجمع الأعضاء تدريجياً")
        await verify_groups(app.bot)
        await auto_register_admin_groups(app.bot)
        # إذا لا تزال القائمة فارغة → اكتشاف تلقائي عبر Telethon
        if not active_groups:
            await discover_all_groups(app.bot)

        if active_groups:
            logger.info(f"✅ البوت يعمل — {len(active_groups)} مجموعة نشطة")
        else:
            logger.warning(
                "⚠️ لا توجد مجموعات نشطة — "
                "أضف البوت أدميناً ثم أرسل /start أو استخدم /addgroup"
            )

        # تحميل التذكيرات المعلقة من MongoDB
        await _load_and_schedule_reminders(app.bot)
        await reschedule_prayers(app.bot, scheduler)

        logger.info("✅ البوت جاهز ويستمع...")
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "channel_post", "my_chat_member", "chat_member", "callback_query"],
        )

        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            save_data()
            save_delete_queue()
            logger.info("💾 حفظ نهائي تم")
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
