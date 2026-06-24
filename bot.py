import logging
import asyncio
import os
import json
import random
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
    ChatMemberHandler
)
from telegram.error import BadRequest, TelegramError

# ─────────────────────────────────────────
# TOKEN — always use environment variable
# التوكن — استخدم متغير بيئة دائماً
# ─────────────────────────────────────────
TOKEN = os.getenv("BOT_TOKEN", "REPLACE_WITH_YOUR_NEW_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# SETTINGS / الإعدادات
# ─────────────────────────────────────────
GAME_TIMEOUT_SECONDS  = 300   # 5 min / 5 دقائق
TD_TIMEOUT_SECONDS    = 120   # 2 min / دقيقتان
WELCOME_DELETE_SECS   = 300   # 5 min / 5 دقائق
BOT_MSG_MAX_AGE_SECS  = 600   # 10 min / 10 دقائق

# ─────────────────────────────────────────
# MEMORY / الذاكرة
# ─────────────────────────────────────────
active_games:      dict = {}
truth_dare_state:  dict = {}
pending_mentions:  dict = {}
scores:            dict = defaultdict(lambda: defaultdict(int))

# bot_messages: list of (chat_id, message_id, sent_at)
bot_messages: list = []

# ─────────────────────────────────────────
# TRUTH / DARE — bilingual / ثنائي اللغة
# ─────────────────────────────────────────
TRUTH_QUESTIONS = [
    "ما أكثر شيء تكذب فيه على أهلك؟\nWhat do you lie about most to your family?",
    "من الشخص الذي تكرهه في المجموعة؟ 😏\nWho in this group annoys you the most?",
    "ما أحرج موقف مررت فيه؟\nWhat's the most embarrassing moment you've been through?",
    "هل سرقت شيئاً من قبل؟ ماذا؟\nHave you ever stolen something? What was it?",
    "ما أكبر سر تخفيه الآن؟\nWhat's the biggest secret you're hiding right now?",
    "من الشخص الذي تحبه أكثر في المجموعة؟\nWho do you like most in this group?",
    "ما الشيء الذي تتمنى أن لا يعرفه أهلك عنك؟\nWhat's something you hope your family never finds out about you?",
    "هل أنت مغرم بأحد الآن؟ من هو؟\nDo you have a crush right now? Who?",
    "ما أغبى قرار اتخذته في حياتك؟\nWhat's the dumbest decision you've ever made?",
    "ما الشيء الذي تتمنى تغييره في نفسك؟\nWhat's one thing you wish you could change about yourself?",
]

DARE_CHALLENGES = [
    "اكتب رسالة إعجاب لأي شخص في المجموعة الآن 💌\nWrite a compliment message to someone in the group right now 💌",
    "غيّر اسمك في المجموعة لمدة ساعة لـ '👶 الخاسر'\nChange your group name to '👶 The Loser' for one hour",
    "قلد أي عضو في المجموعة بأسلوب كتابته\nImpersonate any group member's writing style",
    "اكتب 'أنا خسرت!' بالحروف الكبيرة 10 مرات\nWrite 'I LOST!' in capital letters 10 times",
    "اكتب جملة بالإنجليزي وترجمها حرفياً بالعربي\nWrite an English sentence and translate it literally to Arabic",
    "اكتب مدحاً للفائز عليك بـ 3 أسطر 🎤\nWrite a 3-line praise for the winner 🎤",
    "أرسل صوتية تقول فيها 'الفائز أذكى مني' 🎙️\nSend a voice note saying 'The winner is smarter than me' 🎙️",
    "غيّر صورتك في المجموعة لمدة 30 دقيقة لأي صورة مضحكة\nChange your group photo to a funny picture for 30 minutes",
    "أرسل أحرج إيموجي في هاتفك\nSend the most embarrassing emoji on your phone",
    "اكتب أغرب فكرة تجول في بالك الآن\nWrite the weirdest thought in your head right now",
]

# ─────────────────────────────────────────
# HELPERS / مساعدات
# ─────────────────────────────────────────
def uname(user) -> str:
    if user.full_name:
        return user.full_name
    if user.username:
        return f"@{user.username}"
    return str(user.id)

def mention_html(user_id: int, display: str) -> str:
    return f'<a href="tg://user?id={user_id}">{display}</a>'

# ─────────────────────────────────────────
# CALLBACK STORE — with TTL cleanup
# مخزن الـ callbacks مع تنظيف تلقائي
# ─────────────────────────────────────────
_cb_store: dict = {}   # token → {data, created_at}
CB_TTL_SECONDS = 3600  # 1 hour / ساعة واحدة

def _cleanup_cb_store():
    """Remove expired callback tokens / حذف الـ tokens المنتهية"""
    cutoff = datetime.now() - timedelta(seconds=CB_TTL_SECONDS)
    expired = [k for k, v in _cb_store.items() if v.get("created_at", datetime.now()) < cutoff]
    for k in expired:
        _cb_store.pop(k, None)

def cb_pack(prefix: str, **kwargs) -> str:
    """Store data and return short token / يخزّن البيانات ويعيد مفتاح قصير"""
    _cleanup_cb_store()
    token = f"{prefix}_{random.randint(10000, 99999)}"
    _cb_store[token] = {"prefix": prefix, "created_at": datetime.now(), **kwargs}
    return token[:64]

def cb_get(token: str) -> dict | None:
    entry = _cb_store.get(token)
    if entry is None:
        return None
    return entry

def cb_clear(token: str):
    _cb_store.pop(token, None)

# ─────────────────────────────────────────
# BOT MESSAGE TRACKER — auto-delete after 10 min
# تتبع رسائل البوت — حذف تلقائي بعد 10 دقائق
# ─────────────────────────────────────────
def track_bot_msg(chat_id: int, message_id: int):
    """Register a bot message for auto-deletion / تسجيل رسالة للحذف التلقائي"""
    bot_messages.append({
        "chat_id":    chat_id,
        "message_id": message_id,
        "sent_at":    datetime.now()
    })

async def purge_old_bot_messages(context: ContextTypes.DEFAULT_TYPE):
    """Job: delete bot messages older than 10 min / حذف رسائل البوت القديمة"""
    cutoff = datetime.now() - timedelta(seconds=BOT_MSG_MAX_AGE_SECS)
    to_delete = [m for m in bot_messages if m["sent_at"] < cutoff]
    for m in to_delete:
        try:
            await context.bot.delete_message(m["chat_id"], m["message_id"])
        except TelegramError:
            pass
        bot_messages.remove(m)

# ─────────────────────────────────────────
# XO GAME / لعبة XO
# ─────────────────────────────────────────
def xo_new_board():
    return [None] * 9

def xo_render(board, p1_id, p2_id) -> InlineKeyboardMarkup:
    sym = {p1_id: "❌", p2_id: "⭕"}
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            v = board[i]
            label = sym.get(v, "·") if v else "·"
            cb    = f"xo_taken_{i}" if v else f"xo_move_{i}"
            row.append(InlineKeyboardButton(label, callback_data=cb))
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def xo_winner(board):
    lines = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for a, b, c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if all(board) else None

# ─────────────────────────────────────────
# MUTE / UNMUTE — تقييد / رفع التقييد
# ─────────────────────────────────────────
async def mute(context, chat_id, user_id, minutes: int) -> bool:
    try:
        until = datetime.now() + timedelta(minutes=minutes)
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
        return True
    except TelegramError as e:
        logger.warning(f"mute error: {e}")
        return False

async def unmute(context, chat_id, user_id):
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            )
        )
    except TelegramError as e:
        logger.warning(f"unmute error: {e}")

# ─────────────────────────────────────────
# WELCOME NEW MEMBERS / ترحيب بالأعضاء الجدد
# ─────────────────────────────────────────
async def on_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome new members and auto-delete after 5 min / ترحيب يُحذف بعد 5 دقائق"""
    msg = update.message
    if not msg or not msg.new_chat_members:
        return

    for member in msg.new_chat_members:
        if member.is_bot:
            continue

        name = uname(member)
        mention = mention_html(member.id, name)

        text = (
            f"👋 <b>أهلاً وسهلاً {mention}!</b>\n"
            f"Welcome to the group! 🎉\n\n"
            f"🎮 تحدَّ أحداً بمنشنه والعب XO\n"
            f"Challenge someone by mentioning them & play XO!\n\n"
            f"📊 /scores — الترتيب | Leaderboard\n"
            f"📖 /help — المساعدة | Help"
        )
        sent = await msg.reply_text(text, parse_mode="HTML")
        track_bot_msg(msg.chat_id, sent.message_id)

        # Delete welcome after 5 min / حذف الترحيب بعد 5 دقائق
        context.job_queue.run_once(
            _delete_msg_job,
            WELCOME_DELETE_SECS,
            data={"chat_id": msg.chat_id, "msg_id": sent.message_id},
            name=f"welcdel_{msg.chat_id}_{sent.message_id}"
        )

async def _delete_msg_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    try:
        await context.bot.delete_message(d["chat_id"], d["msg_id"])
        # Remove from tracker too
        bot_messages[:] = [m for m in bot_messages
                           if not (m["chat_id"] == d["chat_id"] and m["message_id"] == d["msg_id"])]
    except TelegramError:
        pass

# ─────────────────────────────────────────
# MESSAGE HANDLER / معالج الرسائل
# ─────────────────────────────────────────
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    cid    = msg.chat_id
    sender = msg.from_user

    # Handle truth/dare replies FIRST
    # معالجة ردود الصراحة/الجرأة أولاً
    td_handled = await handle_td_reply(update, context)
    if td_handled:
        return

    # Only process mentions if no active TD session for this user
    # معالجة المنشن فقط إذا لم يكن هناك جلسة صراحة/جرأة نشطة
    state = truth_dare_state.get(cid)
    if state and sender.id in (state.get("w_id"), state.get("l_id")):
        return

    if not msg.entities:
        return

    for ent in msg.entities:
        if ent.type != "mention":
            continue

        mentioned = msg.text[ent.offset + 1: ent.offset + ent.length]
        bot_username = (context.bot.username or "").lower()
        if mentioned.lower() == bot_username:
            continue

        # Avoid self-mention / تجنّب المنشن الذاتي
        if sender.username and mentioned.lower() == sender.username.lower():
            continue

        # Try to resolve user ID from username
        # محاولة الحصول على ID من الـ username
        t_id = None
        t_name = mentioned
        # We can't reliably get user_id from username via get_chat for regular users.
        # We store username and validate at acceptance time instead.
        # لا يمكن الحصول على user_id من username بشكل موثوق.
        # نخزّن الـ username ونتحقق عند القبول.

        token_game  = cb_pack("mg", c=sender.id, u=mentioned)
        token_reply = cb_pack("mr", c=sender.id, u=mentioned)

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚔️ تحدي | Challenge", callback_data=token_game),
            InlineKeyboardButton("👋 رد | Reply",        callback_data=token_reply),
        ]])

        sent = await msg.reply_text(
            f"👀 <b>{uname(sender)}</b> ذكر | mentioned <b>@{mentioned}</b>\n\nماذا تريد؟ | What do you want?",
            reply_markup=kb,
            parse_mode="HTML"
        )
        track_bot_msg(cid, sent.message_id)
        return  # One mention is enough / منشن واحد يكفي

# ─────────────────────────────────────────
# MENTION CALLBACKS / callbacks المنشن
# ─────────────────────────────────────────
async def on_mention_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    cid  = q.message.chat_id
    data = cb_get(q.data)

    if not data:
        await q.answer("⌛ انتهت الصلاحية | Expired", show_alert=True)
        return

    prefix = data["prefix"]

    # ── Choose to challenge ──
    if prefix == "mg":
        if user.id != data["c"]:
            await q.answer("🚫 ليس لك | Not for you", show_alert=True)
            return

        t_xo = cb_pack("px", c=data["c"], u=data["u"], g="xo")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("❌⭕ XO", callback_data=t_xo),
        ]])
        await q.edit_message_text(
            f"⚔️ <b>{uname(user)}</b> يتحدى | challenges <b>@{data['u']}</b>\n\nاختر اللعبة | Choose game:",
            reply_markup=kb, parse_mode="HTML"
        )

    # ── Reply ──
    elif prefix == "mr":
        if user.id != data["c"]:
            await q.answer("🚫 ليس لك | Not for you", show_alert=True)
            return
        await q.edit_message_text("👋 تم! رد عليه مباشرة | Done! Reply directly in the group.")

    # ── Game selected → show accept/reject ──
    elif prefix == "px":
        if user.id != data["c"]:
            await q.answer("🚫 ليس لك | Not for you", show_alert=True)
            return

        t_acc = cb_pack("ac", c=data["c"], u=data["u"], g=data["g"])
        t_rej = cb_pack("rj", c=data["c"], u=data["u"])

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ قبول | Accept", callback_data=t_acc),
            InlineKeyboardButton("❌ رفض | Reject",  callback_data=t_rej),
        ]])
        await q.edit_message_text(
            f"🎮 <b>{uname(user)}</b> يتحداك | challenges you on ❌⭕ XO!\n\n"
            f"@{data['u']} هل تقبل؟ | Do you accept?",
            reply_markup=kb, parse_mode="HTML"
        )

    # ── Accept ──
    elif prefix == "ac":
        # The challenged person must accept — validate by username match
        # المتحدَّى يجب أن يقبل — نتحقق من الـ username
        if user.username and user.username.lower() != data["u"].lower():
            await q.answer("🚫 هذه الدعوة ليست لك | This invite is not for you", show_alert=True)
            return

        await q.edit_message_text("✅ تم القبول! تبدأ اللعبة الآن... | Accepted! Game starting...")
        await game_start_xo(context, cid, data["c"], user.id, uname(user), data["u"])

    # ── Reject ──
    elif prefix == "rj":
        await q.edit_message_text(
            f"❌ @{data['u']} رفض التحدي! | rejected the challenge!"
        )

# ─────────────────────────────────────────
# START XO / بدء XO
# ─────────────────────────────────────────
async def game_start_xo(context, cid, p1_id, p2_id, p2_name, p2_username):
    try:
        info = await context.bot.get_chat_member(cid, p1_id)
        p1_name = uname(info.user)
    except TelegramError:
        p1_name = "Player 1 / اللاعب 1"

    board = xo_new_board()
    active_games[cid] = {
        "type": "xo", "board": board,
        "p1": p1_id, "p2": p2_id,
        "p1_name": p1_name, "p2_name": p2_name,
        "p2_username": p2_username,
        "turn": p1_id, "msg_id": None,
        "started": datetime.now().isoformat()
    }

    text = (
        f"❌⭕ <b>XO</b>\n\n"
        f"❌ {p1_name}  vs  ⭕ {p2_name}\n\n"
        f"🎯 دور | Turn: <b>{p1_name}</b> ❌"
    )
    sent = await context.bot.send_message(
        cid, text,
        reply_markup=xo_render(board, p1_id, p2_id),
        parse_mode="HTML"
    )
    active_games[cid]["msg_id"] = sent.message_id
    track_bot_msg(cid, sent.message_id)
    _schedule_game_timeout(context, cid)

# ─────────────────────────────────────────
# GAME TIMEOUT / timeout اللعبة
# ─────────────────────────────────────────
def _schedule_game_timeout(context, cid):
    job_name = f"gtout_{cid}"
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
    context.job_queue.run_once(
        _game_timeout_job, GAME_TIMEOUT_SECONDS,
        data={"cid": cid}, name=job_name
    )

def _cancel_game_timeout(context, cid):
    for job in context.job_queue.get_jobs_by_name(f"gtout_{cid}"):
        job.schedule_removal()

async def _game_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    cid = context.job.data["cid"]
    g   = active_games.pop(cid, None)
    if not g:
        return
    try:
        await context.bot.edit_message_text(
            "⏰ انتهى وقت اللعبة! لا فائز.\nGame timed out! No winner.",
            chat_id=cid, message_id=g["msg_id"]
        )
    except TelegramError:
        pass

# ─────────────────────────────────────────
# XO CALLBACK / معالج XO
# ─────────────────────────────────────────
async def on_xo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    cid  = q.message.chat_id
    data = q.data

    if "taken" in data:
        await q.answer("🚫 هذه الخانة محجوزة | Cell taken")
        return

    g = active_games.get(cid)
    if not g or g["type"] != "xo":
        await q.answer("لا توجد لعبة نشطة | No active game")
        return
    if user.id not in (g["p1"], g["p2"]):
        await q.answer("🚫 اللعبة ليست لك | Not your game", show_alert=True)
        return
    if user.id != g["turn"]:
        await q.answer("⏳ ليس دورك | Not your turn", show_alert=True)
        return

    idx = int(data.split("_")[-1])
    if g["board"][idx]:
        await q.answer("🚫 الخانة محجوزة | Cell taken")
        return

    g["board"][idx] = user.id
    winner = xo_winner(g["board"])
    p1, p2   = g["p1"], g["p2"]
    p1n, p2n = g["p1_name"], g["p2_name"]

    await q.answer()

    if winner == "draw":
        await q.edit_message_text(
            f"❌⭕ <b>XO — تعادل! | Draw! 🤝</b>\n\n{p1n}  vs  {p2n}",
            reply_markup=xo_render(g["board"], p1, p2),
            parse_mode="HTML"
        )
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        return

    if winner:
        w_id   = winner
        w_name = p1n if w_id == p1 else p2n
        l_id   = p2  if w_id == p1 else p1
        l_name = p2n if w_id == p1 else p1n
        l_user = g["p2_username"] if w_id == p1 else None

        scores[cid][w_id] += 1
        await q.edit_message_text(
            f"❌⭕ <b>XO — انتهت! | Finished!</b>\n\n"
            f"🏆 فاز | Winner: <b>{w_name}</b>\n"
            f"💀 خسر | Loser: <b>{l_name}</b>\n\n"
            f"🏅 {w_name}: {scores[cid][w_id]} انتصار | win(s)",
            reply_markup=xo_render(g["board"], p1, p2),
            parse_mode="HTML"
        )
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        await td_start(context, cid, w_id, w_name, l_id, l_name, l_user)
        return

    # Next turn / الدور التالي
    g["turn"]  = p2 if user.id == p1 else p1
    nxt_name   = p2n if g["turn"] == p2 else p1n
    symbol     = "⭕" if g["turn"] == p2 else "❌"
    await q.edit_message_text(
        f"❌⭕ <b>XO</b>\n\n❌ {p1n}  vs  ⭕ {p2n}\n\n🎯 دور | Turn: <b>{nxt_name}</b> {symbol}",
        reply_markup=xo_render(g["board"], p1, p2),
        parse_mode="HTML"
    )
    _schedule_game_timeout(context, cid)

# ─────────────────────────────────────────
# TRUTH OR DARE / صراحة أو جرأة
# ─────────────────────────────────────────
async def td_start(context, cid, w_id, w_name, l_id, l_name, l_username):
    l_mention = f"@{l_username}" if l_username else l_name

    t_truth = cb_pack("td_pick", l=l_id, ch="truth")
    t_dare  = cb_pack("td_pick", l=l_id, ch="dare")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗣️ صراحة | Truth", callback_data=t_truth),
        InlineKeyboardButton("😈 جرأة | Dare",    callback_data=t_dare),
    ]])
    sent = await context.bot.send_message(
        cid,
        f"🏆 فاز | Won: <b>{w_name}</b> على | vs <b>{l_mention}</b>!\n\n"
        f"يا {l_mention}، اختر عقوبتك | Choose your penalty:",
        reply_markup=kb, parse_mode="HTML"
    )
    track_bot_msg(cid, sent.message_id)

    truth_dare_state[cid] = {
        "phase": "choosing",
        "w_id": w_id,   "w_name": w_name,
        "l_id": l_id,   "l_name": l_name,
        "l_username": l_username,
        "choice": None, "question": None,
        "bot_msgs": [sent.message_id],
    }

    context.job_queue.run_once(
        _td_timeout_job, TD_TIMEOUT_SECONDS,
        data={"cid": cid}, name=f"tdtout_{cid}"
    )

async def _td_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    cid   = context.job.data["cid"]
    state = truth_dare_state.pop(cid, None)
    if not state:
        return
    l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]
    sent = await context.bot.send_message(
        cid,
        f"⏰ انتهى الوقت! | Time's up!\n"
        f"<b>{l_mention}</b> لم يختر → تم تجاهل العقوبة.\n"
        f"didn't choose → penalty ignored.",
        parse_mode="HTML"
    )
    track_bot_msg(cid, sent.message_id)

def _cancel_td_timeout(context, cid):
    for job in context.job_queue.get_jobs_by_name(f"tdtout_{cid}"):
        job.schedule_removal()

async def on_td_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    cid  = q.message.chat_id
    data = cb_get(q.data)

    if not data:
        await q.answer("⌛ انتهت الصلاحية | Expired", show_alert=True)
        return

    state = truth_dare_state.get(cid)
    if not state:
        await q.answer("لا يوجد نظام نشط | No active session")
        return

    prefix = data["prefix"]

    # ── Loser picks truth or dare ──
    if prefix == "td_pick":
        if user.id != data["l"]:
            await q.answer("🚫 ليس لك | Not for you", show_alert=True)
            return

        choice = data["ch"]
        state["choice"] = choice
        state["phase"]  = "waiting_question"

        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]
        label = "صراحة 🗣️ | Truth" if choice == "truth" else "جرأة 😈 | Dare"

        auto = random.choice(TRUTH_QUESTIONS if choice == "truth" else DARE_CHALLENGES)

        t_auto   = cb_pack("td_auto",   w=state["w_id"], l=state["l_id"], q=auto)
        t_custom = cb_pack("td_custom", w=state["w_id"])

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎲 استخدم هذا | Use this", callback_data=t_auto),
            InlineKeyboardButton("✏️ اكتب سؤالك | Write yours", callback_data=t_custom),
        ]])
        await q.edit_message_text(
            f"✅ <b>{l_mention}</b> اختار | chose <b>{label}</b>!\n\n"
            f"يا <b>{state['w_name']}</b>، اقتراح ذكي | Smart suggestion:\n"
            f"❓ <i>{auto}</i>",
            reply_markup=kb, parse_mode="HTML"
        )

    # ── Winner uses auto suggestion ──
    elif prefix == "td_auto":
        if user.id != data["w"]:
            await q.answer("🚫 ليس لك | Not for you", show_alert=True)
            return
        state["question"] = data["q"]
        await _send_question_to_loser(q, context, cid, state)

    # ── Winner writes custom question ──
    elif prefix == "td_custom":
        if user.id != data["w"]:
            await q.answer("🚫 ليس لك | Not for you", show_alert=True)
            return
        state["phase"] = "waiting_question"
        await q.edit_message_text(
            f"✏️ يا <b>{state['w_name']}</b>، اكتب سؤالك الآن | Write your question now:",
            parse_mode="HTML"
        )

    # ── Loser agrees to answer ──
    elif prefix == "td_will":
        if user.id != data["l"]:
            await q.answer("🚫 ليس لك | Not for you", show_alert=True)
            return
        state["phase"] = "waiting_answer"
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]
        await q.edit_message_text(
            f"✍️ <b>{l_mention}</b> سيجيب الآن | will answer now...\n\n"
            f"اكتب إجابتك في المجموعة | Write your answer in the group:",
            parse_mode="HTML"
        )

    # ── Loser refuses ──
    elif prefix == "td_refuse":
        if user.id != data["l"]:
            await q.answer("🚫 ليس لك | Not for you", show_alert=True)
            return

        l_id      = state["l_id"]
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]

        t_back = cb_pack("td_will", l=l_id)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 تراجعت! سأجيب | I changed my mind!", callback_data=t_back)
        ]])
        await q.edit_message_text(
            f"⚠️ <b>{l_mention}</b> رفض الإجابة! | refused to answer!\n"
            f"سيُقيَّد من المجموعة <b>30 دقيقة</b> | will be restricted for <b>30 min</b> 🔴\n\n"
            f"تراجع وأجب قبل 30 ثانية | Change your mind in 30 seconds:",
            reply_markup=kb, parse_mode="HTML"
        )
        context.job_queue.run_once(
            _mute_job, 30,
            data={"cid": cid, "l_id": l_id, "mins": 30},
            name=f"mute_{cid}_{l_id}"
        )

    # ── Winner accepts the answer ──
    elif prefix == "td_yes":
        if user.id != data["w"]:
            await q.answer("🚫 ليس لك | Not for you", show_alert=True)
            return
        await q.edit_message_text(
            "✅ تم قبول الإجابة! اللعبة انتهت 🎉\nAnswer accepted! Game over 🎉",
            parse_mode="HTML"
        )
        truth_dare_state.pop(cid, None)
        _cancel_td_timeout(context, cid)

    # ── Winner rejects the answer ──
    elif prefix == "td_no":
        if user.id != data["w"]:
            await q.answer("🚫 ليس لك | Not for you", show_alert=True)
            return
        l_id      = state["l_id"]
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]
        await q.edit_message_text(
            f"❌ الإجابة مرفوضة! | Answer rejected!\n"
            f"⚠️ <b>{l_mention}</b> يُقيَّد | restricted <b>10 دقائق | minutes</b>",
            parse_mode="HTML"
        )
        await mute(context, cid, l_id, 10)
        context.job_queue.run_once(
            _unmute_job, 600,
            data={"cid": cid, "uid": l_id},
            name=f"unmute_{cid}_{l_id}"
        )
        truth_dare_state.pop(cid, None)
        _cancel_td_timeout(context, cid)

async def _send_question_to_loser(q_or_msg, context, cid, state):
    state["phase"] = "waiting_loser_response"
    l_id      = state["l_id"]
    l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]

    t_will   = cb_pack("td_will",   l=l_id)
    t_refuse = cb_pack("td_refuse", l=l_id)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ سوف أجيب | I'll answer",  callback_data=t_will),
        InlineKeyboardButton("❌ لا أريد | I refuse",       callback_data=t_refuse),
    ]])
    text = (
        f"📩 <b>{l_mention}</b>، وصلك من | received from <b>{state['w_name']}</b>:\n\n"
        f"❓ <i>{state['question']}</i>\n\n"
        f"⚠️ الرفض = تقييد 30 دقيقة! | Refusal = 30 min restriction!"
    )
    if hasattr(q_or_msg, "edit_message_text"):
        await q_or_msg.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        sent = await context.bot.send_message(cid, text, reply_markup=kb, parse_mode="HTML")
        track_bot_msg(cid, sent.message_id)
        state["bot_msgs"].append(sent.message_id)

# ─────────────────────────────────────────
# TRUTH/DARE REPLY HANDLER / معالج ردود الصراحة
# ─────────────────────────────────────────
async def handle_td_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if the message was consumed by TD logic / يُرجع True لو الرسالة استُهلكت"""
    msg  = update.message
    if not msg or not msg.text:
        return False

    cid   = msg.chat_id
    user  = msg.from_user
    state = truth_dare_state.get(cid)
    if not state:
        return False

    # Winner writes custom question
    if state["phase"] == "waiting_question" and user.id == state["w_id"]:
        state["question"] = msg.text
        await _send_question_to_loser(msg, context, cid, state)
        return True

    # Loser answers
    if state["phase"] == "waiting_answer" and user.id == state["l_id"]:
        state["phase"] = "waiting_verdict"
        w_id      = state["w_id"]
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]

        t_yes = cb_pack("td_yes", w=w_id)
        t_no  = cb_pack("td_no",  w=w_id)

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ قبلت | Accepted",             callback_data=t_yes),
            InlineKeyboardButton("❌ لم يجب بصدق | Not honest",   callback_data=t_no),
        ]])
        sent = await msg.reply_text(
            f"💬 <b>{l_mention}</b> أجاب | answered:\n<i>{msg.text}</i>\n\n"
            f"يا <b>{state['w_name']}</b>، هل قبلت إجابته؟ | Did you accept the answer?",
            reply_markup=kb, parse_mode="HTML"
        )
        track_bot_msg(cid, sent.message_id)
        state["bot_msgs"].append(sent.message_id)
        return True

    return False

# ─────────────────────────────────────────
# JOBS / المهام المجدولة
# ─────────────────────────────────────────
async def _mute_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    cid, l_id, mins = d["cid"], d["l_id"], d["mins"]
    state = truth_dare_state.get(cid)

    # If loser changed their mind and is now answering, skip mute
    if state and state.get("phase") == "waiting_answer":
        return

    l_mention = f"@{state['l_username']}" if state and state.get("l_username") else "اللاعب | Player"
    ok = await mute(context, cid, l_id, mins)
    if ok:
        sent = await context.bot.send_message(
            cid,
            f"🔒 تم تقييد | Restricted: <b>{l_mention}</b> {mins} دقيقة | minutes.",
            parse_mode="HTML"
        )
        track_bot_msg(cid, sent.message_id)
        context.job_queue.run_once(
            _unmute_job, mins * 60,
            data={"cid": cid, "uid": l_id},
            name=f"unmute_{cid}_{l_id}"
        )
    truth_dare_state.pop(cid, None)

async def _unmute_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await unmute(context, d["cid"], d["uid"])

# ─────────────────────────────────────────
# COMMANDS / الأوامر
# ─────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sent = await update.message.reply_text(
        "👋 <b>بوت ألعاب المجموعة | Group Games Bot</b>\n\n"
        "🎮 <b>كيف تلعب | How to play:</b>\n"
        "منشن أي شخص → تظهر خيارات التحدي\n"
        "Mention someone → challenge options appear\n\n"
        "🎯 <b>الألعاب | Games:</b>\n"
        "• ❌⭕ XO\n\n"
        "🏆 بعد كل لعبة → صراحة أو جرأة للخاسر!\n"
        "After each game → Truth or Dare for the loser!\n\n"
        "📊 /scores — الترتيب | Leaderboard\n"
        "❌ /cancel — إلغاء اللعبة | Cancel game",
        parse_mode="HTML"
    )
    track_bot_msg(update.effective_chat.id, sent.message_id)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sent = await update.message.reply_text(
        "📖 <b>تعليمات البوت | Bot Instructions:</b>\n\n"
        "1️⃣ منشن شخص في المجموعة | Mention someone\n"
        "2️⃣ اضغط 'تحدي' | Press 'Challenge'\n"
        "3️⃣ اختر XO | Choose XO\n"
        "4️⃣ انتظر قبول الشخص | Wait for acceptance\n"
        "5️⃣ العبا بالتناوب | Play in turns\n"
        "6️⃣ الخاسر يختار صراحة أم جرأة | Loser picks Truth or Dare\n\n"
        "⚠️ <b>الألعاب تنتهي بعد 5 دقائق | Games end after 5 min</b>\n"
        "🗑️ <b>رسائل البوت تُحذف بعد 10 دقائق | Bot messages deleted after 10 min</b>",
        parse_mode="HTML"
    )
    track_bot_msg(update.effective_chat.id, sent.message_id)

async def cmd_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid   = update.effective_chat.id
    board = scores.get(cid)
    if not board:
        sent = await update.message.reply_text(
            "🏆 لا يوجد انتصارات بعد! | No wins yet!"
        )
        track_bot_msg(cid, sent.message_id)
        return

    sorted_sc = sorted(board.items(), key=lambda x: x[1], reverse=True)
    lines = ["🏆 <b>الترتيب | Leaderboard:</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, wins) in enumerate(sorted_sc[:10]):
        try:
            m = await context.bot.get_chat_member(cid, uid)
            n = uname(m.user)
        except TelegramError:
            n = str(uid)
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {n} — <b>{wins}</b> انتصار | win(s)")

    sent = await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    track_bot_msg(cid, sent.message_id)

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid  = update.effective_chat.id
    user = update.effective_user

    g = active_games.get(cid)
    if g and user.id in (g["p1"], g["p2"]):
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        sent = await update.message.reply_text(
            "🚫 تم إلغاء اللعبة الحالية. | Current game cancelled."
        )
        track_bot_msg(cid, sent.message_id)
        return

    td = truth_dare_state.get(cid)
    if td and user.id in (td["w_id"], td["l_id"]):
        truth_dare_state.pop(cid, None)
        _cancel_td_timeout(context, cid)
        sent = await update.message.reply_text(
            "🚫 تم إلغاء جلسة الصراحة/الجرأة. | Truth/Dare session cancelled."
        )
        track_bot_msg(cid, sent.message_id)
        return

    sent = await update.message.reply_text(
        "لا يوجد شيء لإلغائه. | Nothing to cancel."
    )
    track_bot_msg(cid, sent.message_id)

# ─────────────────────────────────────────
# MAIN / التشغيل
# ─────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).build()

    # Commands / الأوامر
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("scores", cmd_scores))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Welcome new members / ترحيب بالأعضاء
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))

    # Mention callbacks / callbacks المنشن
    mention_prefixes = "|".join(["mg_", "mr_", "px_", "ac_", "rj_"])
    app.add_handler(CallbackQueryHandler(on_mention_cb, pattern=rf"^({mention_prefixes})"))

    # XO callbacks
    app.add_handler(CallbackQueryHandler(on_xo, pattern=r"^xo_"))

    # Truth/Dare callbacks / callbacks الصراحة والجرأة
    app.add_handler(CallbackQueryHandler(on_td_cb, pattern=r"^td_"))

    # Text messages / الرسائل النصية
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    # Job: purge old bot messages every 2 min / تنظيف الرسائل القديمة كل دقيقتين
    app.job_queue.run_repeating(purge_old_bot_messages, interval=120, first=120)

    print("🤖 البوت يعمل... | Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
