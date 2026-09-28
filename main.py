import os
import logging
import asyncio
import random

from dotenv import load_dotenv

# МАҢЫЗДЫ: .env файлын ai_tutor импортталғанға дейін жүктеу керек,
# себебі ai_tutor.py GEMINI_API_KEY-ды модуль жүктелген сәтте оқиды.
# Бұрын load_dotenv() төменде, import-тардан кейін тұрғандықтан,
# admin GEMINI_API_KEY қосса да бот оны "жоқ" деп таниды.
load_dotenv()

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

import database
import questions
import ai_tutor


# =========================
# .env файлындағы мәліметтер
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID_1 = int(os.getenv("ADMIN_ID_1", "0"))
ADMIN_ID_2 = int(os.getenv("ADMIN_ID_2", "0"))
ADMIN_IDS = {ADMIN_ID_1, ADMIN_ID_2}

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN .env файлында көрсетілмеген!")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# =========================
# Логтар
# =========================

logging.basicConfig(level=logging.INFO)


# =========================
# Бот және Dispatcher
# =========================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================
# Күйлер (States)
# =========================

class Registration(StatesGroup):
    waiting_for_name = State()


class AdminUpload(StatesGroup):
    waiting_for_topic = State()
    waiting_for_level = State()
    waiting_for_key = State()
    waiting_for_import_confirm = State()


class AdminPanel(StatesGroup):
    add_topic = State()
    add_level = State()
    add_question_text = State()
    add_options = State()
    add_correct = State()
    add_explanation = State()
    add_preview = State()

    edit_menu = State()
    edit_value = State()

    gen_topic = State()
    gen_level = State()
    gen_count = State()
    gen_preview = State()


class QuizSession(StatesGroup):
    in_progress = State()


class AITutor(StatesGroup):
    chatting = State()


# Активті сауалнамалар: poll_id -> {"user_id", "topic",
#                                    "correct_letter", "correct_text"}
ACTIVE_POLLS = {}

# Соңғы аяқталған тесттегі қате жауаптар: user_id -> [items]
# ("🔎 Қателерімді көру" батырмасы үшін, state тазаланғаннан кейін де
# қолжетімді болу үшін бөлек сақталады)
LAST_QUIZ_RESULTS = {}

# "💡 Неге?" AI түсіндірмесі үшін контекст: poll_id -> {...}
AI_EXPLAIN_CONTEXT = {}

OPTION_LETTERS = ["A", "B", "C", "D"]

# Тест ұзындығының жалғыз рұқсат етілген жиыны — осы жерден
# басқа ешбір жерде 40/50 секілді ескі мән қолданылмауы керек.
QUIZ_SIZES = [20, 25, 30]

# Соңғы тест конфигурациясы (retry үшін): user_id -> {"topic", "level"}
LAST_QUIZ_CONFIG = {}


# =========================
# Негізгі мәзір
# =========================

def main_menu(user_id: int):
    kb = [
        [KeyboardButton(text="📝 Тест тапсыру")],
        [KeyboardButton(text="🤖 Сұрақ қою")],
        [KeyboardButton(text="📚 Менің нәтижелерім")],
        [KeyboardButton(text="ℹ️ Көмек")],
    ]

    # Егер қолданушы админ болса,
    # админ панелі батырмасын қосамыз
    if is_admin(user_id):
        kb.insert(
            0,
            [KeyboardButton(text="🔐 Админ панелі")]
        )

    return ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True
    )


@dp.message(F.text == "ℹ️ Көмек")
async def about_bot(message: types.Message):
    ai_status = (
        "қосулы ✅" if ai_tutor.is_configured() else "әзірге қосылмаған ⚠️"
    )
    await message.answer(
        "🇬🇧 English Boost\n\n"
        "Ағылшын тілін тест арқылы үйренуге арналған Telegram-бот.\n\n"
        "📚 Әртүрлі тақырыптар мен деңгейлер\n"
        "📝 20–30 сұрақтық тесттер\n"
        "💡 Қателерге мұғалім сияқты түсіндірме\n"
        "📊 Жеке статистика\n"
        f"🤖 AI тьютор ({ai_status})\n\n"
        "Командалар:\n"
        "📝 Тест тапсыру — тест тапсыру\n"
        "🤖 Сұрақ қою — ағылшын тілі бойынша кез келген сұрақ қою "
        "немесе тапсырманың фотосын жіберу\n"
        "📚 Менің нәтижелерім — жеке статистикаңыз"
    )


# =========================================================
# 🤖 Сұрақ қою — Gemini AI тьютор режимі
# =========================================================

_AI_EXIT_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Шығу")]],
    resize_keyboard=True,
)


@dp.message(F.text == "🤖 Сұрақ қою")
async def enter_ai_mode(message: types.Message, state: FSMContext):
    await state.set_state(AITutor.chatting)
    await message.answer(
        "🤖 AI English Tutor\n\n"
        "💬 English туралы кез келген сұрағыңызды жазыңыз немесе "
        "тапсырманың фотосын жіберіңіз.\n\n"
        "Мысалы:\n"
        "• Explain Present Perfect\n"
        "• Why is \"went\" correct?\n"
        "• What is the difference between since and for?\n"
        "• Help me solve this exercise\n\n"
        "📷 Тапсырманың фотосын да жіберуге болады.",
        reply_markup=_AI_EXIT_KB,
    )


@dp.message(F.text == "❌ Шығу")
async def exit_ai_mode(message: types.Message, state: FSMContext):
    # Күйге тәуелсіз: бот қайта қосылғанда (MemoryStorage тазаланғанда)
    # экранда "❌ Шығу" батырмасы қалып қоюы мүмкін — сонда да
    # басты мәзірге дұрыс қайтуы керек.
    await state.clear()
    await message.answer(
        "Басты мәзірге оралдыңыз.",
        reply_markup=main_menu(message.from_user.id),
    )


@dp.message(AITutor.chatting, F.text)
async def ai_mode_text(message: types.Message, state: FSMContext):
    thinking = await message.answer("🤔 Ойланып жатырмын...")
    answer = await asyncio.to_thread(ai_tutor.ask_tutor, message.text)
    try:
        await thinking.delete()
    except Exception:
        pass
    await message.answer(answer)


@dp.message(AITutor.chatting, F.photo)
async def ai_mode_photo(message: types.Message, state: FSMContext):
    thinking = await message.answer("🔍 Суретті оқып жатырмын...")

    try:
        photo = message.photo[-1]  # ең жоғары ажыратымдылық
        file_stream = await bot.download(photo)
        image_bytes = file_stream.read()
        answer = await asyncio.to_thread(
            ai_tutor.solve_photo_exercise, image_bytes, "image/jpeg"
        )
    except Exception:
        logging.exception("AI фото өңдеу қатесі")
        answer = ai_tutor.FRIENDLY_UNAVAILABLE

    try:
        await thinking.delete()
    except Exception:
        pass
    await message.answer(answer)


@dp.message(AITutor.chatting, F.document)
async def ai_mode_document(message: types.Message, state: FSMContext):
    """
    AI режимінде файл (.docx/.txt/.pdf/.xlsx/.csv/.rtf/.odt) жіберсе,
    оны сұрақтар базасына импорттамай, керісінше мәтінін оқып,
    Gemini-ге шешу/түсіндіру үшін жібереміз (doc13 талабы).
    """
    thinking = await message.answer("🔍 Файлды оқып жатырмын...")

    try:
        filename = message.document.file_name or ""
        file_stream = await bot.download(message.document)
        file_bytes = file_stream.read()
        text = "\n".join(questions.extract_lines(filename, file_bytes))
        if not text.strip():
            answer = "⚠️ Файлдан мәтін оқи алмадым. Басқа файл жіберіп көріңіз."
        else:
            answer = await asyncio.to_thread(
                ai_tutor.solve_document_exercise, text
            )
    except Exception:
        logging.exception("AI файл өңдеу қатесі")
        answer = ai_tutor.FRIENDLY_UNAVAILABLE

    try:
        await thinking.delete()
    except Exception:
        pass
    await message.answer(answer)


# =========================
# /start командасы
# =========================

@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    # Пайдаланушы тест немесе файл жүктеу ортасында тұрып /start
    # басса, ескі күйді тазалаймыз (тоқтап қалған сессия келесі
    # тестке кедергі келтірмеу үшін), сондай-ақ сол пайдаланушыға
    # тиесілі, жауап берілмей қалған активті poll-дарды өшіреміз.
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        stale_poll_ids = [
            pid for pid, info in ACTIVE_POLLS.items()
            if info.get("user_id") == user_id
        ]
        for pid in stale_poll_ids:
            ACTIVE_POLLS.pop(pid, None)

    current_score = database.get_user_score(user_id)

    if current_score > 0 or is_admin(user_id):
        await message.answer(
            "Қайта қош келдіңіз! Дайындыққа кірісейік.",
            reply_markup=main_menu(user_id)
        )
    else:
        await message.answer(
            "Сәлеметсіз бе! ҰБТ және Олимпиадаға дайындық "
            "ботына қош келдіңіз.\n\n"
            "Бастау үшін аты-жөніңізді енгізіңіз.\n"
            "Мысалы: Асан Әлихан"
        )

        await state.set_state(
            Registration.waiting_for_name
        )


# =========================
# Аты-жөнді қабылдау
# =========================

@dp.message(Registration.waiting_for_name)
async def process_name(
    message: types.Message,
    state: FSMContext
):
    user_id = message.from_user.id

    full_name = (message.text or "").strip()

    if len(full_name) < 3:
        await message.answer(
            "Өтініш, толық аты-жөніңізді дұрыс енгізіңіз."
        )
        return

    database.register_user(
        user_id,
        full_name
    )

    await state.clear()

    await message.answer(
        f"Тіркелу сәтті өтті, {full_name}!",
        reply_markup=main_menu(user_id)
    )


# =========================
# Нәтижені көру
# =========================

@dp.message(F.text == "📚 Менің нәтижелерім")
async def show_score(message: types.Message):
    user_id = message.from_user.id
    stats = database.get_user_stats(user_id)

    await message.answer(
        "📊 Менің статистикам\n\n"
        f"🎯 Тапсырылған тесттер: {stats['tests_completed']}\n"
        f"✅ Дұрыс жауаптар: {stats['total_correct']}\n"
        f"📝 Жауап берілген сұрақтар: {stats['total_answered']}\n"
        f"📈 Орташа нәтиже: {stats['average_percent']}%\n"
        f"🏆 Жалпы ұпай: {stats['score']}"
    )


# (Ескі "📋 Оқушылар статистикасы" батырмасы енді 🔐 Админ панелі
#  ішіндегі 📊 Quiz статистикасы экранына біріктірілді — төменде.)


# =========================================================
# Әкімші: сұрақтар файлын жүктеу (.docx/.txt/.xlsx/.pdf/.csv/.rtf/.odt)
# -> тақырыпты жазу -> деңгейді таңдау -> жауап кілтін жүктеу (.txt)
# =========================================================

@dp.message(F.document)
async def handle_document(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    if not is_admin(user_id):
        await message.answer(
            "Файл жүктеу функциясы тек әкімшілерге қолжетімді."
        )
        return

    current_state = await state.get_state()

    # Тақырып жазуды немесе деңгей таңдауды күтіп тұрғанда файл келсе
    if current_state in (
        AdminUpload.waiting_for_topic.state,
        AdminUpload.waiting_for_level.state,
    ):
        await message.answer(
            "Алдымен алдыңғы қадамды аяқтаңыз: тақырыпты жазыңыз "
            "немесе деңгейді таңдаңыз."
        )
        return

    filename = message.document.file_name or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    file_stream = await bot.download(message.document)
    file_bytes = file_stream.read()

    # -----------------------------------------------------
    # 1-қадам: бірінші файл — сұрақтар файлы
    # -----------------------------------------------------
    if current_state != AdminUpload.waiting_for_key.state:

        try:
            lines, key_lines = questions.extract_lines_and_key(filename, file_bytes)
            logging.debug("EXTRACTED TEXT:\n%s", "\n".join(lines)[:3000])
            parsed = questions.parse_questions_from_lines(lines)
            questions.apply_trailing_key(parsed, key_lines)

            # Стандартты (A-D әрпі) парсер ештеңе таппаса, кейбір
            # файлдарда жауап нұсқалары санмен (1-4) берілуі мүмкін —
            # соны сынап көреміз.
            used_numbered_fallback = False
            use_ai_converter = False
            if not parsed:
                # A/B/C/D жоқ, бірақ "тапсырма + Answers" түріндегі
                # жаттығу файлы (мыс. "she / run much recently?") болса,
                # numbered fallback оны қате сұраққа айналдырып жібереді —
                # сондықтан мұндайды тікелей AI Converter-ге береміз.
                if questions.is_prompt_style_exercise(lines, key_lines):
                    use_ai_converter = True
                else:
                    fallback = questions.parse_questions_numbered_fallback(lines)
                    if fallback:
                        parsed = fallback
                        questions.apply_trailing_key(parsed, key_lines)
                        used_numbered_fallback = True
        except Exception as exc:
            logging.exception("Файлды оқу кезінде қате")
            await message.answer(f"❌ Файлды өңдеу кезінде қате пайда болды: {exc}")
            return

        if use_ai_converter:
            await _start_ai_conversion(message, state, lines, key_lines)
            return

        if parsed and used_numbered_fallback:
            await message.answer(
                "ℹ️ Файлда жауап нұсқалары A-D әрпімен емес, 1-4 "
                "санымен жазылған екен — оларды автоматты түрде "
                "A/B/C/D-ға сәйкестендірдім."
            )

        if not parsed:
            # Толық сәтсіздік туралы бірден хабарламай, неге сұрақ
            # шықпағанын нақты түсіндіреміз (толық емес блоктар
            # табылса, соны да көрсетеміз).
            try:
                _, incomplete = questions.parse_questions_detailed(lines)
            except Exception:
                incomplete = []

            # Файлда А-D нұсқалары мүлде жоқ болса, оны ескі қате
            # хабарламасымен тастамай, AI Converter-ге береміз.
            has_any_options = any(item["found"] for item in incomplete)
            if not has_any_options and sum(len((l or "").strip()) for l in lines) >= 20:
                await _start_ai_conversion(message, state, lines, key_lines)
                return

            if incomplete:
                details = "\n".join(
                    f"  №{item['number']} — {item['reason']}\n"
                    f"    Табылған жауаптар: {', '.join(item['found']) or '—'}\n"
                    f"    Жетіспейді: {', '.join(item['missing']) or '—'}"
                    for item in incomplete[:10]
                )
                await message.answer(
                    "⚠️ Файл тексерілді\n\n"
                    f"Табылған сұрақ тәрізді блоктар: {len(incomplete)}\n"
                    f"Дұрыс танылғаны: 0\n\n" + details + (
                        "\n  ..." if len(incomplete) > 10 else ""
                    ) + "\n\nФорматты тексеріп, қайта жіберіңіз."
                )
            else:
                await message.answer(
                    "❌ Файлдан сұрақтар табылмады. Форматты тексеріп, "
                    "қайта жіберіңіз."
                )
            return

        await state.update_data(
            pending_questions=parsed, import_source=None, ai_report=None
        )
        await state.set_state(AdminUpload.waiting_for_topic)

        answered_count = sum(1 for q in parsed if q.get("inline_answer"))

        if answered_count == len(parsed):
            status_lines = (
                "🔑 Жауап кілті: автоматты түрде анықталды\n"
                f"✅ Жауаптары бар: {answered_count}/{len(parsed)}"
            )
        elif answered_count > 0:
            missing_nums = [
                q["number"] for q in parsed if not q.get("inline_answer")
            ]
            status_lines = (
                f"🔑 Жауап кілті: ішінара анықталды ({answered_count}/"
                f"{len(parsed)})\n"
                f"⚠️ Жауабы табылмаған сұрақтар: {missing_nums}\n"
                "Кейін осыларға сәйкес бөлек жауап кілтін (.txt) сұраймын."
            )
        else:
            status_lines = (
                "🔑 Жауап кілті: файл ішінен табылмады.\n"
                "Кейін бөлек жауап кілті файлын (.txt) сұраймын."
            )

        await message.answer(
            f"✅ Файл сәтті оқылды!\n\n"
            f"📚 Табылған сұрақтар: {len(parsed)}\n"
            f"{status_lines}\n\n"
            "Осы сұрақтардың тақырыбын жазыңыз (мыс: Tenses):"
        )
        return

    # -----------------------------------------------------
    # 2-қадам: екінші файл — жауап кілті (.txt)
    # -----------------------------------------------------
    if ext != "txt":
        await message.answer(
            "Жауап кілті .txt форматында болуы керек. Қайта жіберіңіз."
        )
        return

    data = await state.get_data()
    pending = data.get("pending_questions", [])
    topic = data.get("pending_topic")
    level = data.get("pending_level")

    key_lines = questions.read_txt_lines(file_bytes)
    key = questions.parse_answer_key_from_lines(key_lines)

    if not key:
        await message.answer(
            "Жауап кілтінен ешбір сәйкестік таппадым. "
            "Формат мынадай болуы керек: '1 — B'."
        )
        return

    await _present_import_summary(
        message, state, pending, topic, level, key, key_label="Жауаптар"
    )


# =========================================================
# 🔄 AI CONVERTER: A/B/C/D жоқ дайын тапсырма файлын Quiz-қа айналдыру
# =========================================================
#
# Бұл AI GENERATOR (🤖 AI арқылы сұрақ генерациялау) емес. Генератор
# тақырып бойынша жаңа сұрақ ойлап табады, ал бұл жерде мұғалім жіберген
# дайын тапсырма мен Answers бөлімі Quiz форматына айналдырылады.
# Нәтиже ескі импорттағыдай растау экраны арқылы (import:confirm)
# database.add_question-ға сақталады.

def _build_exercise_text(lines, key_lines):
    """Файлдың толық мәтінін (жауап бөлімімен) AI-ға арналған бір мәтінге жинау."""
    body = "\n".join(
        (l or "").strip() for l in lines if (l or "").strip()
    )
    key_body = "\n".join(
        (l or "").strip() for l in (key_lines or []) if (l or "").strip()
    )
    if key_body:
        body += "\n\nAnswers:\n" + key_body
    return body


async def _start_ai_conversion(message, state: FSMContext, lines, key_lines):
    if not ai_tutor.is_configured():
        await message.answer(
            "Файлда A/B/C/D жауап нұсқалары табылмады.\n\n"
            "Мұндай дайын тапсырма файлын автоматты түрде Quiz-қа "
            "айналдыру үшін AI қажет, бірақ AI әзірге қосылмаған "
            "(.env файлында GEMINI_API_KEY жоқ).\n"
            "Кілтті қосып, ботты қайта іске қосыңыз немесе файлды "
            "A/B/C/D форматында дайындап жіберіңіз."
        )
        return

    thinking = await message.answer(
        "AI файлды Quiz форматына айналдырып жатыр. "
        "Бұл бір-екі минутқа созылуы мүмкін..."
    )

    exercise_text = _build_exercise_text(lines, key_lines)
    answers = questions.extract_numbered_answers(key_lines)

    try:
        converted, report, error = await asyncio.to_thread(
            ai_tutor.convert_exercise_to_quiz, exercise_text, answers
        )
    except Exception:
        logging.exception("AI converter қатесі")
        converted, report, error = [], {}, ai_tutor.FRIENDLY_UNAVAILABLE

    try:
        await thinking.delete()
    except Exception:
        pass

    if error or not converted:
        await message.answer(
            error or "AI файлдан жарамды сұрақ жасай алмады. Файлды тексеріп, қайта жіберіңіз."
        )
        return

    # Ескі парсер форматына келтіреміз (number/question/options/inline_answer),
    # explanation өрісі қосымша.
    pending = [
        {
            "number": idx,
            "question": q["question"],
            "options": q["options"],
            "inline_answer": q["correct_answer"],
            "explanation": q.get("explanation"),
        }
        for idx, q in enumerate(converted, 1)
    ]

    await state.update_data(
        pending_questions=pending, import_source="ai", ai_report=report
    )
    await state.set_state(AdminUpload.waiting_for_topic)

    out = [
        "Файл оқылды.",
        "",
        f"Табылған тапсырмалар: {report['found']}",
        f"AI арқылы Quiz форматына айналдырылды: {report['converted']}",
        "A/B/C/D нұсқалары автоматты жасалды.",
    ]
    if report.get("key_available"):
        out.append("Дұрыс жауаптар Answers бөліміндегі жауаптармен тексерілді.")
    else:
        out.append(
            "Файлда Answers бөлімі жоқ, сондықтан дұрыс жауаптарды AI өзі "
            "анықтады. Сақтамас бұрын сұрақтарды міндетті түрде тексеріңіз."
        )
    if report.get("invalid"):
        out.append(
            f"Тексеруден өтпегендіктен өткізілген тапсырмалар: {report['invalid']}"
        )
    out += ["", "Осы сұрақтардың тақырыбын жазыңыз (мыс: Present Perfect Continuous):"]

    await message.answer("\n".join(out))


async def _present_ai_import_summary(
    target_message, state: FSMContext, pending, topic, level, report
):
    """AI conversion нәтижесін көрсетіп, "Барлығын сақтау / Болдырмау" растауын сұрау."""
    merged = []
    seen = set()
    duplicates = 0
    for q in pending:
        text = q["question"]
        if text in seen or database.question_exists(text):
            duplicates += 1
        seen.add(text)
        merged.append({
            "question": text,
            "options": q["options"],
            "correct_answer": q["inline_answer"],
            "explanation": q.get("explanation"),
        })

    await state.update_data(
        import_merged=merged, import_topic=topic, import_level=level
    )
    await state.set_state(AdminUpload.waiting_for_import_confirm)

    converted = report.get("converted", len(pending))
    if report.get("key_available"):
        verified_line = f"Дұрыс жауаптары тексерілгені: {report.get('verified', 0)}"
    else:
        verified_line = (
            "Дұрыс жауаптары: Answers бөлімі жоқ, AI өзі анықтады (тексеріңіз)"
        )

    text = (
        "Import нәтижесі\n\n"
        f"Тақырып: {topic}\n"
        f"Деңгей: {level}\n\n"
        f"Табылған тапсырмалар: {report.get('found', len(pending))}\n"
        f"Quiz форматына айналдырылғаны: {converted}\n"
        f"A/B/C/D жасалғаны: {report.get('abcd_generated', converted)}\n"
        f"{verified_line}\n"
        f"Қайталанатын сұрақтар: {duplicates}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Барлығын сақтау", callback_data="import:confirm")],
            [InlineKeyboardButton(text="Болдырмау", callback_data="import:cancel")],
        ]
    )
    await target_message.answer(text, reply_markup=kb)


async def _present_import_summary(
    target_message, state: FSMContext, pending, topic, level, key,
    key_label, key_count=None,
):
    """
    Сұрақтарды жауап кілтімен (немесе inline жауаптармен) сәйкестендіріп,
    растау сұрайтын "📥 Import нәтижесі" экранын көрсету. Бөлек кілт
    файлы (handle_document) мен inline-answer жол (process_upload_level)
    екеуі де осы функцияны қолданады. key_count берілмесе len(key)
    қолданылады (бөлек кілт файлы жағдайында дұрыс сан).
    """
    merged, missing = questions.merge_questions_with_key(pending, key)

    total_q = len(pending)
    shown_key_count = key_count if key_count is not None else len(key)

    if missing:
        await target_message.answer(
            "⚠️ Answer key толық емес.\n"
            f"Сұрақтар: {total_q}\n"
            f"{key_label}: {shown_key_count}\n"
            f"Сәйкестік: ❌\n\n"
            f"Жауабы табылмаған сұрақ нөмірлері: {missing}\n\n"
            "Дұрыс/толық жауап кілтін қайта жіберіңіз."
        )
        return

    added = 0
    duplicates = 0
    for q in merged:
        if database.question_exists(q["question"]):
            duplicates += 1
        else:
            added += 1

    await state.update_data(
        import_merged=merged,
        import_topic=topic,
        import_level=level,
    )
    await state.set_state(AdminUpload.waiting_for_import_confirm)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Import", callback_data="import:confirm")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="import:cancel")],
        ]
    )

    await target_message.answer(
        "📥 Import нәтижесі\n\n"
        f"Тақырып: {topic} | Деңгей: {level}\n"
        f"Сұрақтар: {total_q} | {key_label}: {shown_key_count} | Сәйкестік: ✅\n\n"
        f"✅ Валид сұрақтар: {added}\n"
        f"⚠️ Қайталанатындар (өткізіледі): {duplicates}\n\n"
        "Растайсыз ба?",
        reply_markup=kb,
    )


@dp.callback_query(
    AdminUpload.waiting_for_import_confirm,
    F.data == "import:confirm",
)
async def confirm_import(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    merged = data.get("import_merged", [])
    topic = data.get("import_topic")
    level = data.get("import_level")

    added = 0
    duplicates = 0

    for q in merged:
        ok = database.add_question(
            topic=topic,
            level=level,
            question_text=q["question"],
            option_a=q["options"].get("A", ""),
            option_b=q["options"].get("B", ""),
            option_c=q["options"].get("C", ""),
            option_d=q["options"].get("D", ""),
            correct_answer=q["correct_answer"],
            explanation=q.get("explanation"),
        )
        if ok:
            added += 1
        else:
            duplicates += 1

    await state.clear()

    await callback.message.answer(
        "📥 Импорт аяқталды.\n"
        f"Тақырып: {topic} | Деңгей: {level}\n"
        f"Қосылған жаңа сұрақтар: {added}\n"
        f"Қайталанып, өткізілген сұрақтар: {duplicates}\n"
    )
    await callback.answer()


@dp.callback_query(
    AdminUpload.waiting_for_import_confirm,
    F.data == "import:cancel",
)
async def cancel_import(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Импорт болдырылмады. Ешбір сұрақ қосылмады.")
    await callback.answer()


# -----------------------------------------------------
# Тақырыпты жазуды қабылдау (файл жүктеу ағыны, 2-қадам)
# -----------------------------------------------------

@dp.message(AdminUpload.waiting_for_topic)
async def process_upload_topic(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    topic = (message.text or "").strip()

    if not topic:
        await message.answer("Тақырып атауын мәтін түрінде жазыңыз.")
        return

    await state.update_data(pending_topic=topic)
    await state.set_state(AdminUpload.waiting_for_level)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=lvl, callback_data=f"uplevel:{lvl}")]
            for lvl in questions.LEVELS
        ]
    )
    await message.answer("Деңгейді таңдаңыз:", reply_markup=kb)


# -----------------------------------------------------
# Деңгейді таңдауды қабылдау (файл жүктеу ағыны, 3-қадам)
# -----------------------------------------------------

@dp.callback_query(
    AdminUpload.waiting_for_level,
    F.data.startswith("uplevel:")
)
async def process_upload_level(
    callback: types.CallbackQuery,
    state: FSMContext
):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    level = callback.data.split("uplevel:", 1)[1]
    await state.update_data(pending_level=level)

    data = await state.get_data()
    topic = data.get("pending_topic")
    pending = data.get("pending_questions", [])
    count = len(pending)

    # AI Converter нәтижесі: бөлек кілт файлы сұралмайды, тікелей
    # "Import нәтижесі" экранына өтеміз.
    if data.get("import_source") == "ai":
        await _present_ai_import_summary(
            callback.message, state, pending, topic, level,
            data.get("ai_report") or {},
        )
        await callback.answer()
        return

    # Файлдың өзінде ("Answer:/Correct:/Дұрыс жауап:") барлық
    # сұрақтың дұрыс жауабы табылған болса, бөлек кілт файлын
    # сұрамай-ақ тікелей растау экранына өтеміз.
    if questions.all_have_inline_answers(pending):
        await callback.message.answer(
            f"Тақырып: {topic}\nДеңгей: {level}\nСұрақ саны: {count}\n\n"
            "✅ Файлдың өзінен барлық дұрыс жауап табылды — бөлек "
            "кілт файлы қажет емес."
        )
        await _present_import_summary(
            callback.message, state, pending, topic, level,
            key={}, key_label="Табылған жауаптар",
            key_count=sum(1 for q in pending if q.get("inline_answer")),
        )
        await callback.answer()
        return

    await state.set_state(AdminUpload.waiting_for_key)

    await callback.message.answer(
        f"Тақырып: {topic}\n"
        f"Деңгей: {level}\n"
        f"Сұрақ саны: {count}\n\n"
        "Енді осы сұрақтарға сәйкес жауап кілтін (.txt) жіберіңіз."
    )
    await callback.answer()


# =========================================================
# Тест тапсыру: тақырып -> деңгей -> сұрақ саны -> квиз
# =========================================================

@dp.message(F.text == "📝 Тест тапсыру")
async def start_quiz(message: types.Message, state: FSMContext):
    topics = database.get_topics()

    if not topics:
        await message.answer(
            "Әзірше сұрақтар базасы бос. Әкімші сұрақ қоспаған."
        )
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=f"topic:{t}")]
            for t in topics
        ]
    )
    await message.answer("Тақырыпты таңдаңыз:", reply_markup=kb)


@dp.callback_query(F.data.startswith("topic:"))
async def choose_level(callback: types.CallbackQuery, state: FSMContext):
    topic = callback.data.split("topic:", 1)[1]

    levels = database.get_levels_for_topic(topic)

    if not levels:
        await callback.message.answer(
            "Бұл тақырып бойынша деңгей табылмады."
        )
        await callback.answer()
        return

    await state.update_data(quiz_topic=topic)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=lvl, callback_data=f"level:{lvl}")]
            for lvl in levels
        ]
    )
    await callback.message.answer("Деңгейді таңдаңыз:", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("level:"))
async def choose_count(callback: types.CallbackQuery, state: FSMContext):
    level = callback.data.split("level:", 1)[1]
    await state.update_data(quiz_level=level)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{size} сұрақ",
                callback_data=f"count:{size}",
            )]
            for size in QUIZ_SIZES
        ]
    )
    await callback.message.answer(
        "📊 Сұрақ санын таңдаңыз:", reply_markup=kb
    )
    await callback.answer()


async def _start_quiz_session(user_id, topic, level, count, state: FSMContext):
    """
    Тест сессиясын бастау: сұрақтарды алу, жауап нұсқаларын
    араластыру, FSM күйін орнату және бірінші сұрақты жіберу.
    begin_quiz (жаңа тест) пен retry_quiz (қайталау) екеуі де осы
    ортақ функцияны қолданады. Қайтарады: (ok: bool, warning: str|None)
    """
    rows = database.get_random_questions(topic, level, limit=count)

    if not rows:
        return False, "Бұл тақырып пен деңгей бойынша сұрақ табылмады."

    warning = None
    if len(rows) < count:
        warning = (
            f"⚠️ Сіз {count} сұрақ таңдадыңыз, бірақ бұл тақырып/деңгей "
            f"бойынша тек {len(rows)} сұрақ бар. Сол {len(rows)} "
            "сұрақпен жалғастырамыз."
        )

    quiz_questions = []
    for row in rows:
        orig_options = {
            "A": row[2], "B": row[3], "C": row[4], "D": row[5],
        }
        orig_correct = row[6]

        # Жауап нұсқаларының көрсету ретін араластырамыз — дұрыс
        # жауаптың мәтіні өзгермейді, тек оның әрпі/орны ауысады.
        pairs = list(orig_options.items())
        random.shuffle(pairs)

        shuffled_options = {}
        new_correct = None
        for new_letter, (orig_letter, text) in zip(OPTION_LETTERS, pairs):
            shuffled_options[new_letter] = text
            if orig_letter == orig_correct:
                new_correct = new_letter

        quiz_questions.append({
            "id": row[0],
            "question": row[1],
            "options": shuffled_options,
            "correct": new_correct,
            "explanation": (
                ai_tutor.sanitize_ai_text(row[7]) if row[7] else row[7]
            ),
        })

    LAST_QUIZ_CONFIG[user_id] = {
        "topic": topic, "level": level, "count": len(quiz_questions),
    }

    await state.update_data(
        quiz_topic=topic,
        quiz_level=level,
        quiz_questions=quiz_questions,
        quiz_index=0,
        quiz_score=0,
        quiz_total=len(quiz_questions),
        quiz_wrong=[],
    )
    await state.set_state(QuizSession.in_progress)

    if warning:
        await bot.send_message(user_id, warning)

    await bot.send_message(
        user_id,
        f"📝 Тест басталды!\nТақырып: {topic} | Деңгей: {level}\n"
        f"Барлығы {len(quiz_questions)} сұрақ. Сәттілік тілейміз! 🚀"
    )

    await send_next_question(user_id, state)
    return True, warning


@dp.callback_query(F.data.startswith("count:"))
async def begin_quiz(callback: types.CallbackQuery, state: FSMContext):
    try:
        count = int(callback.data.split("count:", 1)[1])
    except ValueError:
        count = 0

    if count not in QUIZ_SIZES:
        await callback.message.answer(
            "⚠️ Жарамсыз сұрақ саны. Өтініш, батырмалардың бірін таңдаңыз."
        )
        await callback.answer()
        return

    data = await state.get_data()
    topic = data.get("quiz_topic")
    level = data.get("quiz_level")

    ok, warning = await _start_quiz_session(
        callback.from_user.id, topic, level, count, state
    )
    if not ok:
        await callback.message.answer(warning)
        await state.clear()

    await callback.answer()


async def send_next_question(user_id: int, state: FSMContext):
    data = await state.get_data()
    quiz_questions = data.get("quiz_questions", [])
    index = data.get("quiz_index", 0)
    total = data.get("quiz_total", 0)

    if index >= len(quiz_questions):
        score = data.get("quiz_score", 0)
        wrong_list = data.get("quiz_wrong", [])
        topic = data.get("quiz_topic")
        level = data.get("quiz_level")

        database.record_test_completion(user_id, correct=score, total=total)

        result = questions.calculate_test_result(score, total)

        result_text = (
            "🏁 Test finished!\n\n"
            "📊 Result:\n"
            f"✅ Correct: {result['correct']}\n"
            f"❌ Incorrect: {result['incorrect']}\n"
            f"📈 Score: {result['percent']}%\n\n"
            f"🎯 Level: {level}\n"
            f"📚 Topic: {topic}\n\n"
            f"{result['tag']}"
        )

        kb_rows = []
        if wrong_list:
            LAST_QUIZ_RESULTS[user_id] = wrong_list
            kb_rows.append([InlineKeyboardButton(
                text="🔎 Қателерімді көру",
                callback_data="review_wrong",
            )])
        kb_rows.append([InlineKeyboardButton(
            text="🔄 Қайталап көру", callback_data="retry_quiz",
        )])
        kb_rows.append([InlineKeyboardButton(
            text="🏠 Басты мәзір", callback_data="main_menu",
        )])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

        await bot.send_message(user_id, result_text, reply_markup=kb)
        await state.clear()
        return

    q = quiz_questions[index]
    options_text = [q["options"][letter] for letter in OPTION_LETTERS]
    correct_index = OPTION_LETTERS.index(q["correct"])

    progress_bar = questions.build_progress_bar(index + 1, total)
    poll_question = (
        f"📘 {index + 1}/{total}\n{progress_bar}\n\n{q['question']}"
    )

    sent_poll = await bot.send_poll(
        chat_id=user_id,
        question=poll_question,
        options=options_text,
        type="quiz",
        correct_option_id=correct_index,
        is_anonymous=False,
    )

    ACTIVE_POLLS[sent_poll.poll.id] = {
        "user_id": user_id,
        "topic": data.get("quiz_topic"),
        "question": q["question"],
        "options": q["options"],
        "correct_letter": q["correct"],
        "correct_text": options_text[correct_index],
        "explanation": q.get("explanation"),
    }


# =========================
# Викторина жауабын тексеру
# =========================

@dp.poll_answer()
async def handle_poll_answer(
    poll_answer: types.PollAnswer,
    state: FSMContext
):
    poll_info = ACTIVE_POLLS.pop(poll_answer.poll_id, None)

    if not poll_info:
        return

    user_id = poll_answer.user.id
    selected = (
        poll_answer.option_ids[0] if poll_answer.option_ids else None
    )
    correct_index = OPTION_LETTERS.index(poll_info["correct_letter"])

    data = await state.get_data()

    if selected == correct_index:
        data["quiz_score"] = data.get("quiz_score", 0) + 1
        await bot.send_message(
            user_id,
            questions.generate_correct_answer_feedback(),
        )
    else:
        user_letter = (
            OPTION_LETTERS[selected] if selected is not None else "—"
        )

        if poll_info.get("explanation"):
            explanation = (
                f"❌ Қате жауап\n\n"
                f"✅ Дұрыс жауап: {poll_info['correct_letter']}) "
                f"{poll_info['correct_text']}\n\n"
                f"📌 {poll_info['explanation']}"
            )
        else:
            explanation = questions.generate_feedback(
                question=poll_info["question"],
                options=poll_info["options"],
                user_answer=user_letter,
                correct_answer=poll_info["correct_letter"],
                topic=poll_info["topic"],
                level=data.get("quiz_level"),
            )

        ai_kb = None
        if ai_tutor.is_configured():
            AI_EXPLAIN_CONTEXT[poll_answer.poll_id] = {
                "question": poll_info["question"],
                "options": poll_info["options"],
                "correct_letter": poll_info["correct_letter"],
                "user_letter": user_letter,
                "level": data.get("quiz_level"),
            }
            ai_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="💡 Неге?",
                    callback_data=f"ai_why:{poll_answer.poll_id}",
                )
            ]])

        await bot.send_message(user_id, explanation, reply_markup=ai_kb)

        wrong_list = data.get("quiz_wrong", [])
        wrong_list.append({
            "question": poll_info["question"],
            "user_letter": user_letter,
            "user_text": poll_info["options"].get(user_letter, "—"),
            "correct_letter": poll_info["correct_letter"],
            "correct_text": poll_info["correct_text"],
            "explanation": explanation,
        })
        data["quiz_wrong"] = wrong_list

    data["quiz_index"] = data.get("quiz_index", 0) + 1
    await state.update_data(**data)

    await send_next_question(user_id, state)


@dp.callback_query(F.data.startswith("ai_why:"))
async def ai_explain_quiz_answer(callback: types.CallbackQuery):
    poll_id = callback.data.split("ai_why:", 1)[1]
    ctx = AI_EXPLAIN_CONTEXT.pop(poll_id, None)

    if not ctx:
        await callback.answer(
            "Бұл сұрақ бойынша контекст табылмады.", show_alert=True
        )
        return

    await callback.answer("🤔 AI ойланып жатыр...")

    answer = await asyncio.to_thread(
        ai_tutor.explain_quiz_answer,
        question=ctx["question"],
        options=ctx["options"],
        correct_letter=ctx["correct_letter"],
        user_letter=ctx["user_letter"],
        level=ctx["level"],
    )
    await bot.send_message(callback.from_user.id, f"🤖 {answer}")


# =========================
# Қателерді қарап шығу
# =========================

@dp.callback_query(F.data == "review_wrong")
async def review_wrong_answers(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    wrong_list = LAST_QUIZ_RESULTS.get(user_id)

    if not wrong_list:
        await callback.message.answer("Қате жауаптар табылмады.")
        await callback.answer()
        return

    chunks = []
    current = ""

    for idx, item in enumerate(wrong_list, 1):
        block = (
            f"❓ Question {idx}\n"
            f"{item['question']}\n"
            f"Сіздің жауабыңыз: {item['user_letter']}) {item['user_text']}\n\n"
            f"{item['explanation']}\n\n"
        )
        if len(current) + len(block) > 3500:
            chunks.append(current)
            current = ""
        current += block

    if current:
        chunks.append(current)

    for chunk in chunks:
        await bot.send_message(user_id, chunk)

    await callback.answer()


@dp.callback_query(F.data == "retry_quiz")
async def retry_quiz(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    config = LAST_QUIZ_CONFIG.get(user_id)

    if not config:
        await callback.message.answer(
            "Алдыңғы тест конфигурациясы табылмады. "
            "\"📝 Тест тапсыру\" арқылы қайта бастаңыз."
        )
        await callback.answer()
        return

    topic = config["topic"]
    level = config["level"]
    count = config.get("count", QUIZ_SIZES[0])

    levels = database.get_levels_for_topic(topic)
    if not levels or level not in levels:
        await callback.message.answer(
            "Бұл тақырып/деңгей енді қолжетімсіз. "
            "\"📝 Тест тапсыру\" арқылы қайта бастаңыз."
        )
        await callback.answer()
        return

    ok, warning = await _start_quiz_session(user_id, topic, level, count, state)
    if not ok:
        await callback.message.answer(warning)

    await callback.answer()


@dp.callback_query(F.data == "main_menu")
async def back_to_main_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "Басты мәзір:", reply_markup=main_menu(callback.from_user.id)
    )
    await callback.answer()


# =========================================================
# 🔐 АДМИН ПАНЕЛІ
# =========================================================

ADMIN_PAGE_SIZE = 5

# Ағымдағы браузер сүзгісі: user_id -> {"topic": str|None, "level": str|None}
ADMIN_LIST_FILTER = {}


def _admin_menu_kb():
    rows = [
        [InlineKeyboardButton(text="➕ Quiz сұрағын қосу", callback_data="adm:add")],
        [InlineKeyboardButton(text="🤖 AI арқылы сұрақ генерациялау", callback_data="adm:genai")],
        [InlineKeyboardButton(text="📋 Quiz сұрақтарын көру", callback_data="adm:list:0")],
        [InlineKeyboardButton(text="📥 Сұрақтарды файлдан жүктеу", callback_data="adm:import")],
        [InlineKeyboardButton(text="📊 Quiz статистикасы", callback_data="adm:stats")],
        [InlineKeyboardButton(text="🔙 Басты мәзір", callback_data="adm:back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_admin_menu(target_message):
    await target_message.answer(
        "🔐 Админ панелі\n\nНе істегіңіз келеді?",
        reply_markup=_admin_menu_kb(),
    )


@dp.message(Command("admin"))
async def admin_command(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Бұл бөлімге кіруге рұқсатыңыз жоқ.")
        return
    await state.clear()
    await _show_admin_menu(message)


@dp.message(F.text == "🔐 Админ панелі")
async def admin_panel_button(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Бұл бөлімге кіруге рұқсатыңыз жоқ.")
        return
    await state.clear()
    await _show_admin_menu(message)


@dp.callback_query(F.data == "adm:back")
async def admin_back(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await callback.message.answer(
        "Басты мәзір:", reply_markup=main_menu(callback.from_user.id)
    )
    await callback.answer()


@dp.callback_query(F.data == "adm:menu")
async def admin_menu_cb(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await _show_admin_menu(callback.message)
    await callback.answer()


@dp.callback_query(F.data == "adm:import")
async def admin_import_hint(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.message.answer(
        "📥 Сұрақтар файлын осында жіберіңіз "
        "(.docx/.txt/.pdf/.xlsx/.csv/.rtf/.odt).\n"
        "Одан кейін тақырып пен деңгейді сұраймын, соңында жауап "
        "кілтін (.txt) жібересіз.\n\n"
        "Егер файлда A/B/C/D нұсқалары жоқ, тек тапсырмалар мен Answers "
        "бөлімі болса, AI оны автоматты түрде толық Quiz-қа айналдырады."
    )
    await callback.answer()


@dp.callback_query(F.data == "adm:stats")
async def admin_stats_cb(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    stats = database.get_admin_stats()

    by_topic = "\n".join(
        f"  • {t}: {c}" for t, c in stats["by_topic"]
    ) or "  (жоқ)"
    by_level = "\n".join(
        f"  • {lvl}: {c}" for lvl, c in stats["by_level"]
    ) or "  (жоқ)"

    text = (
        "📊 Quiz статистикасы\n\n"
        f"👥 Пайдаланушылар: {stats['total_users']}\n"
        f"📝 Барлық сұрақтар: {stats['total_questions']}\n\n"
        f"📚 Тақырып бойынша:\n{by_topic}\n\n"
        f"📊 Деңгей бойынша:\n{by_level}\n\n"
        f"🎯 Тапсырылған тесттер: {stats['tests_completed']}\n"
        f"📈 Орташа нәтиже: {stats['average_percent']}%"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Артқа", callback_data="adm:menu")],
    ])
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


# ---------------------------------------------------------
# 📋 Сұрақтарды қарау (парақтау + сүзгі + edit/delete батырмалары)
# ---------------------------------------------------------

def _question_snippet(q, max_len=60):
    text = q["question_text"]
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


async def _render_admin_list(target_message, user_id, page):
    filt = ADMIN_LIST_FILTER.get(user_id, {})
    topic = filt.get("topic")
    level = filt.get("level")

    total = database.count_all_questions(topic=topic, level=level)
    offset = page * ADMIN_PAGE_SIZE
    items = database.list_questions(
        topic=topic, level=level, offset=offset, limit=ADMIN_PAGE_SIZE
    )

    filter_line = (
        f"Сүзгі: {topic or '—'} / {level or '—'}\n\n"
    )

    if not items:
        text = f"📋 Quiz сұрақтары\n\n{filter_line}Сұрақ табылмады."
        kb_rows = []
    else:
        lines = [f"📋 Quiz сұрақтары ({total})\n", filter_line]
        for q in items:
            lines.append(
                f"#{q['id']} [{q['topic']} | {q['level']}]\n"
                f"{_question_snippet(q)}\n"
                f"✅ {q['correct_answer']}\n"
            )
        text = "\n".join(lines)

        kb_rows = []
        for q in items:
            kb_rows.append([
                InlineKeyboardButton(
                    text=f"✏️ #{q['id']}", callback_data=f"adm:edit:{q['id']}"
                ),
                InlineKeyboardButton(
                    text=f"🗑 #{q['id']}", callback_data=f"adm:del:{q['id']}"
                ),
            ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(
            text="⬅️ Previous", callback_data=f"adm:list:{page - 1}"
        ))
    if offset + ADMIN_PAGE_SIZE < total:
        nav_row.append(InlineKeyboardButton(
            text="➡️ Next", callback_data=f"adm:list:{page + 1}"
        ))
    if nav_row:
        kb_rows.append(nav_row)

    kb_rows.append([
        InlineKeyboardButton(text="📚 Topic", callback_data="adm:ftopic:0"),
        InlineKeyboardButton(text="📊 Level", callback_data="adm:flevel"),
    ])
    if topic or level:
        kb_rows.append([
            InlineKeyboardButton(text="✖️ Сүзгіні тазалау", callback_data="adm:fclear")
        ])
    kb_rows.append([InlineKeyboardButton(text="🔙 Артқа", callback_data="adm:menu")])

    await target_message.answer(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )


@dp.callback_query(F.data.startswith("adm:list:"))
async def admin_list(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    page = int(callback.data.split(":")[-1])
    await _render_admin_list(callback.message, callback.from_user.id, page)
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:ftopic:"))
async def admin_filter_topic_menu(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    topics = database.get_topics()
    kb_rows = [
        [InlineKeyboardButton(text=t, callback_data=f"adm:settopic:{i}")]
        for i, t in enumerate(topics)
    ]
    kb_rows.append([InlineKeyboardButton(text="🔙 Артқа", callback_data="adm:list:0")])
    # Тақырып атауларын callback_data-ға сыйдыру үшін индекс арқылы сақтаймыз
    await callback.message.answer(
        "📚 Тақырыпты таңдаңыз:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    ADMIN_LIST_FILTER.setdefault(callback.from_user.id, {})["_topics_cache"] = topics
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:settopic:"))
async def admin_set_topic_filter(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    user_id = callback.from_user.id
    idx = int(callback.data.split(":")[-1])
    topics = ADMIN_LIST_FILTER.get(user_id, {}).get("_topics_cache", [])

    if idx >= len(topics):
        await callback.answer()
        return

    ADMIN_LIST_FILTER.setdefault(user_id, {})["topic"] = topics[idx]
    await _render_admin_list(callback.message, user_id, 0)
    await callback.answer()


@dp.callback_query(F.data == "adm:flevel")
async def admin_filter_level_menu(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    kb_rows = [
        [InlineKeyboardButton(text=lvl, callback_data=f"adm:setlevelf:{lvl}")]
        for lvl in questions.LEVELS
    ]
    kb_rows.append([InlineKeyboardButton(text="🔙 Артқа", callback_data="adm:list:0")])
    await callback.message.answer(
        "📊 Деңгейді таңдаңыз:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:setlevelf:"))
async def admin_set_level_filter(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    user_id = callback.from_user.id
    level = callback.data.split("adm:setlevelf:", 1)[1]
    ADMIN_LIST_FILTER.setdefault(user_id, {})["level"] = level
    await _render_admin_list(callback.message, user_id, 0)
    await callback.answer()


@dp.callback_query(F.data == "adm:fclear")
async def admin_clear_filter(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    ADMIN_LIST_FILTER[callback.from_user.id] = {}
    await _render_admin_list(callback.message, callback.from_user.id, 0)
    await callback.answer()


# ---------------------------------------------------------
# ✏️ Сұрақты өзгерту
# ---------------------------------------------------------

_EDIT_FIELD_LABELS = {
    "question_text": "Сұрақ мәтіні",
    "option_a": "A) нұсқасы",
    "option_b": "B) нұсқасы",
    "option_c": "C) нұсқасы",
    "option_d": "D) нұсқасы",
    "topic": "Тақырып",
    "explanation": "Түсіндірме",
}


def _format_question_full(q):
    return (
        f"#{q['id']} [{q['topic']} | {q['level']}]\n\n"
        f"{q['question_text']}\n\n"
        f"A) {q['option_a']}\n"
        f"B) {q['option_b']}\n"
        f"C) {q['option_c']}\n"
        f"D) {q['option_d']}\n\n"
        f"✅ Correct answer: {q['correct_answer']}\n"
        f"💡 Explanation: {q['explanation'] or '(автоматты жасалады)'}"
    )


@dp.callback_query(F.data.startswith("adm:edit:"))
async def admin_edit_menu(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    qid = int(callback.data.split(":")[-1])
    q = database.get_question_by_id(qid)
    if not q:
        await callback.message.answer("⚠️ Бұл сұрақ табылмады (өшірілген болуы мүмкін).")
        await callback.answer()
        return

    kb_rows = [
        [InlineKeyboardButton(text="📝 Сұрақ мәтіні", callback_data=f"adm:ef:{qid}:question_text")],
        [
            InlineKeyboardButton(text="A)", callback_data=f"adm:ef:{qid}:option_a"),
            InlineKeyboardButton(text="B)", callback_data=f"adm:ef:{qid}:option_b"),
            InlineKeyboardButton(text="C)", callback_data=f"adm:ef:{qid}:option_c"),
            InlineKeyboardButton(text="D)", callback_data=f"adm:ef:{qid}:option_d"),
        ],
        [InlineKeyboardButton(text="✅ Дұрыс жауап", callback_data=f"adm:ecorrect:{qid}")],
        [InlineKeyboardButton(text="📚 Тақырып", callback_data=f"adm:ef:{qid}:topic")],
        [InlineKeyboardButton(text="📊 Деңгей", callback_data=f"adm:elevel:{qid}")],
        [InlineKeyboardButton(text="💡 Түсіндірме", callback_data=f"adm:ef:{qid}:explanation")],
        [InlineKeyboardButton(text="🔙 Артқа", callback_data="adm:list:0")],
    ]
    await callback.message.answer(
        _format_question_full(q) + "\n\nНені өзгертесіз?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:ef:"))
async def admin_edit_field_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    _, _, qid, field = callback.data.split(":")
    await state.update_data(edit_qid=int(qid), edit_field=field)
    await state.set_state(AdminPanel.edit_value)

    label = _EDIT_FIELD_LABELS.get(field, field)
    await callback.message.answer(f"{label} үшін жаңа мәнді жазыңыз:")
    await callback.answer()


@dp.message(AdminPanel.edit_value)
async def admin_edit_value_received(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    new_value = (message.text or "").strip()
    if not new_value:
        await message.answer("Мән бос болмауы керек. Қайта жазыңыз:")
        return

    data = await state.get_data()
    qid = data.get("edit_qid")
    field = data.get("edit_field")

    q = database.get_question_by_id(qid)
    if not q:
        await message.answer("⚠️ Бұл сұрақ табылмады.")
        await state.clear()
        return

    preview = dict(q)
    preview[field] = new_value

    await state.update_data(edit_new_value=new_value)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сақтау", callback_data="adm:editconfirm")],
        [InlineKeyboardButton(text="❌ Болдырмау", callback_data="adm:editcancel")],
    ])
    await message.answer(
        "📝 Алдын ала қарау:\n\n" + _format_question_full(preview),
        reply_markup=kb,
    )


@dp.callback_query(AdminPanel.edit_value, F.data == "adm:editconfirm")
async def admin_edit_confirm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    qid = data.get("edit_qid")
    field = data.get("edit_field")
    new_value = data.get("edit_new_value")

    ok = database.update_question(qid, **{field: new_value})
    await state.clear()

    if ok:
        await callback.message.answer("✅ Сұрақ сәтті жаңартылды.")
    else:
        await callback.message.answer(
            "⚠️ Жаңарту сәтсіз аяқталды (мүмкін, мәтін басқа сұрақпен қайталанады)."
        )
    await callback.answer()


@dp.callback_query(AdminPanel.edit_value, F.data == "adm:editcancel")
async def admin_edit_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Болдырылмады, ешбір өзгеріс сақталмады.")
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:ecorrect:"))
async def admin_edit_correct_menu(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    qid = callback.data.split(":")[-1]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=l, callback_data=f"adm:setcorrect:{qid}:{l}")
        for l in OPTION_LETTERS
    ]])
    await callback.message.answer("Дұрыс жауапты таңдаңыз:", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:setcorrect:"))
async def admin_set_correct(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    _, _, qid, letter = callback.data.split(":")
    ok = database.update_question(int(qid), correct_answer=letter)
    await callback.message.answer(
        "✅ Дұрыс жауап жаңартылды." if ok else "⚠️ Жаңарту сәтсіз."
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:elevel:"))
async def admin_edit_level_menu(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    qid = callback.data.split(":")[-1]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=lvl, callback_data=f"adm:setlevel:{qid}:{lvl}")]
        for lvl in questions.LEVELS
    ])
    await callback.message.answer("Жаңа деңгейді таңдаңыз:", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:setlevel:"))
async def admin_set_level(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    _, _, qid, level = callback.data.split(":")
    ok = database.update_question(int(qid), level=level)
    await callback.message.answer(
        "✅ Деңгей жаңартылды." if ok else "⚠️ Жаңарту сәтсіз."
    )
    await callback.answer()


# ---------------------------------------------------------
# 🗑 Сұрақты жою
# ---------------------------------------------------------

@dp.callback_query(F.data.startswith("adm:del:"))
async def admin_delete_confirm(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    qid = int(callback.data.split(":")[-1])
    q = database.get_question_by_id(qid)
    if not q:
        await callback.message.answer("⚠️ Бұл сұрақ табылмады.")
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Иә, өшіру", callback_data=f"adm:delyes:{qid}")],
        [InlineKeyboardButton(text="❌ Жоқ", callback_data="adm:list:0")],
    ])
    await callback.message.answer(
        f"⚠️ Сұрақты өшіруге сенімдісіз бе?\n\n{_format_question_full(q)}",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm:delyes:"))
async def admin_delete_confirmed(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    qid = int(callback.data.split(":")[-1])
    ok = database.delete_question(qid)
    await callback.message.answer(
        "✅ Сұрақ сәтті өшірілді." if ok else "⚠️ Сұрақ табылмады (бұрын өшірілген болуы мүмкін)."
    )
    await callback.answer()


# ---------------------------------------------------------
# ➕ Жаңа сұрақ қосу (қадам бойынша)
# ---------------------------------------------------------

@dp.callback_query(F.data == "adm:add")
async def admin_add_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.set_state(AdminPanel.add_topic)
    await callback.message.answer("➕ Жаңа сұрақ қосу\n\n📚 Тақырыпты жазыңыз:")
    await callback.answer()


@dp.message(AdminPanel.add_topic)
async def admin_add_topic(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    topic = (message.text or "").strip()
    if not topic:
        await message.answer("Тақырып бос болмауы керек. Қайта жазыңыз:")
        return

    await state.update_data(new_topic=topic)
    await state.set_state(AdminPanel.add_level)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=lvl, callback_data=f"addlvl:{lvl}")]
        for lvl in questions.LEVELS
    ])
    await message.answer("📊 Деңгейді таңдаңыз:", reply_markup=kb)


@dp.callback_query(AdminPanel.add_level, F.data.startswith("addlvl:"))
async def admin_add_level(callback: types.CallbackQuery, state: FSMContext):
    level = callback.data.split("addlvl:", 1)[1]
    await state.update_data(new_level=level)
    await state.set_state(AdminPanel.add_question_text)
    await callback.message.answer("📝 Сұрақты енгізіңіз:")
    await callback.answer()


@dp.message(AdminPanel.add_question_text)
async def admin_add_question_text(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Сұрақ мәтіні бос болмауы керек. Қайта жазыңыз:")
        return
    await state.update_data(
        new_question=text, new_options={}, option_index=0
    )
    await state.set_state(AdminPanel.add_options)
    await message.answer("A) жауапты енгізіңіз:")


@dp.message(AdminPanel.add_options)
async def admin_add_option(message: types.Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value:
        await message.answer("Жауап бос болмауы керек. Қайта жазыңыз:")
        return

    data = await state.get_data()
    idx = data.get("option_index", 0)
    options = data.get("new_options", {})
    options[OPTION_LETTERS[idx]] = value
    idx += 1

    await state.update_data(new_options=options, option_index=idx)

    if idx < 4:
        await message.answer(f"{OPTION_LETTERS[idx]}) жауапты енгізіңіз:")
        return

    await state.set_state(AdminPanel.add_correct)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=l, callback_data=f"addcorrect:{l}")
        for l in OPTION_LETTERS
    ]])
    await message.answer("Дұрыс жауапты таңдаңыз:", reply_markup=kb)


@dp.callback_query(AdminPanel.add_correct, F.data.startswith("addcorrect:"))
async def admin_add_correct(callback: types.CallbackQuery, state: FSMContext):
    letter = callback.data.split("addcorrect:", 1)[1]
    await state.update_data(new_correct=letter)
    await state.set_state(AdminPanel.add_explanation)
    await callback.message.answer(
        "💡 Түсіндірмені енгізіңіз (немесе \"-\" деп жазып өткізіп жіберіңіз):"
    )
    await callback.answer()


@dp.message(AdminPanel.add_explanation)
async def admin_add_explanation(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    explanation = None if text == "-" else text

    await state.update_data(new_explanation=explanation)
    await state.set_state(AdminPanel.add_preview)

    data = await state.get_data()
    preview = {
        "id": "—",
        "topic": data["new_topic"],
        "level": data["new_level"],
        "question_text": data["new_question"],
        "option_a": data["new_options"]["A"],
        "option_b": data["new_options"]["B"],
        "option_c": data["new_options"]["C"],
        "option_d": data["new_options"]["D"],
        "correct_answer": data["new_correct"],
        "explanation": explanation,
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сақтау", callback_data="addconfirm")],
        [InlineKeyboardButton(text="❌ Болдырмау", callback_data="addcancel")],
    ])
    await message.answer(
        "📝 Сұрақты тексеріңіз:\n\n" + _format_question_full(preview),
        reply_markup=kb,
    )


@dp.callback_query(AdminPanel.add_preview, F.data == "addconfirm")
async def admin_add_confirm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ok = database.add_question(
        topic=data["new_topic"],
        level=data["new_level"],
        question_text=data["new_question"],
        option_a=data["new_options"]["A"],
        option_b=data["new_options"]["B"],
        option_c=data["new_options"]["C"],
        option_d=data["new_options"]["D"],
        correct_answer=data["new_correct"],
        explanation=data.get("new_explanation"),
    )
    await state.clear()

    if ok:
        await callback.message.answer("✅ Жаңа сұрақ сәтті қосылды.")
    else:
        await callback.message.answer(
            "⚠️ Бұл сұрақ базада бұрыннан бар (қайталанады), қосылмады."
        )
    await callback.answer()


@dp.callback_query(AdminPanel.add_preview, F.data == "addcancel")
async def admin_add_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Болдырылмады, сұрақ қосылмады.")
    await callback.answer()


# ---------------------------------------------------------
# 🤖 AI арқылы сұрақ генерациялау
# ---------------------------------------------------------

@dp.callback_query(F.data == "adm:genai")
async def admin_genai_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    if not ai_tutor.is_configured():
        await callback.message.answer(
            "⚠️ AI функциясы әзірге қосылмаған (GEMINI_API_KEY "
            "орнатылмаған). Әкімшіге хабарласыңыз."
        )
        await callback.answer()
        return

    await state.set_state(AdminPanel.gen_topic)
    await callback.message.answer("🤖 AI сұрақ генерациялау\n\n📚 Тақырыпты жазыңыз:")
    await callback.answer()


@dp.message(AdminPanel.gen_topic)
async def admin_genai_topic(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    topic = (message.text or "").strip()
    if not topic:
        await message.answer("Тақырып бос болмауы керек. Қайта жазыңыз:")
        return

    await state.update_data(gen_topic=topic)
    await state.set_state(AdminPanel.gen_level)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=lvl, callback_data=f"genlvl:{lvl}")]
        for lvl in questions.LEVELS
    ])
    await message.answer("📊 Деңгейді таңдаңыз:", reply_markup=kb)


@dp.callback_query(AdminPanel.gen_level, F.data.startswith("genlvl:"))
async def admin_genai_level(callback: types.CallbackQuery, state: FSMContext):
    level = callback.data.split("genlvl:", 1)[1]
    await state.update_data(gen_level=level)
    await state.set_state(AdminPanel.gen_count)
    await callback.message.answer("📝 Қанша сұрақ генерациялау керек? (1–30 санын жазыңыз):")
    await callback.answer()


@dp.message(AdminPanel.gen_count)
async def admin_genai_count(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit() or not (1 <= int(text) <= 30):
        await message.answer("1-ден 30-ға дейінгі бүтін сан жазыңыз:")
        return

    count = int(text)
    data = await state.get_data()
    topic = data["gen_topic"]
    level = data["gen_level"]

    thinking = await message.answer("🤔 AI сұрақтарды генерациялап жатыр...")

    generated, error = await asyncio.to_thread(
        ai_tutor.generate_questions, topic, level, count
    )

    try:
        await thinking.delete()
    except Exception:
        pass

    if error:
        await message.answer(error)
        return

    await state.update_data(gen_generated=generated)
    await state.set_state(AdminPanel.gen_preview)

    preview_lines = [
        f"🤖 {len(generated)} сұрақ дайын (сұралған: {count}).\n"
    ]
    for i, q in enumerate(generated[:3], 1):
        preview_lines.append(
            f"{i}. {q['question']}\n"
            f"   A) {q['options']['A']}\n"
            f"   B) {q['options']['B']}\n"
            f"   C) {q['options']['C']}\n"
            f"   D) {q['options']['D']}\n"
            f"   ✅ {q['correct_answer']}\n"
        )
    if len(generated) > 3:
        preview_lines.append(f"... және тағы {len(generated) - 3} сұрақ.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Барлығын сақтау", callback_data="genconfirm")],
        [InlineKeyboardButton(text="❌ Болдырмау", callback_data="gencancel")],
    ])
    await message.answer("\n".join(preview_lines), reply_markup=kb)


@dp.callback_query(AdminPanel.gen_preview, F.data == "genconfirm")
async def admin_genai_confirm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    generated = data.get("gen_generated", [])
    topic = data["gen_topic"]
    level = data["gen_level"]

    added = 0
    duplicates = 0
    for q in generated:
        ok = database.add_question(
            topic=topic,
            level=level,
            question_text=q["question"],
            option_a=q["options"]["A"],
            option_b=q["options"]["B"],
            option_c=q["options"]["C"],
            option_d=q["options"]["D"],
            correct_answer=q["correct_answer"],
            explanation=q.get("explanation"),
        )
        if ok:
            added += 1
        else:
            duplicates += 1

    await state.clear()
    await callback.message.answer(
        f"✅ Сақталды.\nТақырып: {topic} | Деңгей: {level}\n"
        f"Қосылған жаңа сұрақтар: {added}\n"
        f"Қайталанып, өткізілген сұрақтар: {duplicates}"
    )
    await callback.answer()


@dp.callback_query(AdminPanel.gen_preview, F.data == "gencancel")
async def admin_genai_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Болдырылмады, сұрақтар сақталмады.")
    await callback.answer()


# =========================
# Ботты іске қосу
# =========================

async def main():

    database.init_db()

    print("Бот сәтті қосылды...")

    await dp.start_polling(bot)


# =========================
# Бағдарламаны іске қосу
# =========================

if __name__ == "__main__":
    asyncio.run(main()) 