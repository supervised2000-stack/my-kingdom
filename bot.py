import logging
import asyncio
import os
import json
import random
import time
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.error import BadRequest, TelegramError

# ═══════════════════════════════════════════════════════
#  ⚙️  الإعدادات
# ═══════════════════════════════════════════════════════
TOKEN = os.getenv("BOT_TOKEN", "REPLACE_WITH_YOUR_TOKEN")

GAME_TIMEOUT_SECONDS = 300   # 5 دقائق
TD_TIMEOUT_SECONDS   = 180   # 3 دقائق للصراحة/الجرأة
INVITE_TIMEOUT       = 60    # دقيقة للقبول/الرفض

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
#  🧠  الذاكرة
# ═══════════════════════════════════════════════════════
active_games:     dict = {}
truth_dare_state: dict = {}
pending_mentions: dict = {}
scores:           dict = defaultdict(lambda: defaultdict(int))
losses:           dict = defaultdict(lambda: defaultdict(int))
streaks:          dict = defaultdict(lambda: defaultdict(int))   # سلسلة الانتصارات
_cb_store:        dict = {}
game_stats:       dict = defaultdict(lambda: defaultdict(lambda: {"wins":0,"losses":0,"draws":0}))

# ═══════════════════════════════════════════════════════
#  ❓  بنك الأسئلة والتحديات (موسّع)
# ═══════════════════════════════════════════════════════
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
    "ما آخر كذبة قلتها؟",
    "لو تختار شخصاً في المجموعة كصديق مدى الحياة، من تختار ولماذا؟",
    "ما أكثر شيء تخجل منه في ماضيك؟",
    "هل خنت صديقاً يوماً ما؟ كيف؟",
    "ما أكثر عادة سيئة لديك؟",
    "لو تغير اسمك، ماذا ستختار؟ ولماذا؟",
    "ما الشيء الذي يزعجك في أحد أعضاء المجموعة؟",
    "هل حدث أن بكيت من فيلم أو مسلسل؟ أيّه؟",
    "ما آخر رسالة خاصة أرسلتها ولا تريد أحداً يراها؟",
    "لو تختفي لأسبوع، أين ستذهب؟",
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
    "اكتب قصيدة من 4 أسطر تمدح فيها الفائز 📜",
    "أرسل أغرب ستيكر في هاتفك 🎭",
    "اكتب 5 أشياء تحبها في شخص آخر بالمجموعة 💬",
    "غنّ مقطعاً من أغنية وأرسله صوتياً 🎵",
    "اكتب سيرتك الذاتية بأسلوب كوميدي في 3 أسطر 😂",
    "أرسل تعليق إطراء على أي منشور قديم لعضو بالمجموعة",
    "اكتب رسالة اعتذار لشخص ظلمته سابقاً (حقيقي أو خيالي)",
    "أرسل صورة سيلفي الآن بدون فلاتر 🤳",
    "اكتب 3 أشياء تعترف بها لأول مرة أمام المجموعة 😳",
    "قلّد صوت حيوان بالصوتيات 🦁",
]

# ═══════════════════════════════════════════════════════
#  🛠️  مساعدات عامة
# ═══════════════════════════════════════════════════════
def uname(user) -> str:
    if user.full_name:
        return user.full_name
    if user.username:
        return f"@{user.username}"
    return str(user.id)

def mention_html(user_id: int, display: str) -> str:
    return f'<a href="tg://user?id={user_id}">{display}</a>'

def progress_bar(wins: int, total: int, length: int = 8) -> str:
    if total == 0:
        return "▱" * length
    filled = round((wins / total) * length)
    return "▰" * filled + "▱" * (length - filled)

# ═══════════════════════════════════════════════════════
#  🔐  نظام Callback آمن
# ═══════════════════════════════════════════════════════
def cb_pack(prefix: str, **kwargs) -> str:
    token = f"{prefix}_{random.randint(100000, 999999)}"
    _cb_store[token] = {"prefix": prefix, "ts": time.time(), **kwargs}
    # تنظيف القديم (أكثر من ساعة)
    old = [k for k, v in _cb_store.items() if time.time() - v.get("ts", 0) > 3600]
    for k in old:
        _cb_store.pop(k, None)
    return token[:64]

def cb_get(token: str) -> dict | None:
    return _cb_store.get(token)

def cb_clear(token: str):
    _cb_store.pop(token, None)

# ═══════════════════════════════════════════════════════
#  ❌⭕  XO (Tic-Tac-Toe)
# ═══════════════════════════════════════════════════════
def xo_new_board():
    return [None] * 9

def xo_render(board, p1_id, p2_id, done=False) -> InlineKeyboardMarkup:
    sym = {p1_id: "❌", p2_id: "⭕"}
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            v = board[i]
            if v:
                label = sym.get(v, "·")
                cb    = f"xo_taken_{i}"
            else:
                label = "·"
                cb    = "xo_done" if done else f"xo_move_{i}"
            row.append(InlineKeyboardButton(label, callback_data=cb))
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def xo_winner(board):
    lines = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for a, b, c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if all(board) else None

def xo_winning_line(board):
    lines = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for a, b, c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return (a, b, c)
    return None

# ═══════════════════════════════════════════════════════
#  🔴  Connect 4
# ═══════════════════════════════════════════════════════
def c4_new_board():
    return [[None] * 7 for _ in range(6)]

def c4_drop(board, col, player) -> int:
    for r in range(5, -1, -1):
        if board[r][col] is None:
            board[r][col] = player
            return r
    return -1

def c4_winner(board, player) -> bool:
    for r in range(6):
        for c in range(4):
            if all(board[r][c+i] == player for i in range(4)):
                return True
    for r in range(3):
        for c in range(7):
            if all(board[r+i][c] == player for i in range(4)):
                return True
    for r in range(3):
        for c in range(4):
            if all(board[r+i][c+i] == player for i in range(4)):
                return True
    for r in range(3, 6):
        for c in range(4):
            if all(board[r-i][c+i] == player for i in range(4)):
                return True
    return False

def c4_full(board) -> bool:
    return all(board[0][c] is not None for c in range(7))

def c4_render(board, p1_id, p2_id, done=False, col_hint=None) -> InlineKeyboardMarkup:
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
        btn_row = []
        for i in range(7):
            # highlight column hint
            lbl = f"⬆{i+1}" if col_hint == i else f"{i+1}"
            btn_row.append(InlineKeyboardButton(lbl, callback_data=f"c4_col_{i}"))
        rows.append(btn_row)
    return InlineKeyboardMarkup(rows)

def c4_best_column(board, player) -> int | None:
    """ذكاء اصطناعي بسيط: يحاول يفوز أو يمنع الخصم"""
    other = None
    for r in range(6):
        for c in range(7):
            if board[r][c] and board[r][c] != player:
                other = board[r][c]
                break
        if other:
            break

    for col in range(7):
        tmp = [row[:] for row in board]
        row = c4_drop(tmp, col, player)
        if row != -1 and c4_winner(tmp, player):
            return col

    if other:
        for col in range(7):
            tmp = [row[:] for row in board]
            row = c4_drop(tmp, col, other)
            if row != -1 and c4_winner(tmp, other):
                return col

    return None

# ═══════════════════════════════════════════════════════
#  🔇  تقييد / رفع
# ═══════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════
#  📩  معالجة الرسائل
# ═══════════════════════════════════════════════════════
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    await handle_td_reply(update, context)

    if not msg.entities:
        return

    chat_id = msg.chat_id
    sender  = msg.from_user

    for ent in msg.entities:
        t_id   = None
        t_name = None
        t_username = None

        # ── نوع 1: @username عادي ──
        if ent.type == "mention":
            mentioned = msg.text[ent.offset + 1: ent.offset + ent.length]
            # تجاهل منشن البوت نفسه
            bot_username = context.bot.username or ""
            if mentioned.lower() == bot_username.lower():
                continue
            t_username = mentioned
            # نحاول نجيب الـ id من أعضاء الغرفة (أموثوق من get_chat)
            try:
                member = await context.bot.get_chat_member(chat_id, f"@{mentioned}")
                t_id   = member.user.id
                t_name = uname(member.user)
            except TelegramError:
                # فشل → نحاول get_chat كخطة بديلة
                try:
                    tc = await context.bot.get_chat(f"@{mentioned}")
                    t_id   = tc.id
                    t_name = tc.full_name or mentioned
                except TelegramError:
                    # لا نعرف الـ id، نخزن None ونتعامل لاحقاً
                    t_id   = None
                    t_name = mentioned

        # ── نوع 2: text_mention (شخص بدون username) ──
        elif ent.type == "text_mention" and ent.user:
            t_id       = ent.user.id
            t_name     = uname(ent.user)
            t_username = ent.user.username or None

        else:
            continue

        # تجاهل المنشن الذاتي
        if t_id and t_id == sender.id:
            continue

        # ── إذا t_id لا يزال None، لا نقدر نتحقق من الهوية لاحقاً ──
        # نعرض الزر لكن نضع t_id=None ونعتمد على username فقط
        token_game  = cb_pack("mg", c=sender.id, t=t_id, u=t_username or t_name)
        token_reply = cb_pack("mr", c=sender.id, t=t_id)

        display = f"@{t_username}" if t_username else t_name

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚔️ تحدي للعب",  callback_data=token_game),
            InlineKeyboardButton("👋 رد عليه",     callback_data=token_reply),
        ]])

        sent = await msg.reply_text(
            f"👀 <b>{uname(sender)}</b> ذكر <b>{display}</b>\n\nماذا تريد؟",
            reply_markup=kb,
            parse_mode="HTML"
        )
        pending_mentions[(chat_id, sent.message_id)] = {
            "challenger_id":   sender.id,
            "challenger_name": uname(sender),
            "t_id":       t_id,
            "t_username": t_username,
            "t_name":     t_name,
            "token_game":  token_game,
            "token_reply": token_reply,
        }
        return   # منشن واحد يكفي

# ═══════════════════════════════════════════════════════
#  🔘  Callbacks المنشن
# ═══════════════════════════════════════════════════════
async def on_mention_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    cid  = q.message.chat_id
    data = cb_get(q.data)

    if not data:
        await q.answer("⌛ انتهت صلاحية هذا الزر", show_alert=True)
        return

    prefix = data["prefix"]

    if prefix == "mg":
        if user.id != data["c"]:
            await q.answer("🚫 هذا الخيار ليس لك", show_alert=True)
            return

        # منع تحدي النفس (فحص مبكر إذا عرفنا الـ id)
        if data["t"] and user.id == data["t"]:
            await q.answer("🚫 لا تستطيع تحدي نفسك!", show_alert=True)
            return

        t_xo = cb_pack("px", c=data["c"], t=data["t"], u=data["u"], g="xo")
        t_c4 = cb_pack("px", c=data["c"], t=data["t"], u=data["u"], g="c4")

        display = f"@{data['u']}" if data['u'] else "اللاعب"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("❌⭕ XO",       callback_data=t_xo),
            InlineKeyboardButton("🔴 Connect4",  callback_data=t_c4),
        ]])
        await q.edit_message_text(
            f"⚔️ <b>{uname(user)}</b> يتحدى <b>{display}</b>\n\n🎮 اختر اللعبة:",
            reply_markup=kb, parse_mode="HTML"
        )

    elif prefix == "mr":
        if user.id != data["c"]:
            await q.answer("🚫 هذا الخيار ليس لك", show_alert=True)
            return
        await q.edit_message_text("👋 تم! رد عليه مباشرة في المجموعة.")

    elif prefix == "px":
        if user.id != data["c"]:
            await q.answer("🚫 هذا الخيار ليس لك", show_alert=True)
            return

        gname = "❌⭕ XO" if data["g"] == "xo" else "🔴 Connect4"
        challenger_name = uname(user)
        display_target  = f"@{data['u']}" if data['u'] else "اللاعب"

        t_acc = cb_pack("ac", c=data["c"], t=data["t"], u=data["u"], g=data["g"])
        t_rej = cb_pack("rj", c=data["c"], u=data["u"])

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ قبول",  callback_data=t_acc),
            InlineKeyboardButton("❌ رفض",  callback_data=t_rej),
        ]])
        await q.edit_message_text(
            f"🎮 <b>{challenger_name}</b> يتحدى <b>{display_target}</b> على {gname}!\n\n"
            f"👆 <b>{display_target}</b>، اضغط قبول أو رفض:",
            reply_markup=kb, parse_mode="HTML"
        )

    elif prefix == "ac":
        challenger_id = data["c"]
        target_id     = data["t"]   # قد يكون None إذا لم نعرف الـ id

        # ── منع لعب الشخص ضد نفسه ──
        if user.id == challenger_id:
            await q.answer("🚫 لا تستطيع قبول تحديك أنت!", show_alert=True)
            return

        # ── إذا كنا نعرف الـ id، تحقق أن المستخدم هو المتحدَّى ──
        if target_id and user.id != target_id:
            await q.answer("🚫 هذه الدعوة ليست لك!", show_alert=True)
            return

        # ── إذا كان t_id غير معروف، نقبل أي شخص غير المتحدِّي ──
        # (الحالة النادرة حين فشل جلب الـ id)

        await q.edit_message_text("✅ تم القبول! 🎮 تبدأ اللعبة الآن...")
        if data["g"] == "xo":
            await game_start_xo(context, cid, challenger_id, user.id, uname(user), data["u"])
        else:
            await game_start_c4(context, cid, challenger_id, user.id, uname(user), data["u"])

    elif prefix == "rj":
        await q.edit_message_text(
            f"❌ <b>@{data['u']}</b> رفض التحدي! 💔\n\nجرّب تتحداه مرة ثانية 😄",
            parse_mode="HTML"
        )

# ═══════════════════════════════════════════════════════
#  ❌⭕  بدء XO
# ═══════════════════════════════════════════════════════
async def game_start_xo(context, cid, p1_id, p2_id, p2_name, p2_username):
    # ── حماية نهائية: منع لعب الشخص ضد نفسه ──
    if p1_id == p2_id:
        await context.bot.send_message(cid, "⚠️ لا يمكن للشخص أن يلعب ضد نفسه!", parse_mode="HTML")
        return

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
        "move_count": 0,
        "started": datetime.now().isoformat()
    }

    text = (
        f"❌⭕ <b>لعبة XO</b>\n\n"
        f"❌ <b>{p1_name}</b>  vs  ⭕ <b>{p2_name}</b>\n\n"
        f"🎯 دور: <b>{p1_name}</b> ❌\n"
        f"⏰ المهلة: 5 دقائق"
    )
    sent = await context.bot.send_message(
        cid, text,
        reply_markup=xo_render(board, p1_id, p2_id),
        parse_mode="HTML"
    )
    active_games[cid]["msg_id"] = sent.message_id
    _schedule_game_timeout(context, cid)

# ═══════════════════════════════════════════════════════
#  🔴  بدء Connect 4
# ═══════════════════════════════════════════════════════
async def game_start_c4(context, cid, p1_id, p2_id, p2_name, p2_username):
    # ── حماية نهائية: منع لعب الشخص ضد نفسه ──
    if p1_id == p2_id:
        await context.bot.send_message(cid, "⚠️ لا يمكن للشخص أن يلعب ضد نفسه!", parse_mode="HTML")
        return

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
        "move_count": 0,
        "started": datetime.now().isoformat()
    }

    text = (
        f"🔴 <b>Connect 4</b>\n\n"
        f"🔴 <b>{p1_name}</b>  vs  🔵 <b>{p2_name}</b>\n\n"
        f"🎯 دور: <b>{p1_name}</b> 🔴\n"
        f"⏰ المهلة: 5 دقائق"
    )
    sent = await context.bot.send_message(
        cid, text,
        reply_markup=c4_render(board, p1_id, p2_id),
        parse_mode="HTML"
    )
    active_games[cid]["msg_id"] = sent.message_id
    _schedule_game_timeout(context, cid)

# ═══════════════════════════════════════════════════════
#  ⏰  Timeout الألعاب
# ═══════════════════════════════════════════════════════
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
            "⏰ <b>انتهى وقت اللعبة!</b>\nلا يوجد فائز — تعادل بالوقت ⌛",
            chat_id=cid, message_id=g["msg_id"], parse_mode="HTML"
        )
    except TelegramError:
        pass

# ═══════════════════════════════════════════════════════
#  🔘  Callback XO
# ═══════════════════════════════════════════════════════
async def on_xo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    cid  = q.message.chat_id
    data = q.data

    if "taken" in data or data == "xo_done":
        await q.answer("🚫 هذه الخانة محجوزة")
        return

    g = active_games.get(cid)
    if not g or g["type"] != "xo":
        await q.answer("لا توجد لعبة نشطة")
        return
    if user.id not in (g["p1"], g["p2"]):
        await q.answer("🚫 اللعبة ليست لك", show_alert=True)
        return
    if user.id != g["turn"]:
        await q.answer("⏳ ليس دورك!", show_alert=True)
        return

    idx = int(data.split("_")[-1])
    if g["board"][idx]:
        await q.answer("🚫 الخانة محجوزة")
        return

    g["board"][idx] = user.id
    g["move_count"] += 1
    winner = xo_winner(g["board"])
    p1, p2 = g["p1"], g["p2"]
    p1n, p2n = g["p1_name"], g["p2_name"]

    await q.answer("✅ تم!")

    if winner == "draw":
        game_stats[cid][p1]["draws"] += 1
        game_stats[cid][p2]["draws"] += 1
        await q.edit_message_text(
            f"❌⭕ <b>XO — تعادل!</b> 🤝\n\n"
            f"<b>{p1n}</b>  vs  <b>{p2n}</b>\n\n"
            f"📊 عدد الحركات: {g['move_count']}",
            reply_markup=xo_render(g["board"], p1, p2, True),
            parse_mode="HTML"
        )
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        return

    if winner:
        w_id   = winner
        l_id   = p2 if w_id == p1 else p1
        w_name = p1n if w_id == p1 else p2n
        l_name = p2n if w_id == p1 else p1n
        l_user = g["p2_username"] if w_id == p1 else None

        scores[cid][w_id]  += 1
        losses[cid][l_id]  += 1
        streaks[cid][w_id] += 1
        streaks[cid][l_id]  = 0
        game_stats[cid][w_id]["wins"]   += 1
        game_stats[cid][l_id]["losses"] += 1

        streak_txt = f"🔥 سلسلة {streaks[cid][w_id]} انتصار!" if streaks[cid][w_id] > 1 else ""

        await q.edit_message_text(
            f"❌⭕ <b>XO — انتهت!</b>\n\n"
            f"🏆 فاز: <b>{w_name}</b>\n"
            f"💀 خسر: <b>{l_name}</b>\n\n"
            f"📊 انتصارات {w_name}: <b>{scores[cid][w_id]}</b>\n"
            f"{streak_txt}",
            reply_markup=xo_render(g["board"], p1, p2, True),
            parse_mode="HTML"
        )
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        await td_start(context, cid, w_id, w_name, l_id, l_name, l_user)
        return

    g["turn"]  = p2 if user.id == p1 else p1
    nxt_name   = p2n if g["turn"] == p2 else p1n
    symbol     = "⭕" if g["turn"] == p2 else "❌"

    await q.edit_message_text(
        f"❌⭕ <b>XO</b>\n\n"
        f"❌ <b>{p1n}</b>  vs  ⭕ <b>{p2n}</b>\n\n"
        f"🎯 دور: <b>{nxt_name}</b> {symbol}\n"
        f"📌 حركة #{g['move_count']}",
        reply_markup=xo_render(g["board"], p1, p2),
        parse_mode="HTML"
    )
    _schedule_game_timeout(context, cid)

# ═══════════════════════════════════════════════════════
#  🔘  Callback Connect 4
# ═══════════════════════════════════════════════════════
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
        await q.answer("🚫 اللعبة ليست لك", show_alert=True)
        return
    if user.id != g["turn"]:
        await q.answer("⏳ ليس دورك!", show_alert=True)
        return

    col = int(data.split("_")[-1])
    row = c4_drop(g["board"], col, user.id)
    if row == -1:
        await q.answer("🚫 العمود ممتلئ!")
        return

    g["move_count"] += 1
    p1, p2 = g["p1"], g["p2"]
    p1n, p2n = g["p1_name"], g["p2_name"]

    await q.answer("✅ تم!")

    if c4_winner(g["board"], user.id):
        w_id   = user.id
        l_id   = p2 if w_id == p1 else p1
        w_name = p1n if w_id == p1 else p2n
        l_name = p2n if w_id == p1 else p1n
        l_user = g["p2_username"] if w_id == p1 else None

        scores[cid][w_id]  += 1
        losses[cid][l_id]  += 1
        streaks[cid][w_id] += 1
        streaks[cid][l_id]  = 0
        game_stats[cid][w_id]["wins"]   += 1
        game_stats[cid][l_id]["losses"] += 1

        streak_txt = f"🔥 سلسلة {streaks[cid][w_id]} انتصار!" if streaks[cid][w_id] > 1 else ""

        await q.edit_message_text(
            f"🔴 <b>Connect 4 — انتهت!</b>\n\n"
            f"🏆 فاز: <b>{w_name}</b>\n"
            f"💀 خسر: <b>{l_name}</b>\n\n"
            f"📊 انتصارات {w_name}: <b>{scores[cid][w_id]}</b>\n"
            f"{streak_txt}",
            reply_markup=c4_render(g["board"], p1, p2, True),
            parse_mode="HTML"
        )
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        await td_start(context, cid, w_id, w_name, l_id, l_name, l_user)
        return

    if c4_full(g["board"]):
        game_stats[cid][p1]["draws"] += 1
        game_stats[cid][p2]["draws"] += 1
        await q.edit_message_text(
            f"🔴 <b>Connect 4 — تعادل!</b> 🤝\n\n"
            f"<b>{p1n}</b>  vs  <b>{p2n}</b>\n\n"
            f"📊 عدد الحركات: {g['move_count']}",
            reply_markup=c4_render(g["board"], p1, p2, True),
            parse_mode="HTML"
        )
        active_games.pop(cid, None)
        _cancel_game_timeout(context, cid)
        return

    g["turn"]  = p2 if user.id == p1 else p1
    nxt_name   = p2n if g["turn"] == p2 else p1n
    symbol     = "🔵" if g["turn"] == p2 else "🔴"

    # تلميح ذكي: أفضل عمود للخصم
    hint = c4_best_column(g["board"], g["turn"])

    await q.edit_message_text(
        f"🔴 <b>Connect 4</b>\n\n"
        f"🔴 <b>{p1n}</b>  vs  🔵 <b>{p2n}</b>\n\n"
        f"🎯 دور: <b>{nxt_name}</b> {symbol}\n"
        f"📌 حركة #{g['move_count']}",
        reply_markup=c4_render(g["board"], p1, p2, col_hint=hint),
        parse_mode="HTML"
    )
    _schedule_game_timeout(context, cid)

# ═══════════════════════════════════════════════════════
#  🎭  صراحة أو جرأة
# ═══════════════════════════════════════════════════════
async def td_start(context, cid, w_id, w_name, l_id, l_name, l_username):
    l_mention = mention_html(l_id, f"@{l_username}" if l_username else l_name)

    t_truth = cb_pack("td_pick", l=l_id, ch="truth")
    t_dare  = cb_pack("td_pick", l=l_id, ch="dare")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗣️ صراحة", callback_data=t_truth),
        InlineKeyboardButton("😈 جرأة",  callback_data=t_dare),
    ]])

    sent = await context.bot.send_message(
        cid,
        f"🏆 فاز {mention_html(w_id, w_name)} على {l_mention}!\n\n"
        f"يا {l_mention}، اختر عقوبتك 👇\n"
        f"⏳ لديك <b>3 دقائق</b> للاختيار!",
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
        f"⏰ <b>انتهى الوقت!</b>\n"
        f"<b>{l_mention}</b> لم يختر → تم تجاهل العقوبة 😑",
        parse_mode="HTML"
    )

async def on_td_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    cid  = q.message.chat_id
    data = cb_get(q.data)

    if not data:
        await q.answer("⌛ انتهت صلاحية هذا الزر", show_alert=True)
        return

    state = truth_dare_state.get(cid)
    if not state:
        await q.answer("لا يوجد نظام نشط")
        return

    prefix = data["prefix"]

    # الخاسر اختار
    if prefix == "td_pick":
        if user.id != data["l"]:
            await q.answer("🚫 هذا الاختيار ليس لك", show_alert=True)
            return

        choice = data["ch"]
        state["choice"] = choice
        state["phase"]  = "waiting_question"

        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]
        label = "صراحة 🗣️" if choice == "truth" else "جرأة 😈"
        pool  = TRUTH_QUESTIONS if choice == "truth" else DARE_CHALLENGES

        # 3 اقتراحات مختلفة
        suggestions = random.sample(pool, min(3, len(pool)))
        auto = suggestions[0]

        buttons = []
        for i, s in enumerate(suggestions):
            short = s[:30] + "…" if len(s) > 30 else s
            t = cb_pack("td_auto", w=state["w_id"], l=state["l_id"], q=s)
            buttons.append([InlineKeyboardButton(f"🎲 {short}", callback_data=t)])

        t_custom = cb_pack("td_custom", w=state["w_id"])
        buttons.append([InlineKeyboardButton("✏️ اكتب سؤالك الخاص", callback_data=t_custom)])

        state["_suggestions"] = suggestions

        await q.edit_message_text(
            f"✅ <b>{l_mention}</b> اختار <b>{label}</b>!\n\n"
            f"يا <b>{state['w_name']}</b>، اختر سؤالاً أو اكتب خاصك:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )

    elif prefix == "td_auto":
        if user.id != data["w"]:
            await q.answer("🚫 هذا الاختيار ليس لك", show_alert=True)
            return
        state["question"] = data["q"]
        await _send_question_to_loser(q, context, cid, state)

    elif prefix == "td_custom":
        if user.id != data["w"]:
            await q.answer("🚫 هذا الاختيار ليس لك", show_alert=True)
            return
        state["phase"] = "waiting_question"
        await q.edit_message_text(
            f"✏️ يا <b>{state['w_name']}</b>، اكتب سؤالك أو تحديك الآن في المجموعة:",
            parse_mode="HTML"
        )

    elif prefix == "td_will":
        if user.id != data["l"]:
            await q.answer("🚫 هذا الاختيار ليس لك", show_alert=True)
            return
        state["phase"] = "waiting_answer"
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]
        await q.edit_message_text(
            f"✍️ <b>{l_mention}</b> سيجيب الآن...\n\n"
            f"👇 اكتب إجابتك في المجموعة:",
            parse_mode="HTML"
        )

    elif prefix == "td_refuse":
        if user.id != data["l"]:
            await q.answer("🚫 هذا الاختيار ليس لك", show_alert=True)
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
            f"تراجع وأجب قبل 30 ثانية ⏳",
            reply_markup=kb, parse_mode="HTML"
        )
        context.job_queue.run_once(
            _mute_job, 30,
            data={"cid": cid, "l_id": l_id, "mins": 30},
            name=f"mute_{cid}_{l_id}"
        )

    elif prefix == "td_yes":
        if user.id != data["w"]:
            await q.answer("🚫 هذا الاختيار ليس لك", show_alert=True)
            return
        await q.edit_message_text(
            "✅ <b>تم قبول الإجابة!</b> 🎉\n\nاللعبة انتهت بنجاح 🏆",
            parse_mode="HTML"
        )
        truth_dare_state.pop(cid, None)
        _cancel_td_timeout(context, cid)

    elif prefix == "td_no":
        if user.id != data["w"]:
            await q.answer("🚫 هذا الاختيار ليس لك", show_alert=True)
            return
        l_id      = state["l_id"]
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]
        await q.edit_message_text(
            f"❌ <b>الإجابة مرفوضة!</b>\n"
            f"⚠️ <b>{l_mention}</b> سيُقيَّد <b>10 دقائق</b> 🔒",
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
        f"⚠️ الرفض = تقييد 30 دقيقة! 🔒"
    )
    if hasattr(q_or_msg, "edit_message_text"):
        sent = await q_or_msg.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        sent = await context.bot.send_message(cid, text, reply_markup=kb, parse_mode="HTML")
        state["bot_msgs"].append(sent.message_id)

async def handle_td_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.message
    if not msg or not msg.text:
        return
    cid  = msg.chat_id
    user = msg.from_user
    state = truth_dare_state.get(cid)
    if not state:
        return

    if state["phase"] == "waiting_question" and user.id == state["w_id"]:
        state["question"] = msg.text
        await _send_question_to_loser(msg, context, cid, state)

    elif state["phase"] == "waiting_answer" and user.id == state["l_id"]:
        state["phase"] = "waiting_verdict"
        w_id      = state["w_id"]
        l_mention = f"@{state['l_username']}" if state.get("l_username") else state["l_name"]

        t_yes = cb_pack("td_yes", w=w_id)
        t_no  = cb_pack("td_no",  w=w_id)

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ قبلت الإجابة",      callback_data=t_yes),
            InlineKeyboardButton("❌ لم يجب بصدق",      callback_data=t_no),
        ]])
        sent = await msg.reply_text(
            f"💬 <b>{l_mention}</b> أجاب:\n\n<i>{msg.text}</i>\n\n"
            f"يا <b>{state['w_name']}</b>، هل قبلت إجابته؟",
            reply_markup=kb, parse_mode="HTML"
        )
        state["bot_msgs"].append(sent.message_id)

# ═══════════════════════════════════════════════════════
#  ⚙️  Jobs المساعدة
# ═══════════════════════════════════════════════════════
async def _mute_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    cid, l_id, mins = d["cid"], d["l_id"], d["mins"]
    state = truth_dare_state.get(cid)
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
            _unmute_job, mins * 60,
            data={"cid": cid, "uid": l_id},
            name=f"unmute_{cid}_{l_id}"
        )
    truth_dare_state.pop(cid, None)

async def _unmute_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await unmute(context, d["cid"], d["uid"])
    try:
        await context.bot.send_message(
            d["cid"],
            f"🔓 تم رفع التقييد عن اللاعب.",
            parse_mode="HTML"
        )
    except TelegramError:
        pass

def _cancel_td_timeout(context, cid):
    for job in context.job_queue.get_jobs_by_name(f"tdtout_{cid}"):
        job.schedule_removal()

# ═══════════════════════════════════════════════════════
#  📋  الأوامر
# ═══════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = uname(update.effective_user)
    await update.message.reply_text(
        f"👋 أهلاً <b>{name}</b>!\n\n"
        f"🎮 <b>بوت ألعاب المجموعات</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕹️ <b>الألعاب المتاحة:</b>\n"
        f"  • ❌⭕ XO (إكس أو)\n"
        f"  • 🔴 Connect 4\n\n"
        f"🎭 <b>بعد كل لعبة:</b>\n"
        f"  • صراحة أو جرأة للخاسر!\n"
        f"  • رفض الإجابة = تقييد من المجموعة 🔒\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>كيف تبدأ:</b>\n"
        f"  منشن أي شخص في المجموعة\n"
        f"  واختر تحدي للعب!\n\n"
        f"📊 /scores — لوحة الترتيب\n"
        f"📈 /stats — إحصائياتك\n"
        f"❓ /help — المساعدة\n"
        f"🚫 /cancel — إلغاء اللعبة",
        parse_mode="HTML"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>دليل الاستخدام:</b>\n\n"
        "1️⃣ منشن شخص في المجموعة\n"
        "2️⃣ اضغط ⚔️ تحدي للعب\n"
        "3️⃣ اختر XO أو Connect4\n"
        "4️⃣ انتظر قبول الشخص المتحدَّى\n"
        "5️⃣ العبا بالتناوب\n"
        "6️⃣ الخاسر يختار صراحة أم جرأة\n"
        "7️⃣ الفائز يختار السؤال أو التحدي\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>قواعد مهمة:</b>\n"
        "• الألعاب تنتهي تلقائياً بعد <b>5 دقائق</b>\n"
        "• رفض الصراحة/الجرأة = تقييد <b>30 دقيقة</b>\n"
        "• إجابة غير مقنعة = تقييد <b>10 دقائق</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎯 <b>الأوامر:</b>\n"
        "/scores — لوحة الترتيب\n"
        "/stats — إحصائياتك الشخصية\n"
        "/cancel — إلغاء اللعبة الحالية",
        parse_mode="HTML"
    )

async def cmd_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid   = update.effective_chat.id
    board = scores.get(cid)
    if not board:
        await update.message.reply_text("🏆 لا يوجد انتصارات بعد!\n\nابدأ أول تحدٍ الآن 🎮")
        return

    sorted_sc = sorted(board.items(), key=lambda x: x[1], reverse=True)
    lines = ["🏆 <b>لوحة الترتيب</b>\n━━━━━━━━━━━━━━\n"]
    medals = ["🥇", "🥈", "🥉"]

    for i, (uid, wins) in enumerate(sorted_sc[:10]):
        try:
            m = await context.bot.get_chat_member(cid, uid)
            n = uname(m.user)
        except TelegramError:
            n = str(uid)

        total = wins + losses[cid].get(uid, 0)
        bar   = progress_bar(wins, total)
        medal = medals[i] if i < 3 else f"{i+1}."
        streak_info = f" 🔥×{streaks[cid].get(uid, 0)}" if streaks[cid].get(uid, 0) > 1 else ""
        lines.append(
            f"{medal} <b>{n}</b>{streak_info}\n"
            f"   {bar} {wins}✅ {losses[cid].get(uid, 0)}❌"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid  = update.effective_chat.id
    uid  = update.effective_user.id
    name = uname(update.effective_user)

    w = scores[cid].get(uid, 0)
    l = losses[cid].get(uid, 0)
    d = game_stats[cid][uid].get("draws", 0)
    t = w + l + d
    wr = round((w / t) * 100) if t > 0 else 0
    s  = streaks[cid].get(uid, 0)

    bar = progress_bar(w, t, 10)

    await update.message.reply_text(
        f"📈 <b>إحصائيات {name}</b>\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"🎮 مجموع الألعاب: <b>{t}</b>\n"
        f"✅ انتصارات: <b>{w}</b>\n"
        f"❌ خسائر:   <b>{l}</b>\n"
        f"🤝 تعادلات: <b>{d}</b>\n\n"
        f"📊 نسبة الفوز: <b>{wr}%</b>\n"
        f"{bar}\n\n"
        f"🔥 السلسلة الحالية: <b>{s}</b> انتصار متتالي",
        parse_mode="HTML"
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid  = update.effective_chat.id
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

    await update.message.reply_text("⚠️ لا يوجد شيء لإلغائه.")

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نفس الـ scores لكن مختصر"""
    await cmd_scores(update, context)

# ═══════════════════════════════════════════════════════
#  🚀  main
# ═══════════════════════════════════════════════════════
def main():
    app = Application.builder().token(TOKEN).build()

    # أوامر
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("scores", cmd_scores))
    app.add_handler(CommandHandler("top",    cmd_top))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Callbacks المنشن
    mention_prefixes = "|".join(["mg_", "mr_", "px_", "ac_", "rj_"])
    app.add_handler(CallbackQueryHandler(on_mention_cb, pattern=rf"^({mention_prefixes})"))

    # Callbacks الألعاب
    app.add_handler(CallbackQueryHandler(on_xo,    pattern=r"^xo_"))
    app.add_handler(CallbackQueryHandler(on_c4,    pattern=r"^c4_"))
    app.add_handler(CallbackQueryHandler(on_td_cb, pattern=r"^td_"))

    # الرسائل
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    logger.info("🤖 البوت يعمل بنجاح...")
    print("🚀 البوت شغّال! اضغط Ctrl+C للإيقاف.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
