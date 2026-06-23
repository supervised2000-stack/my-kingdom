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
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.error import BadRequest, TelegramError

# ─────────────────────────────────────────
# التوكن - استخدم متغير بيئة دائماً
# ─────────────────────────────────────────
TOKEN = os.getenv("BOT_TOKEN", "REPLACE_WITH_YOUR_NEW_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# الذاكرة
# ─────────────────────────────────────────
active_games: dict = {}          # chat_id → game
truth_dare_state: dict = {}      # chat_id → state
pending_mentions: dict = {}      # (chat_id, msg_id) → data
scores: dict = defaultdict(lambda: defaultdict(int))  # chat_id → user_id → wins
game_timeouts: dict = {}         # chat_id → job_name

GAME_TIMEOUT_SECONDS = 300       # 5 دقائق قبل انتهاء اللعبة تلقائياً
TD_TIMEOUT_SECONDS   = 120       # دقيقتان للإجابة

# ─────────────────────────────────────────
# أسئلة صراحة وتحديات جرأة (ذكاء اصطناعي محلي)
# ─────────────────────────────────────────
TRUTH_QUESTIONS = [
    "ما أكثر شيء تكذب فيه على أهلك؟",
    "من الشخص الذي تكرهه في المجموعة؟ 😏",
    "ما أحرج موقف مررت فيه؟",
    "هل سرقت شيئاً من قبل؟ ماذا؟",
    "ما أكبر سر تخفيه الآن؟",
    "من الشخص الذي تحبه أكثر في المجموعة؟",
    "ما الشيء الذي تتمنى أن لا يعرفه أهلك عنك؟",
    "هل أنت مغرم بأحد الآن؟ من هو؟",
    "ما أغبى قرار اتخذته في حياتك؟",
    "ما الشيء الذي تتمنى تغييره في نفسك؟",
]

DARE_CHALLENGES = [
    "اكتب رسالة إعجاب لأي شخص في المجموعة الآن 💌",
    "غيّر اسمك في المجموعة لمدة ساعة لـ '👶 الخاسر'",
    "أرسل أول صورة في معرض هاتفك 📸",
    "قلد أي عضو في المجموعة بأسلوب كتابته",
    "اكتب 'أنا خسرت!' بالحروف الكبيرة 10 مرات",
    "أرسل أحرج إيموجي موجود في هاتفك",
    "اكتب جملة بالإنجليزي وترجمها حرفياً بالعربي",
    "اكتب مدحاً للفائز عليك بـ 3 أسطر 🎤",
    "أرسل صوتية تقول فيها 'الفائز أذكى مني' 🎙️",
    "غيّر صورتك في المجموعة لمدة 30 دقيقة لأي صورة مضحكة",
]

# ─────────────────────────────────────────
# مساعد: اسم المستخدم
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
# إنشاء مفتاح callback آمن (≤ 64 بايت)
# ─────────────────────────────────────────
_cb_store: dict = {}   # token → full data

def cb_pack(prefix: str, **kwargs) -> str:
    """يخزّن البيانات ويعيد مفتاح قصير."""
    token = f"{prefix}_{random.randint(10000,99999)}"
    _cb_store[token] = {"prefix": prefix, **kwargs}
    return token[:64]

def cb_get(token: str) -> dict | None:
    return _cb_store.get(token)

def cb_clear(token: str):
    _cb_store.pop(token, None)

# ─────────────────────────────────────────
# XO
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
    for a,b,c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if all(board) else None

# ─────────────────────────────────────────
# Connect 4
# ─────────────────────────────────────────
def c4_new_board():
    return [[None]*7 for _ in range(6)]

def c4_drop(board, col, player) -> int:
    for r in range(5, -1, -1):
        if board[r][col] is None:
            board[r][col] = player
            return r
    return -1

def c4_winner(board, player) -> bool:
    # أفقي
    for r in range(6):
        for c in range(4):
            if all(board[r][c+i] == player for i in range(4)):
                return True
    # عمودي
    for r in range(3):
        for c in range(7):
            if all(board[r+i][c] == player for i in range(4)):
                return True
    # قطري ↘
    for r in range(3):
        for c in range(4):
            if all(board[r+i][c+i] == player for i in range(4)):
                return True
    # قطري ↙
    for r in range(3, 6):
        for c in range(4):
            if all(board[r-i][c+i] == player for i in range(4)):
                return True
    return False

def c4_full(board) -> bool:
    return all(board[0][c] is not None for c in range(7))

def c4_render(board, p1_id, p2_id, done=False) -> InlineKeyboardMarkup:
    sym = {p1_id: "🔴", p2_id: "🔵"}
    rows = []
    for r in range(6):
        row = []
        for c in range(7):
            v = board[r][c]
            label = sym.get(v, "⬜") if v else "⬜"
            row.append(InlineKeyboardButton(label, callback_data="c4_taken"))
        rows.append(row)
    if not done:
        rows.append([InlineKeyboardButton(f"{i+1}", callback_data=f"c4_col_{i}") for i in range(7)])
    return InlineKeyboardMarkup(rows)

# ─────────────────────────────────────────
# مساعد: تقييد / رفع التقييد
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
# الأحداث: رسائل عامة
# ─────────────────────────────────────────
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    # فحص الردود على صراحة/جرأة
    await handle_td_reply(update, context)

    # فحص المنشن
    if not msg.entities:
        return

    chat_id = msg.chat_id
    sender  = msg.from_user

    for ent in msg.entities:
        if ent.type != "mention":
            continue
        mentioned = msg.text[ent.offset+1 : ent.offset+ent.length]
        if mentioned.lower() == (context.bot.username or "").lower():
            continue

        # تجنّب المنشن الذاتي
        try:
            target_chat = await context.bot.get_chat(f"@{mentioned}")
            t_id   = target_chat.id
            t_name = target_chat.full_name or mentioned
        except TelegramError:
            t_id, t_name = None, mentioned

        if t_id == sender.id:
            continue

        # callback_data آمن
        token_game  = cb_pack("mg",  c=sender.id, t=t_id, u=mentioned)
        token_reply = cb_pack("mr",  c=sender.id, t=t_id)

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚔️ تحدي للعب",  callback_data=token_game),
            InlineKeyboardButton("👋 رد عليه",    callback_data=token_reply),
        ]])

        sent = await msg.reply_text(
            f"👀 <b>{uname(sender)}</b> ذكر <b>@{mentioned}</b>\n\nماذا تريد؟",
            reply_markup=kb,
            parse_mode="HTML"
        )
        pending_mentions[(chat_id, sent.message_id)] = {
            "challenger_id":   sender.id,
            "challenger_name": uname(sender),
            "t_id":   t_id,
            "t_username": mentioned,
            "t_name": t_name,
            "token_game":  token_game,
            "token_reply": token_reply,
        }
        return   # منشن واحد يكفي

# ─────────────────────────────────────────
# الأحداث: callbacks المنشن
# ─────────────────────────────────────────
async def on_mention_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    cid  = q.message.chat_id
    data = cb_get(q.data)

    if not data:
        await q.answer("انتهت صلاحية هذا الزر ⌛", show_alert=True)
        return

    prefix = data["prefix"]

    # ── اختيار اللعب ──
    if prefix == "mg":
        if user.id != data["c"]:
            await q.answer("هذا الخيار ليس لك 🚫", show_alert=True)
            return

        t_xo = cb_pack("px", c=data["c"], t=data["t"], u=data["u"], g="xo")
        t_c4 = cb_pack("px", c=data["c"], t=data["t"], u=data["u"], g="c4")

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("❌⭕ XO",      callback_data=t_xo),
            InlineKeyboardButton("🔴 Connect4", callback_data=t_c4),
        ]])
        await q.edit_message_text(
            f"⚔️ <b>{uname(user)}</b> يتحدى <b>@{data['u']}</b>\n\nاختر اللعبة:",
            reply_markup=kb, parse_mode="HTML"
        )

    # ── رد عليه ──
    elif prefix == "mr":
        if user.id != data["c"]:
            await q.answer("هذا الخيار ليس لك 🚫", show_alert=True)
            return
        await q.edit_message_text("👋 تم! رد عليه مباشرة في المجموعة.")

    # ── اختيار نوع اللعبة → عرض قبول/رفض ──
    elif prefix == "px":
        if user.id != data["c"]:
            await q.answer("هذا الخيار ليس لك 🚫", show_alert=True)
            return

        gname = "❌⭕ XO" if data["g"] == "xo" else "🔴 Connect4"
        t_acc = cb_pack("ac", c=data["c"], t=data["t"], u=data["u"], g=data["g"])
        t_rej = cb_pack("rj", c=data["c"], u=data["u"])

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ قبول",  callback_data=t_acc),
            InlineKeyboardButton("❌ رفض",  callback_data=t_rej),
        ]])
        await q.edit_message_text(
            f"🎮 <b>{uname(user)}</b> يتحداك على {gname}!\n\n"
            f"@{data['u']} هل تقبل؟",
            reply_markup=kb, parse_mode="HTML"
        )

    # ── قبول ──
    elif prefix == "ac":
        if data["t"] and user.id != data["t"]:
            await q.answer("هذه الدعوة ليست لك 🚫", show_alert=True)
            return

        await q.edit_message_text("✅ تم القبول! تبدأ اللعبة الآن...")
        if data["g"] == "xo":
            await game_start_xo(context, cid, data["c"], user.id, uname(user), data["u"])
        else:
            await game_start_c4(context, cid, data["c"], user.id, uname(user), data["u"])

    # ── رفض ──
    elif prefix == "rj":
        await q.edit_message_text(f"❌ @{data['u']} رفض التحدي!")

# ─────────────────────────────────────────
# بدء XO
# ─────────────────────────────────────────
async def game_start_xo(context, cid, p1_id, p2_id, p2_name, p2_username):
    try:
        info = await context.bot.get_chat_member(cid, p1_id)
        p1_name = uname(info.user)
    except TelegramError:
        p1_name = "اللاعب 1"

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
        f"❌⭕ <b>لعبة XO</b>\n\n"
        f"❌ {p1_name}  vs  ⭕ {p2_name}\n\n"
        f"🎯 دور: <b>{p1_name}</b> ❌"
    )
    sent = await context.bot.send_message(cid, text,
        reply_markup=xo_render(board, p1_id, p2_id), parse_mode="HTML")
    active_games[cid]["msg_id"] = sent.message_id
    _schedule_game_timeout(context, cid)

# ─────────────────────────────────────────
# بدء Connect4
# ─────────────────────────────────────────
async def game_start_c4(context, cid, p1_id, p2_id, p2_name, p2_username):
    try:
        info = await context.bot.get_chat_member(cid, p1_id)
        p1_name = uname(info.user)
    except TelegramError:
        p1_name = "اللاعب 1"

    board = c4_new_board()
    active_games[cid] = {
        "type": "c4", "board": board,
        "p1": p1_id, "p2": p2_id,
        "p1_name": p1_name, "p2_name": p2_name,
        "p2_username": p2_username,
        "turn": p1_id, "msg_id": None,
        "started": datetime.now().isoformat()
    }

    text = (
        f"🔴 <b>Connect4</b>\n\n"
        f"🔴 {p1_name}  vs  🔵 {p2_name}\n\n"
        f"🎯 دور: <b>{p1_name}</b> 🔴"
    )
    sent = await context.bot.send_message(cid, text,
        reply_markup=c4_render(board, p1_id, p2_id), parse_mode="HTML")
    active_games[cid]["msg_id"] = sent.message_id
    _schedule_game_timeout(context, cid)

# ─────────────────────────────────────────
# Timeout للألعاب
# ─────────────────────────────────────────
def _schedule_game_timeout(context, cid):
    job_name = f"gtout_{cid}"
    # ألغِ القديم لو موجود
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
    context.job_queue.run_once(
        _game_timeout_job, GAME_TIMEOUT_SECONDS,
        data={"cid": cid}, name=job_name
    )

async def _game_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    cid = context.job.data["cid"]
    g   = active_games.pop(cid, None)
    if not g:
        return
    try:
        await context.bot.edit_message_text(
            "⏰ انتهى وقت اللعبة! لا فائز.",
            chat_id=cid, message_id=g["msg_id"]
        )
    except TelegramError:
        pass

# ─────────────────────────────────────────
# Callback XO
# ─────────────────────────────────────────
async def on_xo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    cid  = q.message.chat_id
    data = q.data

    if "taken" in data:
        await q.answer("هذه الخانة محجوزة 🚫")
        return

    g = active_games.get(cid)
    if not g or g["type"] != "xo":
        await q.answer("لا توجد لعبة نشطة")
        return
    if user.id not in (g["p1"], g["p2"]):
        await q.answer("اللعبة ليست لك 🚫", show_alert=True)
        return
    if user.id != g["turn"]:
        await q.answer("ليس دورك ⏳", show_alert=True)
        return

    idx = int(data.split("_")[-1])
    if g["board"][idx]:
        await q.answer("الخانة محجوزة 🚫")
        return

    g["board"][idx] = user.id
    winner = xo_winner(g["board"])
    p1, p2 = g["p1"], g["p2"]
    p1n, p2n = g["p1_name"], g["p2_name"]

    if winner == "draw":
        await q.edit_message_text(
            f"❌⭕ <b>XO — تعادل!</b> 🤝\n\n{p1n}  vs  {p2n}",
            reply_markup=xo_render(g["board"], p1, p2), parse_mode="HTML")
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
            f"❌⭕ <b>XO — انتهت!</b>\n\n"
            f"🏆 فاز: <b>{w_name}</b>\n"
            f"💀 خسر: <b>{l_name}</b>\n\n"
            f"🏅 {w_name} لديه الآن {scores[cid][w_id]} انتصار",
            reply_markup=xo_render(g["board"], p1, p2), parse_mode="HTML")
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        await td_start(context, cid, w_id, w_name, l_id, l_name, l_user)
        return

    g["turn"] = p2 if user.id == p1 else p1
    nxt_name  = p2n if g["turn"] == p2 else p1n
    symbol    = "⭕" if g["turn"] == p2 else "❌"
    await q.edit_message_text(
        f"❌⭕ <b>XO</b>\n\n❌ {p1n}  vs  ⭕ {p2n}\n\n🎯 دور: <b>{nxt_name}</b> {symbol}",
        reply_markup=xo_render(g["board"], p1, p2), parse_mode="HTML")
    _schedule_game_timeout(context, cid)

# ─────────────────────────────────────────
# Callback Connect4
# ─────────────────────────────────────────
async def on_c4(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    cid  = q.message.chat_id
    data = q.data

    if data == "c4_taken":
        await q.answer()
        return

    g = active_games.get(cid)
    if not g or g["type"] != "c4":
        await q.answer("لا توجد لعبة نشطة")
        return
    if user.id not in (g["p1"], g["p2"]):
        await q.answer("اللعبة ليست لك 🚫", show_alert=True)
        return
    if user.id != g["turn"]:
        await q.answer("ليس دورك ⏳", show_alert=True)
        return

    col = int(data.split("_")[-1])
    row = c4_drop(g["board"], col, user.id)
    if row == -1:
        await q.answer("العمود ممتلئ 🚫")
        return

    p1, p2 = g["p1"], g["p2"]
    p1n, p2n = g["p1_name"], g["p2_name"]

    if c4_winner(g["board"], user.id):
        w_id   = user.id
        w_name = p1n if w_id == p1 else p2n
        l_id   = p2  if w_id == p1 else p1
        l_name = p2n if w_id == p1 else p1n
        l_user = g["p2_username"] if w_id == p1 else None

        scores[cid][w_id] += 1
        await q.edit_message_text(
            f"🔴 <b>Connect4 — انتهت!</b>\n\n"
            f"🏆 فاز: <b>{w_name}</b>\n"
            f"💀 خسر: <b>{l_name}</b>\n\n"
            f"🏅 {w_name} لديه الآن {scores[cid][w_id]} انتصار",
            reply_markup=c4_render(g["board"], p1, p2, True), parse_mode="HTML")
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        await td_start(context, cid, w_id, w_name, l_id, l_name, l_user)
        return

    if c4_full(g["board"]):
        await q.edit_message_text(
            f"🔴 <b>Connect4 — تعادل!</b> 🤝\n\n{p1n}  vs  {p2n}",
            reply_markup=c4_render(g["board"], p1, p2, True), parse_mode="HTML")
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        return

    g["turn"] = p2 if user.id == p1 else p1
    nxt_name  = p2n if g["turn"] == p2 else p1n
    symbol    = "🔵" if g["turn"] == p2 else "🔴"
    await q.edit_message_text(
        f"🔴 <b>Connect4</b>\n\n🔴 {p1n}  vs  🔵 {p2n}\n\n🎯 دور: <b>{nxt_name}</b> {symbol}",
        reply_markup=c4_render(g["board"], p1, p2), parse_mode="HTML")
    _schedule_game_timeout(context, cid)

def _cancel_game_timeout(context, cid):
    for job in context.job_queue.get_jobs_by_name(f"gtout_{cid}"):
        job.schedule_removal()

# ─────────────────────────────────────────
# صراحة أو جرأة
# ─────────────────────────────────────────
async def td_start(context, cid, w_id, w_name, l_id, l_name, l_username):
    l_mention = f"@{l_username}" if l_username else l_name

    t_truth = cb_pack("td_pick", l=l_id, ch="truth")
    t_dare  = cb_pack("td_pick", l=l_id, ch="dare")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗣️ صراحة", callback_data=t_truth),
        InlineKeyboardButton("😈 جرأة",  callback_data=t_dare),
    ]])
    sent = await context.bot.send_message(
        cid,
        f"🏆 فاز <b>{w_name}</b> على <b>{l_mention}</b>!\n\n"
        f"يا {l_mention}، اختر عقوبتك:",
        reply_markup=kb, parse_mode="HTML"
    )
    truth_dare_state[cid] = {
        "phase": "choosing",
        "w_id": w_id, "w_name": w_name,
        "l_id": l_id, "l_name": l_name,
        "l_username": l_username,
        "choice": None, "question": None,
        "bot_msgs": [sent.message_id],
    }
    # timeout للصراحة/الجرأة
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
    await context.bot.send_message(
        cid,
        f"⏰ انتهى وقت الاختيار!\n"
        f"<b>{l_mention}</b> لم يختر → تم تجاهل العقوبة.",
        parse_mode="HTML"
    )

async def on_td_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    cid  = q.message.chat_id
    data = cb_get(q.data)

    if not data:
        await q.answer("انتهت صلاحية هذا الزر ⌛", show_alert=True)
        return

    state = truth_dare_state.get(cid)
    if not state:
        await q.answer("لا يوجد نظام نشط")
        return

    prefix = data["prefix"]

    # ── الخاسر اختار صراحة أو جرأة ──
    if prefix == "td_pick":
        if user.id != data["l"]:
            await q.answer("هذا الاختيار ليس لك 🚫", show_alert=True)
            return

        choice = data["ch"]
        state["choice"] = choice
        state["phase"]  = "waiting_question"

        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]
        label = "صراحة 🗣️" if choice == "truth" else "جرأة 😈"

        # اقتراح تلقائي ذكي
        auto = random.choice(TRUTH_QUESTIONS if choice == "truth" else DARE_CHALLENGES)

        t_auto   = cb_pack("td_auto",   w=state["w_id"], l=state["l_id"], q=auto)
        t_custom = cb_pack("td_custom", w=state["w_id"])

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎲 استخدم هذا", callback_data=t_auto),
            InlineKeyboardButton("✏️ اكتب سؤالك", callback_data=t_custom),
        ]])
        await q.edit_message_text(
            f"✅ <b>{l_mention}</b> اختار <b>{label}</b>!\n\n"
            f"يا <b>{state['w_name']}</b>، اقتراح ذكي:\n"
            f"❓ <i>{auto}</i>",
            reply_markup=kb, parse_mode="HTML"
        )

    # ── الفائز وافق على الاقتراح التلقائي ──
    elif prefix == "td_auto":
        if user.id != data["w"]:
            await q.answer("هذا الاختيار ليس لك 🚫", show_alert=True)
            return

        state["question"] = data["q"]
        await _send_question_to_loser(q, context, cid, state)

    # ── الفائز يريد يكتب سؤاله ──
    elif prefix == "td_custom":
        if user.id != data["w"]:
            await q.answer("هذا الاختيار ليس لك 🚫", show_alert=True)
            return
        state["phase"] = "waiting_question"
        await q.edit_message_text(
            f"✏️ يا <b>{state['w_name']}</b>، اكتب سؤالك أو تحديك الآن:",
            parse_mode="HTML"
        )

    # ── الخاسر سيجيب ──
    elif prefix == "td_will":
        if user.id != data["l"]:
            await q.answer("هذا الاختيار ليس لك 🚫", show_alert=True)
            return
        state["phase"] = "waiting_answer"
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]
        await q.edit_message_text(
            f"✍️ <b>{l_mention}</b> سيجيب الآن...\n\nاكتب إجابتك في المجموعة:",
            parse_mode="HTML"
        )

    # ── الخاسر رفض ──
    elif prefix == "td_refuse":
        if user.id != data["l"]:
            await q.answer("هذا الاختيار ليس لك 🚫", show_alert=True)
            return

        l_id      = state["l_id"]
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]

        t_back = cb_pack("td_will", l=l_id)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 تراجعت! سأجيب", callback_data=t_back)
        ]])
        await q.edit_message_text(
            f"⚠️ <b>{l_mention}</b> رفض الإجابة!\n"
            f"سيُقيَّد من المجموعة <b>30 دقيقة</b> 🔴\n\n"
            f"تراجع وأجب قبل 30 ثانية:",
            reply_markup=kb, parse_mode="HTML"
        )
        context.job_queue.run_once(
            _mute_job, 30,
            data={"cid": cid, "l_id": l_id, "mins": 30},
            name=f"mute_{cid}_{l_id}"
        )

    # ── الفائز قبل الإجابة ──
    elif prefix == "td_yes":
        if user.id != data["w"]:
            await q.answer("هذا الاختيار ليس لك 🚫", show_alert=True)
            return
        await q.edit_message_text("✅ تم قبول الإجابة! اللعبة انتهت 🎉", parse_mode="HTML")
        truth_dare_state.pop(cid, None)
        _cancel_td_timeout(context, cid)

    # ── الفائز رفض الإجابة ──
    elif prefix == "td_no":
        if user.id != data["w"]:
            await q.answer("هذا الاختيار ليس لك 🚫", show_alert=True)
            return
        l_id      = state["l_id"]
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]
        await q.edit_message_text(
            f"❌ الإجابة مرفوضة!\n⚠️ <b>{l_mention}</b> يُقيَّد <b>10 دقائق</b>",
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
        InlineKeyboardButton("✅ سوف أجيب",  callback_data=t_will),
        InlineKeyboardButton("❌ لا أريد",    callback_data=t_refuse),
    ]])
    text = (
        f"📩 <b>{l_mention}</b>، وصلك من <b>{state['w_name']}</b>:\n\n"
        f"❓ <i>{state['question']}</i>\n\n"
        f"⚠️ الرفض = تقييد 30 دقيقة!"
    )
    if hasattr(q_or_msg, "edit_message_text"):
        sent = await q_or_msg.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        sent = await context.bot.send_message(cid, text, reply_markup=kb, parse_mode="HTML")
        state["bot_msgs"].append(sent.message_id)

# ─────────────────────────────────────────
# ردود الصراحة/الجرأة في المجموعة
# ─────────────────────────────────────────
async def handle_td_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.message
    if not msg or not msg.text:
        return
    cid  = msg.chat_id
    user = msg.from_user
    state = truth_dare_state.get(cid)
    if not state:
        return

    # الفائز يكتب سؤاله
    if state["phase"] == "waiting_question" and user.id == state["w_id"]:
        state["question"] = msg.text
        await _send_question_to_loser(msg, context, cid, state)

    # الخاسر يجيب
    elif state["phase"] == "waiting_answer" and user.id == state["l_id"]:
        state["phase"] = "waiting_verdict"
        w_id      = state["w_id"]
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]

        t_yes = cb_pack("td_yes", w=w_id)
        t_no  = cb_pack("td_no",  w=w_id)

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ قبلت",           callback_data=t_yes),
            InlineKeyboardButton("❌ لم يجب بصدق",   callback_data=t_no),
        ]])
        sent = await msg.reply_text(
            f"💬 <b>{l_mention}</b> أجاب:\n<i>{msg.text}</i>\n\n"
            f"يا <b>{state['w_name']}</b>، هل قبلت إجابته؟",
            reply_markup=kb, parse_mode="HTML"
        )
        state["bot_msgs"].append(sent.message_id)

# ─────────────────────────────────────────
# Jobs
# ─────────────────────────────────────────
async def _mute_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    cid, l_id, mins = d["cid"], d["l_id"], d["mins"]
    state = truth_dare_state.get(cid)
    # لو الخاسر تراجع وقبل الإجابة، لا تقيّده
    if state and state.get("phase") == "waiting_answer":
        return
    l_mention = f"@{state['l_username']}" if state and state.get("l_username") else "اللاعب"
    ok = await mute(context, cid, l_id, mins)
    if ok:
        await context.bot.send_message(
            cid,
            f"🔒 تم تقييد <b>{l_mention}</b> {mins} دقيقة بسبب الرفض.",
            parse_mode="HTML"
        )
        context.job_queue.run_once(
            _unmute_job, mins*60,
            data={"cid": cid, "uid": l_id},
            name=f"unmute_{cid}_{l_id}"
        )
    truth_dare_state.pop(cid, None)

async def _unmute_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await unmute(context, d["cid"], d["uid"])

def _cancel_td_timeout(context, cid):
    for job in context.job_queue.get_jobs_by_name(f"tdtout_{cid}"):
        job.schedule_removal()

# ─────────────────────────────────────────
# أوامر
# ─────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>بوت ألعاب المجموعة</b>\n\n"
        "🎮 <b>كيف تلعب:</b>\n"
        "منشن أي شخص → تظهر خيارات التحدي\n\n"
        "🎯 <b>الألعاب:</b>\n"
        "• ❌⭕ XO\n"
        "• 🔴 Connect4\n\n"
        "🏆 بعد كل لعبة → صراحة أو جرأة للخاسر!\n\n"
        "📊 /scores — عرض الترتيب",
        parse_mode="HTML"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>تعليمات البوت:</b>\n\n"
        "1️⃣ منشن شخص في المجموعة\n"
        "2️⃣ اختر تحدي للعب\n"
        "3️⃣ اختر XO أو Connect4\n"
        "4️⃣ انتظر قبول الشخص\n"
        "5️⃣ العبا بالتناوب\n"
        "6️⃣ الخاسر يختار صراحة أم جرأة\n\n"
        "⚠️ <b>الألعاب تنتهي تلقائياً بعد 5 دقائق</b>",
        parse_mode="HTML"
    )

async def cmd_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    board = scores.get(cid)
    if not board:
        await update.message.reply_text("🏆 لا يوجد انتصارات بعد!")
        return

    sorted_sc = sorted(board.items(), key=lambda x: x[1], reverse=True)
    lines = ["🏆 <b>ترتيب الانتصارات:</b>\n"]
    medals = ["🥇","🥈","🥉"]
    for i, (uid, wins) in enumerate(sorted_sc[:10]):
        try:
            m = await context.bot.get_chat_member(cid, uid)
            n = uname(m.user)
        except TelegramError:
            n = str(uid)
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {n} — <b>{wins}</b> انتصار")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    user = update.effective_user

    g = active_games.get(cid)
    if g and user.id in (g["p1"], g["p2"]):
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        await update.message.reply_text("🚫 تم إلغاء اللعبة الحالية.")
        return

    td = truth_dare_state.get(cid)
    if td and user.id in (td["w_id"], td["l_id"]):
        truth_dare_state.pop(cid, None)
        _cancel_td_timeout(context, cid)
        await update.message.reply_text("🚫 تم إلغاء جلسة الصراحة/الجرأة.")
        return

    await update.message.reply_text("لا يوجد شيء لإلغائه.")

# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("scores", cmd_scores))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # callbacks المنشن (prefix متنوعة)
    mention_prefixes = "|".join(["mg_","mr_","px_","ac_","rj_"])
    app.add_handler(CallbackQueryHandler(on_mention_cb,
        pattern=rf"^({mention_prefixes})"))

    # XO
    app.add_handler(CallbackQueryHandler(on_xo,  pattern=r"^xo_"))
    # Connect4
    app.add_handler(CallbackQueryHandler(on_c4,  pattern=r"^c4_"))
    # صراحة/جرأة
    app.add_handler(CallbackQueryHandler(on_td_cb, pattern=r"^td_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    print("🤖 البوت يعمل...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
