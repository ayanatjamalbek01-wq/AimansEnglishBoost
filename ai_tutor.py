"""
ai_tutor.py
Gemini API (google-genai SDK) арқылы жұмыс істейтін AI тьютор
модулі. Бұл модуль тек мыналар үшін қолданылады:

    - 🤖 Сұрақ қою (еркін сұрақ-жауап, мәтін немесе фото)
    - 💡 Неге? (тест сұрағына тереңірек AI түсіндірмесі, міндетті емес)
    - 🤖 AI арқылы сұрақ генерациялау (админ панелі)
    - 🔄 convert_exercise_to_quiz: A/B/C/D жоқ дайын тапсырма файлын
      (тапсырма + Answers бөлімі) толық Quiz сұрақтарына айналдыру
    - 🧹 sanitize_ai_text: AI мәтінінен Markdown таңбаларын тазалау

МАҢЫЗДЫ: негізгі Quiz логикасы (дұрыс/қате жауапты тексеру, ұпай
есептеу) бұл модульге ЕШҚАШАН тәуелді емес — бәрі database.py мен
questions.py арқылы жергілікті (local) есептеледі. Gemini тек
қосымша, міндетті емес мүмкіндіктер үшін шақырылады.

.env-де қажет:
    GEMINI_API_KEY=...          (міндетті — болмаса AI функциялары
                                  сөндірулі болады, бірақ бот өзі
                                  толық жұмыс істей береді)
    GEMINI_MODEL=gemini-3.1-flash-lite   (міндетті емес, әдепкі осы)
"""

import difflib
import json
import logging
import os
import re

from dotenv import load_dotenv

# Қорғаныс: бұл модуль қай жерден, қандай ретпен импортталса да
# GEMINI_API_KEY дұрыс оқылуы үшін өзі де .env жүктейді (негізгі
# жүктеу main.py-да болғанымен, бос ортада тапсырыс дұрыс болмайды).
load_dotenv()

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types


logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Ескерту: Google модельдерді жиі тоқтатады. Жаңа кілттерге
# gemini-2.5-flash қазірдің өзінде жабылған (2026 қазан айында
# толық тоқтатылады), сондықтан әдепкі ретінде тұрақты
# gemini-3.1-flash-lite қолданылады. Керек болса .env-де
# GEMINI_MODEL арқылы басқа модельге ауыстыруға болады.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()

_client = None

FRIENDLY_UNAVAILABLE = (
    "⚠️ AI қызметі уақытша қолжетімсіз. Біраз уақыттан кейін қайта "
    "көріңіз."
)
FRIENDLY_NOT_CONFIGURED = (
    "⚠️ AI функциясы әлі қосылмаған (GEMINI_API_KEY орнатылмаған). "
    "Бұл функция тек қарапайым тест функцияларына әсер етпейді."
)

_SYSTEM_INSTRUCTION = (
    "You are an English tutor inside a Telegram educational bot "
    "called English Boost. Help students learn English clearly and "
    "accurately. Adapt explanations to the student's level "
    "(Elementary, Pre-Intermediate, Intermediate, Upper-Intermediate, "
    "Advanced) when it is known. When explaining grammar: explain "
    "simply, give 1-3 examples, point out common mistakes, and do "
    "not make the explanation unnecessarily long. When solving a "
    "multiple-choice question: identify the correct answer, explain "
    "why it is correct, and briefly explain why the other options "
    "are incorrect when useful. When the user sends a photo: read "
    "the text from the image, identify the exercise, solve it, and "
    "explain the answer. If the image is unclear, explicitly say "
    "which part cannot be read instead of inventing text — never "
    "invent information that is not visible in the image. If the "
    "student asks something unrelated to English education, "
    "politely redirect them toward English learning. Never claim an "
    "answer is correct if you are uncertain. "
    "LANGUAGE RULE (CRITICAL, HIGHEST PRIORITY): Respond ONLY in "
    "Kazakh, unless the student's message is clearly in English. "
    "NEVER respond in Russian, even one word, even if the student "
    "writes in Russian. ТІЛ ЕРЕЖЕСІ: Жауапты тек қазақ тілінде жаз, "
    "орыс тілінде бір сөз де жазба. "
    "FORMAT RULE (CRITICAL): Write plain text only. Never use "
    "Markdown: no asterisks, no hash headings, no backticks, no "
    "underscores for emphasis, no tables, no code blocks. For "
    "lists use simple lines that start with a number or a bullet "
    "character."
)


def is_configured() -> bool:
    """GEMINI_API_KEY орнатылған ба (AI функциялары қолжетімді ме)."""
    return bool(GEMINI_API_KEY)


def _get_client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY орнатылмаған")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


_KAZAKH_LETTERS = set("әғқңөұүһӘҒҚҢӨҰҮҺ")


def _looks_russian(text: str) -> bool:
    """
    Мәтін кириллицамен жазылып, бірақ қазақ тіліне тән әріптері
    (ә, ғ, қ, ң, ө, ұ, ү, һ) мүлде жоқ немесе өте аз болса —
    ол орысша деп есептеледі. Ағылшын мәтін бұған кірмейді.
    """
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04FF")
    if cyr < 30:
        return False
    kaz = sum(1 for ch in text if ch in _KAZAKH_LETTERS)
    return kaz / cyr < 0.02


def _force_kazakh(text: str) -> str:
    """Орысша шыққан жауапты қазақшаға аударту (бір рет). Сәтсіз болса — түпнұсқа."""
    try:
        client = _get_client()
        config = genai_types.GenerateContentConfig(
            system_instruction=(
                "You are a translator. Translate the given text into "
                "natural, simple Kazakh. Keep English words, examples "
                "and quoted sentences unchanged. Output only the "
                "translation, no comments. Do not use markdown."
            ),
            temperature=0.1,
        )
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=text, config=config
        )
        translated = (resp.text or "").strip()
        if translated and not _looks_russian(translated):
            return translated
    except Exception:
        logger.exception("Қазақшаға аудару сәтсіз")
    return text


def _safe_generate(contents, system_instruction=_SYSTEM_INSTRUCTION,
                    temperature=0.4, response_mime_type=None,
                    max_output_tokens=None):
    """
    Gemini-ге сұраныс жіберудің ортақ, қауіпсіз (қате шықса
    crash болмайтын) орындаушысы. Сәтті болса (мәтін, error_message)
    -> (str, None) қайтарады, сәтсіз болса (None, friendly_message).
    """
    if not is_configured():
        return None, FRIENDLY_NOT_CONFIGURED

    try:
        client = _get_client()
        config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            response_mime_type=response_mime_type,
            max_output_tokens=max_output_tokens,
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )
        text = (response.text or "").strip()
        if not text:
            return None, (
                "⚠️ AI бос жауап қайтарды. Сұрағыңызды басқаша "
                "тұжырымдап қайта жіберіп көріңіз."
            )
        # Тек қарапайым мәтін жауаптарға (JSON емес): орысша шықса,
        # қазақшаға аудартамыз.
        if response_mime_type is None and _looks_russian(text):
            logger.warning("Gemini орысша жауап берді — қазақшаға аударылып жатыр")
            text = _force_kazakh(text)
        # Қолданушыға баратын кез келген қарапайым мәтін жауабынан
        # Markdown таңбаларын міндетті түрде тазалаймыз.
        if response_mime_type is None:
            text = sanitize_ai_text(text)
        return text, None

    except genai_errors.ClientError as exc:
        logger.exception("Gemini ClientError")
        status = getattr(exc, "code", None)
        if status == 401 or status == 403:
            return None, (
                "⚠️ AI қызметінің кілті жарамсыз. Әкімшіге хабарласыңыз."
            )
        if status == 429:
            return None, (
                "⚠️ AI қызметіне тым көп сұраныс жіберілді (лимит). "
                "Бір-екі минуттан кейін қайта көріңіз."
            )
        return None, FRIENDLY_UNAVAILABLE

    except genai_errors.ServerError:
        logger.exception("Gemini ServerError")
        return None, FRIENDLY_UNAVAILABLE

    except Exception:
        logger.exception("Gemini күтпеген қате")
        return None, FRIENDLY_UNAVAILABLE


def ask_tutor(user_text: str, level: str = None) -> str:
    """
    Еркін сұрақ-жауап режимі ("🤖 Сұрақ қою"). Әрдайым қолданушыға
    көрсетуге болатын дайын мәтін қайтарады (қате болса — түсінікті
    хабарлама, ешқашан exception лақтырмайды).
    """
    prompt = user_text
    if level:
        prompt = f"[Student level: {level}]\n{user_text}"

    text, error = _safe_generate(prompt)
    return text if text else error


def solve_photo_exercise(image_bytes: bytes, mime_type: str, level: str = None) -> str:
    """Фотодағы (тапсырма/жаттығу) мәтінді оқып, шешіп, түсіндіру."""
    instruction = (
        "Read this image of an English exercise or question. Solve "
        "it and explain the answer clearly."
    )
    if level:
        instruction = f"[Student level: {level}] {instruction}"

    part = genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    text, error = _safe_generate([instruction, part])
    return text if text else error


def solve_document_exercise(text: str, level: str = None) -> str:
    """
    Файлдан (docx/txt/pdf/т.б.) алынған мәтіндегі жаттығуды шешіп,
    түсіндіру ("🤖 Сұрақ қою" режимінде файл жіберілгенде).
    """
    instruction = (
        "The following text was extracted from a file the student "
        "uploaded. It may contain one or more English exercises or "
        "questions (multiple-choice, fill-in-the-blank, grammar, "
        "vocabulary, matching, true/false, etc.). Identify the "
        "exercise(s), solve them, and explain the answers clearly. "
        "If the text is unclear or incomplete in places, say so "
        "instead of inventing content."
    )
    if level:
        instruction = f"[Student level: {level}] {instruction}"

    # Өте ұзын файлдарда шектен тыс токен/шығынды болдырмау үшін
    # мәтінді ақылға қонымды шамамен қысқартамыз.
    trimmed = text if len(text) <= 6000 else text[:6000] + "\n...[truncated]"

    prompt = f"{instruction}\n\n---\n{trimmed}\n---"
    text_out, error = _safe_generate(prompt, temperature=0.3)
    return text_out if text_out else error


def explain_quiz_answer(question, options, correct_letter, user_letter, level=None):
    """
    Тест сұрағына "💡 Неге?" батырмасы басылғанда тереңірек AI
    түсіндірмесі жасау. НАЗАР: бұл ЕШҚАШАН дұрыс жауапты өзгертпейді
    — correct_letter әрдайым базадан (Python) келеді, Gemini тек
    түсіндіреді.
    """
    options_text = "\n".join(
        f"{letter}) {text}" for letter, text in options.items()
    )
    prompt = (
        "ТІЛ ЕРЕЖЕСІ: Жауапты ТЕК қазақ тілінде жаз. Орыс тілін "
        "қолданба, тіпті бір сөз де болса орысша жазба.\n\n"
        f"Question:\n{question}\n\n{options_text}\n\n"
        f"Correct answer: {correct_letter}\n"
        f"Student answer: {user_letter}\n"
        f"Student level: {level or 'unknown'}\n\n"
        "Explain briefly and clearly why the correct answer is "
        "right. Do not change or question the given correct answer "
        "— just explain it.\n\n"
        "ЕСКЕРТУ: жауабыңды міндетті түрде қазақ тілінде жаз, орыс "
        "тілінде бір де сөйлем болмасын."
    )
    text, error = _safe_generate(prompt, temperature=0.2)
    return text if text else error


_QUESTION_GEN_SCHEMA_HINT = (
    "Return ONLY a JSON array (no markdown, no extra text). Use plain "
    "text inside strings: no asterisks, hashtags, backticks or "
    "underscores. Each "
    "item must have exactly these keys: \"question\" (string), "
    "\"A\", \"B\", \"C\", \"D\" (strings, the four options), "
    "\"correct\" (one of \"A\"/\"B\"/\"C\"/\"D\"), and "
    "\"explanation\" (a short 1-3 sentence explanation in Kazakh)."
)


def generate_questions(topic: str, level: str, count: int):
    """
    AI арқылы сұрақтар генерациялау (админ панелі). Әр сұрақты
    міндетті түрде валидациялайды — question/A/B/C/D/correct
    өрістерінің бәрі бар, әрі "correct" A-D ішінде болуы керек;
    сай келмеген сұрақтар тасталады (сақталмайды).

    Қайтарады: (valid_questions: list[dict], error: str|None)
    valid_questions-тегі әр элемент:
        {"question":, "options": {A,B,C,D}, "correct_answer":,
         "explanation":}
    """
    prompt = (
        f"Generate {count} multiple-choice English test questions.\n"
        f"Topic: {topic}\nLevel (CEFR-like): {level}\n\n"
        f"{_QUESTION_GEN_SCHEMA_HINT}"
    )

    text, error = _safe_generate(
        prompt, temperature=0.7, response_mime_type="application/json"
    )
    if not text:
        return [], error

    try:
        raw_items = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("AI generate_questions: JSON parse сәтсіз: %r", text[:300])
        return [], (
            "⚠️ AI жауабын оқу мүмкін болмады. Қайта көріңіз."
        )

    if not isinstance(raw_items, list):
        return [], "⚠️ AI жауабының форматы дұрыс емес."

    valid = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        required = ("question", "A", "B", "C", "D", "correct")
        if not all(k in item and str(item[k]).strip() for k in required):
            continue
        correct = str(item["correct"]).strip().upper()
        if correct not in ("A", "B", "C", "D"):
            continue

        candidate = {
            "question": sanitize_ai_text(item["question"]),
            "options": {
                "A": sanitize_ai_text(item["A"]),
                "B": sanitize_ai_text(item["B"]),
                "C": sanitize_ai_text(item["C"]),
                "D": sanitize_ai_text(item["D"]),
            },
            "correct_answer": correct,
            "explanation": sanitize_ai_text(item.get("explanation", "")) or None,
        }
        ok, reason = validate_generated_question(candidate)
        if not ok:
            logger.info("AI generate_questions: сұрақ тасталды (%s)", reason)
            continue
        valid.append(candidate)

    if not valid:
        return [], (
            "⚠️ AI жарамды сұрақ жасай алмады. Тақырыпты нақтылап "
            "қайта көріңіз."
        )

    return valid, None

# =========================================================
# 🧹 sanitize_ai_text — Markdown таңбаларын тазалау
# =========================================================
#
# Бот пайдаланушыға (Telegram) ешқашан Markdown таңбаларын
# көрсетпеуі керек. AI-дан келген БАРЛЫҚ мәтін осы функциядан өтеді:
#   - қарапайым жауаптар _safe_generate ішінде автоматты тазаланады;
#   - JSON-дағы әр өріс (сұрақ, нұсқа, түсіндірме) бөлек тазаланады.

_ABCD = ("A", "B", "C", "D")

_MD_ESCAPE_RE = re.compile(r"\\([*_#`~\[\]])")
_MD_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\n]*)\)")
_MD_CHECKBOX_RE = re.compile(r"\[[ xX]?\]")
_MD_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]*")
_MD_RULE_RE = re.compile(r"(?m)^[ \t]*(?:[-*_=~][ \t]*){3,}$")
_MD_QUOTE_RE = re.compile(r"(?m)^[ \t]*>+[ \t]?")
_MD_BULLET_RE = re.compile(r"(?m)^([ \t]*)[*+\-•][ \t]+")
_BLANK_RUN_RE = re.compile(r"_{3,}")
_MD_EDGE_UNDERSCORE_RE = re.compile(r"(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])")


def sanitize_ai_text(text) -> str:
    """
    AI мәтінінен Markdown форматтау таңбаларын қауіпсіз түрде алып
    тастап, таза қарапайым мәтін қайтарады. Ешқашан exception лақтырмайды.

    Не істейді:
      - **bold**, *italic*, __bold__, _italic_, ~~strike~~  -> тек мәтін
      - ### Heading  -> Heading
      - `code` және ``` блоктары  -> тек мәтін
      - [мәтін](сілтеме)  -> мәтін
      - "* пункт" / "- пункт" тізімдері  -> "• пункт"
      - "---" сызықтары, ">" дәйексөз белгісі  -> алынады
      - ___ (3+ астыңғы сызық, бос орын белгісі)  -> .....
    Ағылшын сөздері, апострофтар, дефис, жақша, тыныс белгілері
    (?, !, ., :, ;, ,) және "he / she" сияқты жарты сызықтар сақталады.
    """
    try:
        if text is None:
            return ""
        s = str(text).replace("\r\n", "\n").replace("\r", "\n")

        # Бос орын белгісі "____" — Markdown емес, бірақ астыңғы сызық
        # болып шықпауы үшін көрінетін "....." белгісіне ауыстырамыз.
        s = _BLANK_RUN_RE.sub(".....", s)

        s = _MD_ESCAPE_RE.sub(r"\1", s)
        s = _MD_LINK_RE.sub(r"\1", s)
        s = s.replace("```", "")
        s = _MD_HEADING_RE.sub("", s)
        s = _MD_RULE_RE.sub("", s)
        s = _MD_QUOTE_RE.sub("", s)
        s = _MD_BULLET_RE.sub(r"\1• ", s)
        s = _MD_CHECKBOX_RE.sub("", s)

        # Қалған форматтау таңбалары
        s = re.sub(r"[*`~#]", "", s)
        s = _MD_EDGE_UNDERSCORE_RE.sub("", s)
        s = s.replace("_", " ")

        s = re.sub(r"[ \t]+\n", "\n", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()
    except Exception:
        logger.exception("sanitize_ai_text қатесі")
        return re.sub(r"[*`~#_]", "", str(text or "")).strip()


# =========================================================
# ✅ validate_generated_question — сұрақты тексеру
# =========================================================

def _loose(text) -> str:
    """Салыстыру үшін: кіші әріп, тыныс белгісі мен бос орынсыз."""
    return re.sub(r"[\W_]+", "", str(text or "").casefold())


def _exact_norm(text) -> str:
    """Тырнақша/бос орын бірізді, бірақ тыныс белгісі сақталады."""
    s = (
        str(text or "")
        .replace("\u2019", "'").replace("\u2018", "'")
        .replace("\u201c", '"').replace("\u201d", '"')
    )
    return re.sub(r"\s+", " ", s).strip().casefold()


def validate_generated_question(q, authoritative_answer=None, check_leak=False):
    """
    AI жасаған (немесе конвертацияланған) бір сұрақты тексеру.
    Формат: {"question": str, "options": {"A","B","C","D"},
             "correct_answer": "A".."D", "explanation": str|None}

    Тексереді:
      - question бос емес;
      - A, B, C, D бар және бос емес;
      - correct_answer тек A/B/C/D;
      - төрт жауап бір-бірінен өзгеше (регистр/тыныс белгісі ғана
        өзгеше нұсқалар да бірдей саналады);
      - authoritative_answer берілсе: ол нұсқалардың дәл біреуімен
        сәйкес келеді және correct_answer дәл сол нұсқаны көрсетеді;
      - check_leak=True болса: сұрақ мәтіні дайын дұрыс жауапты
        (3+ сөзден тұрса) ашып көрсетпейді.

    Қайтарады: (True, None) немесе (False, "себеп").
    Сәтсіз сұрақ database-ке ЕШҚАШАН сақталмауы керек.
    """
    if not isinstance(q, dict):
        return False, "сұрақ объект емес"

    question = str(q.get("question") or "").strip()
    if not question:
        return False, "question бос"

    options = q.get("options")
    if not isinstance(options, dict):
        return False, "options жоқ"

    for letter in _ABCD:
        if not str(options.get(letter) or "").strip():
            return False, f"{letter} нұсқасы жоқ немесе бос"

    correct = str(q.get("correct_answer") or "").strip().upper()
    if correct not in _ABCD:
        return False, "correct_answer A/B/C/D емес"

    loose = {letter: _loose(options[letter]) for letter in _ABCD}
    if any(not value for value in loose.values()):
        return False, "нұсқада мәтін жоқ"
    if len(set(loose.values())) < 4:
        return False, "төрт нұсқа бір-бірінен өзгеше емес"

    if authoritative_answer:
        target = _loose(authoritative_answer)
        matches = [l for l in _ABCD if loose[l] == target]
        if len(matches) != 1:
            return False, "дұрыс жауап нұсқалардың дәл біреуіне сәйкес емес"
        if matches[0] != correct:
            return False, "correct_answer басқа нұсқаны көрсетіп тұр"
        if _exact_norm(options[correct]) != _exact_norm(authoritative_answer):
            return False, "дұрыс нұсқа бастапқы жауаппен дәл сәйкес емес"

    if check_leak:
        correct_text = str(options[correct])
        if len(correct_text.split()) >= 3 and loose[correct] in _loose(question):
            return False, "сұрақ мәтіні дұрыс жауапты ашып көрсетіп тұр"

    return True, None


# =========================================================
# 🔄 convert_exercise_to_quiz — дайын тапсырма -> Quiz
# =========================================================
#
# Бұл AI GENERATOR (generate_questions) емес: генератор тақырып
# бойынша ЖАҢА сұрақ ойлап табады, ал converter мұғалім жіберген
# дайын тапсырманы (және Answers бөлімін) Quiz форматына айналдырады.

CONVERT_MAX_ATTEMPTS = 2          # 1 негізгі әрекет + 1 retry
CONVERT_MAX_CHARS = 30000
CONVERT_MAX_ITEMS = 60
CONVERT_MAX_OUTPUT_TOKENS = 24000

CONVERTER_SYSTEM_PROMPT = (
    "You are an English educational quiz converter.\n"
    "Convert the supplied English exercise into multiple-choice quiz "
    "questions.\n"
    "Use the provided answers as the authoritative correct answers "
    "whenever available.\n"
    "Generate exactly four options A, B, C, D.\n"
    "Exactly one option must be correct.\n"
    "The correct option must exactly match the authoritative answer.\n"
    "Generate three plausible but grammatically incorrect distractors.\n"
    "Return valid JSON only.\n"
    "Do not return Markdown.\n"
    "Do not return explanations outside JSON.\n"
    "Do not use asterisks, hashtags, backticks, underscores or Markdown "
    "formatting.\n"
    "Preserve the educational meaning of the source material.\n"
    "Do not invent information unrelated to the source.\n"
    "\n"
    "ADDITIONAL RULES:\n"
    "1. Create exactly one question for every numbered task, in the "
    "original order. Never skip or merge tasks. Titles and instruction "
    "lines are context, not questions. If the file has several exercises "
    "and the numbering restarts, convert all of them, in order.\n"
    "2. Task instructions such as \"Make the present perfect continuous "
    "yes / no questions\" or \"Make the present perfect continuous wh- "
    "questions\" give word cues separated by slashes, for example "
    "\"she / run much recently?\" or \"How long / you / study Japanese?\". "
    "Rebuild the full sentence the instruction asks for. Example: the cue "
    "\"she / run much recently?\" gives \"Has she been running much "
    "recently?\" and the cue \"How long / you / study Japanese?\" gives "
    "\"How long have you been studying Japanese?\".\n"
    "3. The question text must name the task type and show the original "
    "cue, but it must NEVER show the finished correct sentence. Example "
    "question text: Choose the correct Present Perfect Continuous "
    "yes / no question: she / run much recently?\n"
    "4. Every distractor must be a complete answer of the same kind as "
    "the correct answer, built on a typical learner mistake of exactly "
    "this task (wrong auxiliary verb, missing or wrong -ing form, wrong "
    "word order, wrong tense, missing auxiliary). Distractors must be "
    "grammatically wrong for this task, must be different from the "
    "correct answer and from each other (not only capitalization or "
    "punctuation), and must never be another correct answer.\n"
    "5. If a task already has its own answer options, keep those "
    "original options and do not invent new ones. If a task is a "
    "complete-the-sentence or fill-in-the-blank task, keep the sentence "
    "with the gap written as five dots ..... and use the possible "
    "words or phrases as options. Keep the meaning and format of the "
    "original task.\n"
    "6. Every question text must be different from all other question "
    "texts, so always include the cue or the sentence of the task in the "
    "question text.\n"
    "7. When an Answers section is given, answer number k belongs to task "
    "number k in the original order. Copy that authoritative answer into "
    "the correct option character for character, including capital "
    "letters and final punctuation. Never contradict the Answers "
    "section.\n"
    "8. When there is no Answers section, work out the single correct "
    "answer yourself from the content of the task, carefully.\n"
    "9. The explanation is one or two short sentences in Kazakh that "
    "explain the grammar rule behind the correct answer. English words "
    "and examples inside the explanation stay in English. Never write "
    "Russian.\n"
    "10. Use plain text inside every JSON string. No line breaks inside "
    "strings.\n"
    "11. Output exactly this JSON structure and nothing else: "
    "{\"questions\": [{\"question\": \"...\", \"options\": {\"A\": \"...\", "
    "\"B\": \"...\", \"C\": \"...\", \"D\": \"...\"}, "
    "\"correct_answer\": \"B\", \"explanation\": \"...\"}]}"
)

_CONVERT_FAIL_HINT = (
    "\n\nОсы себептен файл Quiz-қа айналмады. Файлда тапсырмалар "
    "нөмірленген (1., 2., 3.) және жауаптары Answers бөлімінде болуы "
    "керек. Тексеріп, қайта жіберіңіз."
)


def _build_converter_prompt(exercise_text, expected_count,
                            previous_raw=None, problems=None):
    parts = []

    if previous_raw is not None:
        parts.append(
            "Your previous reply could not be used. Fix every problem "
            "listed below and return the COMPLETE corrected JSON again "
            "(all questions, not only the fixed ones). Return valid JSON "
            "only, with no Markdown."
        )
        parts.append("Problems found:")
        for problem in (problems or [])[:12]:
            parts.append("- " + problem)
        parts.append("Your previous reply was:")
        parts.append("<<<")
        parts.append(previous_raw[:8000])
        parts.append(">>>")
        parts.append("")

    parts.append(
        "Convert the following exercise file into quiz questions and "
        "return the JSON described in your instructions."
    )
    if expected_count:
        parts.append(
            f"The Answers section contains {expected_count} answers, so "
            f"return exactly {expected_count} questions, one for each "
            "task, in the original order."
        )
    else:
        parts.append(
            "The file has no Answers section. Determine the correct "
            "answers yourself."
        )
    parts.append("")
    parts.append("EXERCISE FILE TEXT:")
    parts.append("<<<")
    parts.append(exercise_text)
    parts.append(">>>")
    return "\n".join(parts)


def _extract_json_payload(raw):
    """AI жауабынан JSON алу (``` қоршауы немесе артық мәтін болса да)."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[A-Za-z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)

    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        pass

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = s.find(open_ch), s.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError("reply is not valid JSON")


_OPT_KEY_RE = re.compile(r"^\s*\(?([A-Da-d])\)?[.:]?\s*$")
_OPT_PREFIX_RE = re.compile(r"^\(?[A-Da-d]\)\s+")


def _clean_option_text(value):
    return _OPT_PREFIX_RE.sub("", sanitize_ai_text(value)).strip()


def _normalize_correct_letter(value, options):
    """'B', 'b', 'B)', 'B) text' немесе толық жауап мәтіні -> 'B' / None."""
    s = str(value or "").strip()
    if not s:
        return None

    match = re.match(r"^\(?([A-Da-d])\)?[.:]?$", s) or re.match(
        r"^([A-Da-d])\)\s", s
    )
    if match:
        return match.group(1).upper()

    target = _loose(s)
    if target:
        found = [l for l in _ABCD if _loose(options.get(l)) == target]
        if len(found) == 1:
            return found[0]
    return None


def _normalize_ai_question(item):
    """AI JSON элементін ішкі форматқа келтіру. -> (dict|None, себеп|None)."""
    if not isinstance(item, dict):
        return None, "item is not a JSON object"

    options = {}
    raw_options = item.get("options")
    if isinstance(raw_options, dict):
        for key, value in raw_options.items():
            match = _OPT_KEY_RE.match(str(key))
            if match:
                options[match.group(1).upper()] = _clean_option_text(value)
    elif isinstance(raw_options, list) and len(raw_options) == 4:
        for letter, value in zip(_ABCD, raw_options):
            options[letter] = _clean_option_text(value)
    else:
        for letter in _ABCD:
            if letter in item:
                options[letter] = _clean_option_text(item[letter])

    correct = _normalize_correct_letter(
        item.get("correct_answer", item.get("correct")), options
    )

    return {
        "question": sanitize_ai_text(item.get("question", "")),
        "options": options,
        "correct_answer": correct,
        "explanation": sanitize_ai_text(item.get("explanation", "")) or None,
    }, None


def _align_with_authoritative(q, target, auth_by_loose):
    """
    AI дұрыс нұсқаны бастапқы (authoritative) жауаптан сәл өзгертіп
    жіберсе (нүкте/регистр түсіп қалса), оны дәл түпнұсқамен ауыстырады
    немесе correct_answer әрпін түзетеді.

    target        — осы сұраққа тиесілі жауап (реті бойынша сәйкестендіру
                    мүмкін болса), әйтпесе None;
    auth_by_loose — барлық жауаптардың {loose_norm: дәл мәтін} сөздігі.
    """
    options = q["options"]
    if not all(options.get(l) for l in _ABCD):
        return q

    loose = {l: _loose(options[l]) for l in _ABCD}
    chosen = q.get("correct_answer")

    if target:
        wanted = _loose(target)
        matches = [l for l in _ABCD if loose[l] == wanted]
        if len(matches) == 1:
            q["correct_answer"] = matches[0]
            options[matches[0]] = target
        elif not matches and chosen in _ABCD:
            ratio = difflib.SequenceMatcher(
                None, loose[chosen], wanted
            ).ratio()
            if ratio >= 0.85:
                options[chosen] = target
        return q

    matches = [l for l in _ABCD if loose[l] in auth_by_loose]
    if len(matches) == 1:
        q["correct_answer"] = matches[0]
        options[matches[0]] = auth_by_loose[loose[matches[0]]]
    elif len(matches) == 0 and chosen in _ABCD and auth_by_loose:
        best_key = max(
            auth_by_loose,
            key=lambda k: difflib.SequenceMatcher(
                None, loose[chosen], k
            ).ratio(),
        )
        if difflib.SequenceMatcher(None, loose[chosen], best_key).ratio() >= 0.85:
            options[chosen] = auth_by_loose[best_key]
    return q


_REASON_EN = {
    "сұрақ объект емес": "the item is not a JSON object",
    "question бос": "the question text is empty",
    "options жоқ": "the options are missing",
    "correct_answer A/B/C/D емес": "correct_answer must be exactly A, B, C or D",
    "нұсқада мәтін жоқ": "an option has no text",
    "төрт нұсқа бір-бірінен өзгеше емес": (
        "the four options are not different from each other"
    ),
    "дұрыс жауап нұсқалардың дәл біреуіне сәйкес емес": (
        "the authoritative answer is not one of the four options"
    ),
    "correct_answer басқа нұсқаны көрсетіп тұр": (
        "correct_answer points to a wrong option; the authoritative "
        "answer is in another option"
    ),
    "дұрыс нұсқа бастапқы жауаппен дәл сәйкес емес": (
        "the correct option must match the authoritative answer "
        "character for character"
    ),
    "сұрақ мәтіні дұрыс жауапты ашып көрсетіп тұр": (
        "the question text shows the finished correct answer; show only "
        "the task cue"
    ),
}


def _reason_en(reason):
    """Валидатордың қазақша себебін AI-ға түсінікті ағылшын мәтініне айналдыру."""
    match = re.match(r"^([A-D]) нұсқасы жоқ немесе бос$", reason or "")
    if match:
        return f"option {match.group(1)} is missing or empty"
    return _REASON_EN.get(reason, reason)


def _process_converter_output(raw, answers):
    """
    AI жауабын талдап, әр сұрақты нормализациялау, бастапқы жауаппен
    салыстыру және валидациялау.

    Қайтарады: {"valid": [...], "problems": [...], "raw_count": int,
                "verified": int}
    """
    result = {"valid": [], "problems": [], "raw_count": 0, "verified": 0}

    try:
        payload = _extract_json_payload(raw)
        items = payload.get("questions") if isinstance(payload, dict) else payload
        if not isinstance(items, list) or not items:
            raise ValueError('JSON has no non-empty "questions" list')
    except (ValueError, AttributeError) as exc:
        result["problems"].append(f"The reply was not usable JSON: {exc}")
        return result

    result["raw_count"] = len(items)

    use_index = bool(answers) and len(items) == len(answers)
    if answers and not use_index:
        result["problems"].append(
            f"The Answers section has {len(answers)} answers but you "
            f"returned {len(items)} questions. Return exactly "
            f"{len(answers)} questions, one per task, in the original order."
        )
    auth_by_loose = {_loose(a): a for a in answers}

    seen_stems = {}
    for idx, item in enumerate(items):
        label = f"Question {idx + 1}"
        q, reason = _normalize_ai_question(item)
        if q is None:
            result["problems"].append(f"{label}: {reason}")
            continue

        target = answers[idx] if use_index else None
        if answers:
            q = _align_with_authoritative(q, target, auth_by_loose)

        ok, reason = validate_generated_question(
            q, authoritative_answer=target, check_leak=True
        )
        if ok and answers and not use_index:
            correct_loose = _loose(q["options"][q["correct_answer"]])
            if correct_loose not in auth_by_loose:
                ok, reason = False, (
                    "correct option does not match any answer of the "
                    "Answers section"
                )
        if not ok:
            result["problems"].append(f"{label}: {_reason_en(reason)}")
            continue

        stem = _loose(q["question"])
        if stem in seen_stems:
            result["problems"].append(
                f"{label}: question text is identical to question "
                f"{seen_stems[stem]}. Include the task cue so every "
                "question text is unique."
            )
            continue
        seen_stems[stem] = idx + 1

        result["valid"].append(q)
        if answers:
            result["verified"] += 1

    return result


def convert_exercise_to_quiz(exercise_text, authoritative_answers=None):
    """
    Мұғалім жіберген дайын тапсырманы (A/B/C/D нұсқасыз) толық Quiz
    сұрақтарына айналдыру:
      тапсырмаларды анықтау -> жауаптарды анықтау -> A/B/C/D жасау ->
      дұрыс жауапты бекіту -> explanation жасау -> JSON валидация.

    exercise_text          — файлдың толық мәтіні ("Answers:" бөлімімен).
    authoritative_answers  — Answers бөліміндегі жауаптар (реті бойынша).
                             Берілсе, дұрыс нұсқа сол жауаппен дәл
                             сәйкес болуы МІНДЕТТІ; берілмесе, дұрыс
                             жауапты AI өзі анықтайды.

    JSON дұрыс емес болса немесе сұрақтар валидациядан өтпесе, AI-ға
    қате тізімі мен алдыңғы жауабы беріліп, автоматты retry жасалады.

    Қайтарады: (questions, report, error)
      questions — [{"question","options":{A,B,C,D},"correct_answer",
                    "explanation"}, ...] (тек валидтілері)
      report    — {"found","converted","abcd_generated","verified",
                   "invalid","key_available","attempts"}
      error     — сәтсіз болса мұғалімге түсінікті мәтін, әйтпесе None
    """
    if not is_configured():
        return [], {}, FRIENDLY_NOT_CONFIGURED

    text = (exercise_text or "").strip()
    if len(text) < 20:
        return [], {}, "Файлдан жеткілікті мәтін табылмады."
    if len(text) > CONVERT_MAX_CHARS:
        return [], {}, (
            "Файл тым үлкен. Оны бірнеше кіші файлға бөліп жіберіңіз."
        )

    answers = [a.strip() for a in (authoritative_answers or []) if a and a.strip()]
    if len(answers) > CONVERT_MAX_ITEMS:
        return [], {}, (
            f"Бір файлда {CONVERT_MAX_ITEMS} тапсырмадан артық болмауы "
            "керек. Файлды бөліп жіберіңіз."
        )

    best = None
    api_error = None
    previous_raw = None
    previous_problems = None
    attempts = 0

    for attempt in range(1, CONVERT_MAX_ATTEMPTS + 1):
        attempts = attempt
        prompt = _build_converter_prompt(
            text, len(answers), previous_raw, previous_problems
        )
        raw, err = _safe_generate(
            prompt,
            system_instruction=CONVERTER_SYSTEM_PROMPT,
            temperature=0.3 if attempt == 1 else 0.1,
            response_mime_type="application/json",
            max_output_tokens=CONVERT_MAX_OUTPUT_TOKENS,
        )
        if not raw:
            api_error = err
            break

        result = _process_converter_output(raw, answers)
        if best is None or len(result["valid"]) > len(best["valid"]):
            best = result
        if not result["problems"]:
            break

        logger.warning(
            "AI converter, әрекет %s: %s мәселе табылды: %s",
            attempt, len(result["problems"]), result["problems"][:3],
        )
        previous_raw = raw
        previous_problems = result["problems"]

    if best is None:
        return [], {}, api_error or FRIENDLY_UNAVAILABLE

    if not best["valid"]:
        reasons = "\n".join(
            f"- {sanitize_ai_text(p)}" for p in best["problems"][:3]
        )
        return [], {}, (
            "AI файлды Quiz форматына айналдыра алмады "
            f"({attempts} әрекет жасалды)."
            + (f"\n\nТабылған мәселелер:\n{reasons}" if reasons else "")
            + _CONVERT_FAIL_HINT
        )

    converted = len(best["valid"])
    found = len(answers) if answers else best["raw_count"]
    report = {
        "found": found,
        "converted": converted,
        "abcd_generated": converted,
        "verified": best["verified"],
        "invalid": max(found - converted, 0),
        "key_available": bool(answers),
        "attempts": attempts,
    }
    return best["valid"], report, None