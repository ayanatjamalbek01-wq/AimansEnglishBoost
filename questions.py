"""
questions.py
Сұрақтарды файлдан (.docx / .txt / .xlsx / .pdf / .csv / .rtf / .odt)
оқу, жауап кілтімен сәйкестендіру және тақырып бойынша грамматикалық
түсіндірмелерді сақтау модулі.

===========================================================
ТАҚЫРЫП ПЕН ДЕҢГЕЙ:

    Файл атауынан АНЫҚТАЛМАЙДЫ. Файл атауы кез келген болуы мүмкін
    (мыс. questions.docx). Тақырып пен деңгейді әкімшіден Telegram
    арқылы диалог түрінде main.py сұрайды (тақырып — еркін мәтін,
    деңгей — LEVELS тізімінен батырмамен таңдау).
===========================================================
СҰРАҚТАР ФАЙЛЫНЫҢ ІШКІ ФОРМАТЫ (БӘРІ БІРДЕЙ):

    1. Сұрақ мәтіні
       A) Жауап 1
       B) Жауап 2
       C) Жауап 3
       D) Жауап 4

    2. Келесі сұрақ...

===========================================================
ЖАУАП КІЛТІ ФАЙЛЫ (әрдайым .txt, сұрақтар файлынан кейін жіберіледі):

    1 — B
    2 — C
    3 — A
===========================================================
"""

import csv
import random
import re
from io import BytesIO, StringIO

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader
from striprtf.striprtf import rtf_to_text
from odf.opendocument import load as load_odt
from odf import text as odf_text
from odf import teletype


SUPPORTED_EXTENSIONS = ("docx", "txt", "xlsx", "pdf", "csv", "rtf", "odt")

# Мәтіні жоқ, сондықтан сұрақ ала алмайтын, бірақ жиі шатастырылатын
# кеңейтімдер — түсінікті қате хабарлама үшін бөлек тізім.
UNSUPPORTED_WITH_HINT = {
    "doc": (
        "Ескі Word форматы (.doc, Word 97-2003) қолдау көрсетілмейді. "
        "Файлды Word-та ашып, 'Файл → Сақтау' арқылы .docx форматында "
        "қайта сақтап жіберіңіз."
    ),
}

LEVELS = [
    "Elementary",
    "Pre-Intermediate",
    "Intermediate",
    "Upper-Intermediate",
    "Advanced",
]

# =========================================================
# СҰРАҚ ТҮРІН АНЫҚТАУ ЖӘНЕ ӘРТҮРЛІ FEEDBACK ЖҮЙЕСІ
# =========================================================
#
# Feedback енді бір ғана дайын мәтін емес — сұрақтың тақырыбына қарай
# қай грамматикалық/лексикалық санатқа жататыны анықталады да
# ("clue"), содан кейін сол санатқа сай кездейсоқ үлгі (template)
# таңдалады. Ешбір нақты грамматикалық ереже немесе мәтін ойдан
# шығарылмайды — тек файлдан келген нақты дұрыс жауап мәтіні
# қолданылады.

# Топ атауы осы кілт сөздердің қайсысын қамтитынына қарай анықталады
# (кіші-үлкен әріпке тәуелсіз, ішінара сәйкестік бойынша).
_TYPE_KEYWORDS = [
    ("tense", ["tense", "шақ", "simple", "continuous", "perfect", "future"]),
    ("modal", ["modal"]),
    ("article", ["article"]),
    ("preposition", ["preposition"]),
    ("conditional", ["conditional"]),
    ("synonym_antonym", ["synonym", "antonym"]),
    ("vocabulary", ["vocabulary", "vocab"]),
    ("reading", ["reading"]),
    ("meaning", ["meaning"]),
]

# Бұл тақырыптар нақты грамматикалық категория емес — "таңдалған
# грамматикалық тақырыпқа негізделген" деген сияқты сөйлем ЕШҚАШАН
# осыларда қолданылмауы керек.
_GENERIC_TOPICS = {"tests", "test", "mixed grammar", "mixed"}


def detect_question_type(topic: str) -> str:
    """Тақырып атауы бойынша сұрақ түрін (санатын) анықтау."""
    t = (topic or "").strip().lower()

    if t in _GENERIC_TOPICS:
        return "general"

    for qtype, keywords in _TYPE_KEYWORDS:
        for kw in keywords:
            if kw in t:
                return qtype

    return "general"


# Дұрыс жауапқа арналған өте қысқа, әртүрлі реакциялар (түсіндірмесіз).
_CORRECT_TEMPLATES = [
    "Дұрыс ✅",
    "Иә, осы.",
    "Дәл өзі.",
    "Correct! 👌",
    "Мынауы дұрыс.",
    "Иә, жақсы.",
]

# Уақыт көрсеткіштері (time markers) — сөйлемде табылса, дәл сол сөз
# аталып, соған сәйкес шақ түсіндіріледі. Ретке маңызы бар: неғұрлым
# нақты тіркестер алдымен тексеріледі.
_TENSE_MARKERS = [
    ("last night", "Past Simple"),
    ("last week", "Past Simple"),
    ("last month", "Past Simple"),
    ("last year", "Past Simple"),
    ("at the moment", "Present Continuous"),
    ("right now", "Present Continuous"),
    ("every day", "Present Simple"),
    ("next week", "Future Simple"),
    ("next year", "Future Simple"),
    ("yesterday", "Past Simple"),
    ("tomorrow", "Future Simple"),
    ("already", "Present Perfect"),
    ("since", "Present Perfect"),
    ("just", "Present Perfect"),
    ("never", "Present Perfect"),
    ("ever", "Present Perfect"),
    ("yet", "Present Perfect"),
    ("usually", "Present Simple"),
    ("often", "Present Simple"),
    ("sometimes", "Present Simple"),
    ("always", "Present Simple"),
    ("now", "Present Continuous"),
    ("ago", "Past Simple"),
]

_TENSE_MARKER_MEANING = {
    "Present Perfect": "әрекет бұрын басталып, қазірге дейін жалғасып тұр немесе жақында аяқталды",
    "Past Simple": "бұл өткен, аяқталған уақытты білдіреді",
    "Present Continuous": "бұл дәл қазір болып жатқан әрекетті білдіреді",
    "Present Simple": "бұл әдеттегі, қайталанатын әрекетті білдіреді",
    "Future Simple": "бұл болашақ әрекетті білдіреді",
}

# Әр шақ туралы толық, оқытушыдай түсіндірме үшін деректер.
# Барлығы стандартты, жалпыға белгілі грамматикалық фактілер —
# ешбір ереже немесе мысал ойдан шығарылмаған.
_TENSE_INFO = {
    "Present Perfect": {
        "use": "әрекеттің бұрын басталып, қазірге дейін жалғасуын немесе жақында аяқталғанын білдіреді",
        "structure": "have/has + V3 (өткен шақ есімше)",
        "rule": "Іс-әрекеттің нақты уақыты емес, нәтижесі немесе жалғасуы маңызды болғанда қолданылады.",
        "memory": "HAVE/HAS + V3 — нәтиже немесе жалғасқан тәжірибе.",
        "example": "She has lived here for five years.",
    },
    "Past Simple": {
        "use": "өткенде басталып, өткенде аяқталған, нақты уақыты белгілі әрекетті білдіреді",
        "structure": "V2 (өткен шақ) немесе did + V1",
        "rule": "Әрекет толығымен аяқталған, қазіргі уақытпен байланысы жоқ.",
        "memory": "yesterday, ago, last week → Past Simple.",
        "example": "I visited my grandmother yesterday.",
    },
    "Present Simple": {
        "use": "әдеттегі, күнделікті қайталанатын іс-әрекетті немесе жалпы фактіні білдіреді",
        "structure": "V1 (he/she/it үшін V1 + s/es)",
        "rule": "He/She/It үшін етістікке -s/-es жалғанады.",
        "memory": "HE/SHE/IT → VERB + S/ES.",
        "example": "She plays tennis every weekend.",
    },
    "Present Continuous": {
        "use": "дәл қазір болып жатқан немесе уақытша жағдайды білдіреді",
        "structure": "am/is/are + V-ing",
        "rule": "Сөйлеу сәтінде болып жатқан әрекетке қолданылады.",
        "memory": "now, right now, at the moment → Continuous.",
        "example": "She is studying right now.",
    },
    "Past Continuous": {
        "use": "өткенде белгілі бір сәтте жалғасып жатқан әрекетті білдіреді",
        "structure": "was/were + V-ing",
        "rule": "Көбіне басқа бір өткен әрекетпен қатар қолданылады.",
        "memory": "was/were + V-ing — өткендегі 'фон' әрекет.",
        "example": "I was reading when he called.",
    },
    "Past Perfect": {
        "use": "өткендегі екі әрекеттің қайсысы бұрын болғанын көрсетеді",
        "structure": "had + V3",
        "rule": "Екі өткен әрекеттің ең бұрынғысында қолданылады.",
        "memory": "had + V3 — 'өткеннің өткені'.",
        "example": "She had left before I arrived.",
    },
    "Future Simple": {
        "use": "келер шақтағы жоспарланбаған әрекетті немесе болжамды білдіреді",
        "structure": "will + V1",
        "rule": "Сол сәтте қабылданған шешім немесе болжам үшін қолданылады.",
        "memory": "WILL + V1 — келер шақ.",
        "example": "I will call you tomorrow.",
    },
}

# Модальды етістіктердің мағынасы мен мысалы (стандартты грамматика).
_MODAL_INFO = [
    ("mustn't", "тыйымды", "You mustn't smoke here."),
    ("must", "күшті міндеттілікті", "You must wear a seatbelt."),
    ("have to", "сырттан келген міндеттілікті", "I have to finish this today."),
    ("need to", "қажеттілікті", "You need to study more."),
    ("should", "кеңесті", "You should see a doctor."),
    ("ought to", "кеңесті", "You ought to apologize."),
    ("could", "өткен мүмкіндікті немесе сыпайы сұранысты", "Could you help me, please?"),
    ("can", "мүмкіндікті немесе рұқсатты", "She can swim very well."),
    ("might", "аз ықтималдықты", "It might rain later."),
    ("may", "ықтималдықты немесе рұқсатты", "May I come in?"),
    ("would", "сыпайы сұранысты немесе гипотетикалық жағдайды", "Would you like some tea?"),
    ("will", "болашақ шешімді немесе уәдені", "I will help you tomorrow."),
]

_BLANK_RE = re.compile(r"_{2,}")


def _fill_blank(question, correct_text):
    """Сөйлемдегі бос орынды (___) дұрыс жауаппен толтырып, толық
    сөйлемді қайтару. Бос орын болмаса None қайтарады."""
    if not question or not _BLANK_RE.search(question):
        return None
    return _BLANK_RE.sub(correct_text, question, count=1)


def _find_tense_marker(question: str):
    """Сөйлемнен уақыт көрсеткішін табу (бар болса нақты сөзбен)."""
    text = question or ""
    lowered = text.lower()

    for marker, tense in _TENSE_MARKERS:
        match = re.search(r"\b" + re.escape(marker) + r"\b", lowered)
        if match:
            start, end = match.span()
            return text[start:end], tense

    return None, None


def _guess_tense_from_form(correct_text: str):
    """Ешбір clue табылмаса, дұрыс жауаптың өз құрылымынан шақты болжау."""
    t = (correct_text or "").strip().lower()

    if re.match(r"^(has|have)\s+\w+", t):
        return "Present Perfect"
    if re.match(r"^had\s+\w+", t):
        return "Past Perfect"
    if re.match(r"^(is|are|am)\s+\w+ing", t):
        return "Present Continuous"
    if re.match(r"^(was|were)\s+\w+ing", t):
        return "Past Continuous"
    if re.match(r"^will\s+\w+", t):
        return "Future Simple"
    if re.match(r"^(was|were)\b", t):
        return "Past Simple"

    return None


def _tense_feedback(question: str, correct_text: str, user_text: str = None) -> str:
    """
    Мұғалім сияқты толық түсіндірме: неге дәл осы шақ керек, шақтың
    ережесі, есте сақтау кеңесі және мысал. Тек стандартты, жалпыға
    белгілі грамматикалық фактілер қолданылады — ешбір ереже ойдан
    шығарылмайды.
    """
    marker, tense = _find_tense_marker(question)
    if not tense:
        tense = _guess_tense_from_form(correct_text)

    info = _TENSE_INFO.get(tense)
    filled = _fill_blank(question, correct_text)

    parts = ["📌 Неге?"]

    if marker and tense in _TENSE_MARKER_MEANING:
        parts.append(
            f"\"{marker}\" деген сөз бар — {_TENSE_MARKER_MEANING[tense]}, "
            f"сондықтан {tense} керек."
        )
    elif info:
        parts.append(
            f"\"{correct_text}\" құрылымы {tense} шағын көрсетеді, "
            "сөйлемнің мағынасы да осыған сай келеді."
        )
    else:
        parts.append("Сөйлемнің құрылымы мен мағынасына қарағанда, дұрыс жауап осы.")

    if filled:
        parts.append(f"\nТолық сөйлем: \"{filled}\"")

    if user_text:
        parts.append(
            f"\n❌ \"{user_text}\" бұл жерде сәйкес келмейді, себебі ол "
            "басқа уақыт мағынасын білдіреді."
        )

    if info:
        parts.append(f"\n📚 Grammar: {tense}")
        parts.append(f"{info['use'].capitalize()}.")
        parts.append(f"Құрылымы: {info['structure']}")
        parts.append(f"\n💡 Ереже:\n{info['rule']}")
        parts.append(f"\n🧠 Есте сақта:\n{info['memory']}")
        parts.append(f"\n✏️ Мысал:\n{info['example']}")

    return "\n".join(parts)


def _article_feedback(question: str, correct_text: str, user_text: str = None) -> str:
    """Артикльдер (a/an/the) бойынша толық түсіндірме."""
    t = (correct_text or "").strip().lower()

    next_word = None
    if question and "___" in question:
        after = question.split("___", 1)[1].strip()
        m = re.match(r"[\"'“]?([A-Za-z]+)", after)
        if m:
            next_word = m.group(1)

    if t == "an" and next_word:
        why = (
            f"\"{next_word}\" дауысты дыбыстан басталады, сондықтан "
            "\"a\" емес, \"an\" қолданылады."
        )
        rule = "\"an\" дауысты дыбыстан (a, e, i, o, u) басталатын сөздердің алдында қолданылады."
        example = "an apple, an hour, an umbrella"
    elif t == "a" and next_word:
        why = (
            f"\"{next_word}\" дауыссыз дыбыстан басталады, сондықтан "
            "\"an\" емес, \"a\" қолданылады."
        )
        rule = "\"a\" дауыссыз дыбыстан басталатын сөздердің алдында қолданылады."
        example = "a book, a university, a car"
    elif t == "the":
        why = (
            "Бұл жерде сөз нақты, белгілі бір нәрсені білдіріп тұр, "
            "сондықтан белгілі артикль \"the\" керек."
        )
        rule = "\"the\" сөйлеушіге де, тыңдаушыға да белгілі, нақты зат/адам туралы айтқанда қолданылады."
        example = "The book on the table is mine."
    else:
        why = (
            "Бұл жерде артикльсіз нұсқа дұрыс, себебі зат есім жалпы "
            "немесе саналмайтын мағынада қолданылған."
        )
        rule = "Көпше түрдегі жалпы зат есімдер немесе саналмайтын зат есімдер алдында артикль қойылмайды."
        example = "I like music. Dogs are friendly."

    filled = _fill_blank(question, correct_text)

    parts = ["📌 Неге?", why]
    if filled:
        parts.append(f"\nТолық сөйлем: \"{filled}\"")
    if user_text and user_text.strip().lower() != t:
        parts.append(f"\n❌ \"{user_text}\" мұнда дыбыстық/мағыналық ережеге сай келмейді.")
    parts.append("\n📚 Grammar: Articles (a/an/the)")
    parts.append(f"💡 Ереже:\n{rule}")
    parts.append(f"\n✏️ Мысал:\n{example}")

    return "\n".join(parts)


def _modal_feedback(question: str, correct_text: str, user_text: str = None) -> str:
    """Модальды етістіктер бойынша толық түсіндірме."""
    t = (correct_text or "").strip().lower()

    meaning = None
    for modal, mean, example in _MODAL_INFO:
        if modal in t:
            meaning = (modal, mean, example)
            break

    filled = _fill_blank(question, correct_text)
    parts = ["📌 Неге?"]

    if meaning:
        modal, mean, example = meaning
        parts.append(
            f"\"{modal}\" бұл сөйлемде {mean} білдіреді, сондықтан "
            "мағынаға сай келеді."
        )
    else:
        example = None
        parts.append(f"\"{correct_text}\" осы сөйлемнің мағынасына ең сай модальды етістік.")

    if filled:
        parts.append(f"\nТолық сөйлем: \"{filled}\"")
    if user_text:
        parts.append(f"\n❌ \"{user_text}\" бұл жерде мағынаға сай келмейді.")

    parts.append("\n📚 Grammar: Modal verbs")
    parts.append(
        "Модальды етістіктер міндеттілік, кеңес, мүмкіндік немесе "
        "рұқсат сияқты қосымша мағына береді."
    )
    if example:
        parts.append(f"\n✏️ Мысал:\n{example}")

    return "\n".join(parts)


def _generic_teacher_feedback(question: str, correct_text: str, user_text: str, qtype: str) -> str:
    """
    Арнайы грамматикалық анализатор жоқ санаттар үшін (vocabulary,
    preposition, reading, synonym/antonym, conditional, general).

    МАҢЫЗДЫ ШЕКТЕУ: жүйеде оқылым мәтіні (passage) бөлек сақталмайды —
    тек сұрақ пен жауап нұсқалары бар. Сондықтан бұл жерде "мәтіннен
    дәлел" деп жоқ дереккөзді ойдан шығармаймыз — нақты берілген
    сөйлемнің өзін дәлел ретінде көрсетеміз (бұл — шынайы дерек).
    """
    filled = _fill_blank(question, correct_text)

    parts = []

    if qtype == "reading":
        parts.append("📖 Сөйлемнен дәлел")
        parts.append(
            f"Дұрыс жауап — \"{correct_text}\" — берілген сөйлемдегі "
            "мағынаға сәйкес келеді."
        )
        if filled:
            parts.append(f"\nТолық сөйлем: \"{filled}\"")
        if user_text:
            parts.append(
                f"\n❌ \"{user_text}\" нұсқасы сөйлемде айтылған "
                "ақпаратқа сәйкес келмейді."
            )
        parts.append(
            "\n🧠 Test tip:\nЖауапты сөйлемдегі нақты сөздермен "
            "салыстырып, мағынасы дәл сол ақпаратқа сай келетінін "
            "тексеріңіз."
        )
    else:
        parts.append("📌 Неге?")
        if filled:
            parts.append(f"Толық дұрыс сөйлем: \"{filled}\"")
        else:
            parts.append(f"Дұрыс жауап — \"{correct_text}\".")
        if user_text:
            parts.append(
                f"\n❌ \"{user_text}\" бұл жерде сөйлемнің мағынасына "
                "немесе құрылымына сай келмейді."
            )
        parts.append(
            "\n🧠 Есте сақта:\nЖауапты таңдағанда бүкіл сөйлемді оқып, "
            "мағынаға толық сай келетін нұсқаны таңдаңыз."
        )

    return "\n".join(parts)


def generate_feedback(question, options, user_answer, correct_answer, topic, level=None):
    """
    Мұғалім сияқты толық түсіндірме жасау: неге дұрыс жауап дұрыс,
    қажет болса грамматикалық ереже, есте сақтау кеңесі және мысал.

    Тек стандартты, жалпыға белгілі грамматикалық фактілер мен
    сұрақтың нақты өз мәтіні қолданылады — ешбір ереже, мысал немесе
    "мәтіндегі дәлел" ойдан шығарылмайды.

    Қайтарады: толық хабарлама (❌/✅ тақырыптарымен бірге) — шақырушы
    тарап бөлек "Дұрыс жауап:" жолын қосудың қажеті жоқ.
    """
    qtype = detect_question_type(topic)
    correct_text = (options or {}).get(correct_answer, correct_answer)
    user_text = (options or {}).get(user_answer)

    if qtype == "tense":
        body = _tense_feedback(question, correct_text, user_text)
    elif qtype == "article":
        body = _article_feedback(question, correct_text, user_text)
    elif qtype == "modal":
        body = _modal_feedback(question, correct_text, user_text)
    else:
        body = _generic_teacher_feedback(question, correct_text, user_text, qtype)

    header = f"❌ Қате жауап\n\n✅ Дұрыс жауап: {correct_answer}) {correct_text}\n\n"
    return header + body


def generate_correct_answer_feedback() -> str:
    """Дұрыс жауапқа өте қысқа, әртүрлі реакция қайтару (түсіндірмесіз)."""
    return random.choice(_CORRECT_TEMPLATES)


def calculate_test_result(correct: int, total: int) -> dict:
    """Тест соңындағы нәтиже мен мотивациялық белгіні есептеу."""
    incorrect = total - correct
    percent = round((correct / total) * 100) if total else 0

    if percent >= 85:
        tag = "⭐ Excellent"
    elif percent >= 60:
        tag = "👍 Good job"
    else:
        tag = "📚 Keep practicing"

    return {
        "correct": correct,
        "incorrect": incorrect,
        "total": total,
        "percent": percent,
        "tag": tag,
    }


def build_progress_bar(current: int, total: int, length: int = 12) -> str:
    """'━━━━░░░░░░░░' үлгісіндегі қарапайым прогресс-жолақ жасау."""
    if total <= 0:
        return "░" * length

    filled = min(length, round(length * current / total))
    return "━" * filled + "░" * (length - filled)


def parse_topic_level_from_filename(filename: str):
    """
    "Tenses_Elementary.docx" -> ("Tenses", "Elementary")
    Файл атауы "Тақырып_Деңгей.кеңейтім" үлгісінде болуы керек.
    Сәйкес келмесе (None, None) қайтарады.
    """
    if "." not in filename:
        return None, None

    name = filename.rsplit(".", 1)[0]
    parts = [p for p in name.split("_") if p.strip()]

    if len(parts) < 2:
        return None, None

    level_raw = parts[-1].replace("-", " ").strip()
    topic_raw = " ".join(parts[:-1]).replace("-", " ").strip()

    level = None
    for lvl in LEVELS:
        if lvl.replace("-", " ").lower() == level_raw.lower():
            level = lvl
            break

    if level is None:
        return None, None

    topic = topic_raw.title()
    return topic, level


_Q_START_RE = re.compile(r"^\s*(?:№\s*)?(\d{1,3})[.\)]\s+(.*)$")
_Q_START_LABELED_RE = re.compile(
    r"^\s*(?:question|q)\s*\.?\s*(\d{1,3})?\s*[:.\)]\s*(.*)$",
    re.IGNORECASE,
)
# A) / A. / A: / A- / A - / a) ... барлығын қабылдайды.
_OPT_RE = re.compile(r"^\s*([A-Da-d])\s*[.\):\-]\s*(.+)$")

# Сұрақ ішінде тікелей берілген жауап жолы: "Answer: B", "Correct
# answer: goes", "Дұрыс жауап: B", "Key: B", т.б.
_INLINE_ANSWER_RE = re.compile(
    r"^\s*(?:answer\s*key|correct\s*answer|answer|correct|key|"
    r"жауабы|дұрыс\s*жауап)\s*[:\-]\s*(.+)$",
    re.IGNORECASE,
)

# Файлдың соңында бөлек жауап кілті бөлімі басталатынын білдіретін
# айдар жолдары (doc: ANSWERS/ANSWER KEY/ЖАУАПТАР/т.б.).
_ANSWER_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:answers?(?:\s*key)?|answers?\s*sheet|correct\s*answers?|"
    r"key|жауаптар|дұрыс\s*жауаптар|жауап\s*кілті)\s*[:\-]?\s*$",
    re.IGNORECASE,
)

# Файлда/сұрақта нақты сұрақ емес қызметтік жолдар (тақырып,
# нұсқаулық, footer, бет нөмірі, т.б.) — бұларды сұрақ деп қабылдамау
# үшін жеңіл сүзгі.
_NON_QUESTION_HINTS = (
    "instructions", "нұсқаулық", "тапсырма", "exercise", "page ",
    "бет ", "topic:", "level:", "тақырып:", "деңгей:",
)


def _norm_text(s: str) -> str:
    """Салыстыру үшін мәтінді жеңілдету (кіші әріп, тыныс белгісіз)."""
    return re.sub(r"[^a-zа-яё0-9]+", "", (s or "").lower())


def _looks_like_question_line(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    return not any(hint in lowered for hint in _NON_QUESTION_HINTS)


def split_question_and_answer_sections(lines):
    """
    Файл ішінен "ANSWERS/ANSWER KEY/ЖАУАПТАР/ДҰРЫС ЖАУАПТАР/ЖАУАП
    КІЛТІ" секілді айдарды тауып, содан кейінгі бөлікті бөлек жауап
    кілті ретінде бөліп алу (сол айдардан кейінгі ешбір жол сұрақ
    деп қабылданбайды).

    Анық айдар табылмаса, файл СОҢЫНАН бастап "1-B", "2-C" секілді
    үздіксіз жолдар тізбегін іздейді (кемінде 3 жол болса, оны да
    жауап кілті бөлімі деп есептейді).

    Қайтарады: (question_lines, key_lines)
    """
    heading_idx = None
    for i, raw in enumerate(lines):
        line = (raw or "").strip()
        if line and _ANSWER_SECTION_HEADING_RE.match(line):
            heading_idx = i
            break

    if heading_idx is not None:
        return lines[:heading_idx], lines[heading_idx + 1:]

    # Анық айдар жоқ — соңынан бастап "N-L" тізбегін іздейміз.
    tail_start = None
    run_len = 0
    for i in range(len(lines) - 1, -1, -1):
        line = (lines[i] or "").strip()
        if not line:
            if run_len > 0:
                break
            continue

        tokens = [line] if "," not in line else [
            t.strip() for t in line.split(",") if t.strip()
        ]
        if tokens and all(_KEY_RE.match(t) for t in tokens):
            tail_start = i
            run_len += 1
            continue
        break

    if tail_start is not None and run_len >= 3:
        return lines[:tail_start], lines[tail_start:]

    return lines, []


def _resolve_inline_answer(raw: str, options: dict):
    """
    "B", "B)", "B) goes" немесе тікелей "goes" секілді жолдан
    дұрыс жауаптың әрпін (A-D) анықтау. Анықталмаса None қайтарады.
    """
    raw = raw.strip()

    # "B) goes" немесе "B. goes" секілді префикс
    m = re.match(r"^([A-Da-d])\s*[.\)]?\s*(.*)$", raw)
    if m:
        return m.group(1).upper()

    # Тек әріп жеке тұрса ("B")
    if re.match(r"^[A-Da-d]$", raw):
        return raw.upper()

    # Толық жауап мәтіні бойынша сәйкестендіру (мыс. "goes")
    norm_raw = _norm_text(raw)
    if norm_raw:
        for letter, text in options.items():
            if _norm_text(text) == norm_raw:
                return letter

    return None


def parse_questions_from_lines(lines):
    """
    Мәтін жолдарынан сұрақтар тізімін шығару. Икемді парсер —
    әртүрлі нөмірлеу стилін ("1.", "1)", "Question 1:", "Q1:",
    "Question:") және A)/A./a)/a. жауап нұсқаларын таниды. Сондай-ақ
    сұрақ ішінде тікелей берілген "Answer:/Correct:/Дұрыс жауап:"
    секілді жолдардан дұрыс жауапты бірден анықтауға тырысады
    (табылса — "inline_answer" өрісіне қосады).

    Қайтарады:
        [{"number": int, "question": str,
          "options": {"A": str, "B": str, "C": str, "D": str},
          "inline_answer": str|None}, ...]
    """
    questions_list = []
    current = None
    auto_number = 0

    def _is_complete(item):
        if item is None or not item["question"].strip():
            return False
        return all(
            item["options"].get(letter, "").strip()
            for letter in ("A", "B", "C", "D")
        )

    n = len(lines)
    i = 0
    while i < n:
        raw_line = lines[i]
        line = (raw_line or "").strip()
        i += 1
        if not line:
            continue

        q_match = _Q_START_RE.match(line)
        labeled_match = None if q_match else _Q_START_LABELED_RE.match(line)

        if (q_match or labeled_match) and _looks_like_question_line(
            (q_match or labeled_match).group(2)
        ):
            if _is_complete(current):
                questions_list.append(current)

            if q_match:
                number = int(q_match.group(1))
                text = q_match.group(2).strip()
                auto_number = max(auto_number, number)
            else:
                number_raw = labeled_match.group(1)
                if number_raw:
                    number = int(number_raw)
                    auto_number = max(auto_number, number)
                else:
                    auto_number += 1
                    number = auto_number
                text = labeled_match.group(2).strip()

            # "Question:" сияқты белгіден кейін мәтін бос болуы мүмкін
            # (сұрақ мәтіні келесі жолда келеді) — бос болса, жаңа
            # блок ашамыз да, келесі мазмұнды жолды сұрақ ретінде
            # аламыз.
            current = {
                "number": number,
                "question": text,
                "options": {},
                "inline_answer": None,
            }
            continue

        opt_match = _OPT_RE.match(line)
        if opt_match and current is not None:
            letter = opt_match.group(1).upper()
            current["options"][letter] = opt_match.group(2).strip()
            continue

        ans_match = _INLINE_ANSWER_RE.match(line)
        if ans_match and current is not None and current["options"]:
            resolved = _resolve_inline_answer(
                ans_match.group(1), current["options"]
            )
            if resolved:
                current["inline_answer"] = resolved
            continue

        # Нөмірсіз, ешбір белгісі жоқ сұрақ: алдыңғы блок жоқ немесе
        # толық аяқталған болса, әрі келесі мазмұнды жол "A)" опциясына
        # сай келсе — мұны жаңа сұрақтың бастауы деп қабылдаймыз
        # (авто-нөмірлеу).
        if (current is None or _is_complete(current)) and _looks_like_question_line(line):
            j = i
            next_line = None
            while j < n:
                candidate = (lines[j] or "").strip()
                if candidate:
                    next_line = candidate
                    break
                j += 1
            if next_line and re.match(r"^\s*[Aa]\s*[.\):\-]", next_line):
                if _is_complete(current):
                    questions_list.append(current)
                auto_number += 1
                current = {
                    "number": auto_number,
                    "question": line,
                    "options": {},
                    "inline_answer": None,
                }
                continue

        # "Question:" style label болғанда сұрақ мәтіні бос қалуы
        # мүмкін — келесі мазмұнды жол сол сұрақтың мәтіні болады.
        if (
            current is not None
            and not current["question"]
            and not current["options"]
        ):
            current["question"] = line
            continue

        # Әйтпесе — ағымдағы сұрақ мәтінінің жалғасы (көп жолды сұрақ,
        # әлі бірде-бір опция басталмаған).
        if current is not None and current["question"] and not current["options"]:
            current["question"] = (current["question"] + " " + line).strip()

    if _is_complete(current):
        questions_list.append(current)

    return questions_list


def parse_questions_detailed(lines):
    """
    parse_questions_from_lines-тің толық нұсқасы: тек толық сұрақтарды
    ғана емес, толық емес (options жетіспейтін) сұрақтарды да бөлек
    қайтарады — админ-ге "N сұрақтың M-і толық анықталды" деп нақты
    есеп беру үшін.

    Қайтарады: (complete_list, incomplete_list)
    incomplete_list-тегі әр элемент — {"number": int, "reason": str}.
    """
    all_blocks = []
    current = None
    auto_number = 0

    for raw_line in lines:
        line = (raw_line or "").strip()
        if not line:
            continue

        q_match = _Q_START_RE.match(line)
        labeled_match = None if q_match else _Q_START_LABELED_RE.match(line)

        if (q_match or labeled_match) and _looks_like_question_line(
            (q_match or labeled_match).group(2)
        ):
            if current is not None:
                all_blocks.append(current)

            if q_match:
                number = int(q_match.group(1))
                text = q_match.group(2).strip()
                auto_number = max(auto_number, number)
            else:
                number_raw = labeled_match.group(1)
                if number_raw:
                    number = int(number_raw)
                    auto_number = max(auto_number, number)
                else:
                    auto_number += 1
                    number = auto_number
                text = labeled_match.group(2).strip()

            current = {
                "number": number,
                "question": text,
                "options": {},
                "inline_answer": None,
            }
            continue

        opt_match = _OPT_RE.match(line)
        if opt_match and current is not None:
            letter = opt_match.group(1).upper()
            current["options"][letter] = opt_match.group(2).strip()
            continue

        ans_match = _INLINE_ANSWER_RE.match(line)
        if ans_match and current is not None and current["options"]:
            resolved = _resolve_inline_answer(
                ans_match.group(1), current["options"]
            )
            if resolved:
                current["inline_answer"] = resolved
            continue

        if (
            current is not None
            and not current["question"]
            and not current["options"]
        ):
            current["question"] = line

    if current is not None:
        all_blocks.append(current)

    complete, incomplete = [], []
    for item in all_blocks:
        if not item["question"].strip():
            incomplete.append({
                "number": item["number"],
                "found": [],
                "missing": ["A", "B", "C", "D"],
                "reason": "сұрақ мәтіні жоқ",
            })
            continue
        found = [
            l for l in ("A", "B", "C", "D")
            if item["options"].get(l, "").strip()
        ]
        missing = [l for l in ("A", "B", "C", "D") if l not in found]
        if missing:
            incomplete.append({
                "number": item["number"],
                "found": found,
                "missing": missing,
                "reason": f"жауап нұсқалары толық емес ({', '.join(missing)})",
            })
            continue
        complete.append(item)

    return complete, incomplete


_KEY_RE = re.compile(
    r"^\s*(\d+)\s*[.\)\-–—:]\s*(?:answer\s*[:\-]?\s*)?([A-Da-d])\s*$",
    re.IGNORECASE,
)


def parse_answer_key_from_lines(lines):
    """
    "1 — B", "1-B", "1. B", "4) D" секілді жолдардан {номер: әріп}
    сөздігін шығару. Сондай-ақ "1-B, 2-C, 3-A" секілді бір жолға
    үтірмен тізілген ықшам форматты да таниды.
    """
    key = {}
    for raw_line in lines:
        line = (raw_line or "").strip()
        if not line:
            continue

        # Бір жолда үтірмен тізілген бірнеше жазба ("1-B, 2-C")
        tokens = [line] if "," not in line else [
            t.strip() for t in line.split(",") if t.strip()
        ]
        for token in tokens:
            match = _KEY_RE.match(token)
            if match:
                key[int(match.group(1))] = match.group(2).upper()
    return key


def apply_trailing_key(parsed_questions, key_lines):
    """
    Файл соңындағы/бөлек парақтағы жауап кілтін ("ANSWERS:" айдарынан
    кейінгі бөлік немесе xlsx-тегі "Answers" парағы) сұрақтарға нөмірі
    бойынша қосу — тек сұрақтың өз ішінде ("Answer: B" секілді жолдан)
    inline_answer әлі табылмаған жағдайда ғана үстінен жазады.
    """
    if not key_lines:
        return parsed_questions

    trailing_key = parse_answer_key_from_lines(key_lines)
    for q in parsed_questions:
        if not q.get("inline_answer") and q["number"] in trailing_key:
            q["inline_answer"] = trailing_key[q["number"]]

    return parsed_questions


def read_txt_lines(file_bytes: bytes):
    text = file_bytes.decode("utf-8", errors="ignore")
    return text.splitlines()


def _iter_docx_block_items(document):
    """
    DOCX құжатын дәйектілік бойынша аралау (параграф пен кесте бір
    ретте кездескен реті бойынша) — стандартты python-docx тәсілі.
    """
    from docx.oxml.ns import qn as _qn
    from docx.table import Table as _Table
    from docx.text.paragraph import Paragraph as _Paragraph

    parent_elm = document.element.body
    for child in parent_elm.iterchildren():
        if child.tag == _qn("w:p"):
            yield _Paragraph(child, document)
        elif child.tag == _qn("w:tbl"):
            yield _Table(child, document)


def read_docx_lines(file_bytes: bytes):
    """
    .docx файлынан параграфтар МЕН кестелердегі мәтінді де оқу
    (кесте түріндегі сұрақ файлдарын қолдау үшін).
    """
    from docx.table import Table as _Table

    doc = Document(BytesIO(file_bytes))
    lines = []

    try:
        blocks = list(_iter_docx_block_items(doc))
    except Exception:
        blocks = None

    if blocks is None:
        # Қандай да бір себеппен дәйекті аралау сәтсіз болса, қарапайым
        # тәсілге ораламыз: параграфтар, содан кейін кестелер.
        lines.extend(p.text for p in doc.paragraphs)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text:
                        lines.append(text)
        return lines

    for block in blocks:
        if isinstance(block, _Table):
            for row in block.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text:
                        lines.append(text)
        else:
            if block.text.strip():
                lines.append(block.text)

    return lines


def read_xlsx_lines(file_bytes: bytes):
    """
    xlsx файлындағы белсенді парақтың барлық ұяшық мәтінін
    жол-жолмен оқу (extract_lines арқылы шақырылғанда — жалғыз
    парақ болған жағдайлар үшін). Бірнеше парақ пен бөлек жауап
    кілті парағын анықтау үшін read_xlsx_lines_and_key қолданылады.
    """
    wb = load_workbook(BytesIO(file_bytes), data_only=True)
    sheet = wb.active

    lines = []
    for row in sheet.iter_rows(values_only=True):
        for cell in row:
            if cell is None:
                continue
            text = str(cell).strip()
            if text:
                lines.append(text)
    return lines


# Парақ атауы осы кілт сөздердің біреуін қамтыса, ол парақ жауап
# кілті ретінде қаралады (мыс. "Answers", "Answer Key", "Жауаптар").
_SHEET_KEY_HINTS = ("answer", "key", "жауап")


def read_xlsx_lines_and_key(file_bytes: bytes):
    """
    xlsx файлындағы БАРЛЫҚ парақтарды аралап, атауында
    "Answers"/"Key"/"Жауаптар" сияқты сөз бар парақты жауап кілті
    ретінде, қалғандарын сұрақ мәтіні ретінде бөлек қайтару.

    Қайтарады: (question_lines, key_lines)
    """
    wb = load_workbook(BytesIO(file_bytes), data_only=True)

    question_lines = []
    key_lines = []

    for sheet in wb.worksheets:
        name_lower = (sheet.title or "").lower()
        is_key_sheet = any(hint in name_lower for hint in _SHEET_KEY_HINTS)

        for row in sheet.iter_rows(values_only=True):
            for cell in row:
                if cell is None:
                    continue
                text = str(cell).strip()
                if not text:
                    continue
                if is_key_sheet:
                    key_lines.append(text)
                else:
                    question_lines.append(text)

    # Бөлек "Answers" парағы болмаса, негізгі мәтіннің өзінен
    # "ANSWERS:" айдарын немесе соңындағы "1-B" тізбегін іздейміз.
    if not key_lines:
        question_lines, key_lines = split_question_and_answer_sections(
            question_lines
        )

    return question_lines, key_lines


def read_pdf_lines(file_bytes: bytes):
    """
    .pdf файлындағы мәтінді бет-бетімен оқу (сұрақтар .docx/.txt
    секілді бірізді мәтін түрінде жазылған деп есептеледі).
    Ескерту: сканерленген (мәтіндік қабаты жоқ) PDF файлдардан
    мәтін шықпайды — файл тек мәтіндік PDF болуы керек.
    """
    reader = PdfReader(BytesIO(file_bytes))

    lines = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        lines.extend(page_text.splitlines())

    return lines


def read_csv_lines(file_bytes: bytes):
    """
    .csv файлындағы әр ұяшықты бөлек жол ретінде оқу (.xlsx-пен
    бірдей логика — сұрақтар бір бағанда бірізді жазылған деп
    есептеледі).
    """
    text = file_bytes.decode("utf-8-sig", errors="ignore")
    reader = csv.reader(StringIO(text))

    lines = []
    for row in reader:
        for cell in row:
            cell_text = (cell or "").strip()
            if cell_text:
                lines.append(cell_text)
    return lines


def read_rtf_lines(file_bytes: bytes):
    """.rtf файлын жай мәтінге түрлендіріп оқу."""
    text = file_bytes.decode("utf-8", errors="ignore")
    plain_text = rtf_to_text(text)
    return plain_text.splitlines()


def read_odt_lines(file_bytes: bytes):
    """.odt (OpenDocument Text) файлындағы параграфтарды оқу."""
    doc = load_odt(BytesIO(file_bytes))
    paragraphs = doc.getElementsByType(odf_text.P)
    return [teletype.extractText(p) for p in paragraphs]


def extract_lines(filename: str, file_bytes: bytes):
    """Кеңейтіміне қарай файл ішінен мәтін жолдарын шығару."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "txt":
        return read_txt_lines(file_bytes)
    if ext == "docx":
        return read_docx_lines(file_bytes)
    if ext == "xlsx":
        return read_xlsx_lines(file_bytes)
    if ext == "pdf":
        return read_pdf_lines(file_bytes)
    if ext == "csv":
        return read_csv_lines(file_bytes)
    if ext == "rtf":
        return read_rtf_lines(file_bytes)
    if ext == "odt":
        return read_odt_lines(file_bytes)

    if ext in UNSUPPORTED_WITH_HINT:
        raise ValueError(UNSUPPORTED_WITH_HINT[ext])

    raise ValueError(f"Қолдау көрсетілмейтін файл түрі: .{ext}")


def extract_lines_and_key(filename: str, file_bytes: bytes):
    """
    Файлдан сұрақ мәтінінің жолдарын ЖӘНЕ (табылса) файл ішіндегі
    бөлек жауап кілті бөлімінің жолдарын бөлек қайтару.

    .xlsx үшін парақ атауы бойынша (Answers/Key/Жауаптар) бөлек
    парақты дереу жауап кілті ретінде таниды. Басқа форматтарда
    "ANSWERS:/ЖАУАПТАР:" айдары немесе файл соңындағы "1-B" тізбегі
    бойынша бөледі (split_question_and_answer_sections).

    Қайтарады: (question_lines, key_lines)
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "xlsx":
        return read_xlsx_lines_and_key(file_bytes)

    lines = extract_lines(filename, file_bytes)
    return split_question_and_answer_sections(lines)


def merge_questions_with_key(questions_list, key):
    """
    Сұрақтар тізімі мен жауап кілтін нөмірі бойынша сәйкестендіру.
    Сыртқы кілтте нөмір табылмаса, сол сұрақтың өз ішінен анықталған
    inline_answer (Answer:/Correct:/Дұрыс жауап: жолынан) қолданылады.

    Қайтарады: (merged_list, missing_numbers)
    merged_list-тегі әр элементте "correct_answer" өрісі қосылады.
    """
    merged = []
    missing = []

    for q in questions_list:
        num = q["number"]
        if num in key:
            q_copy = dict(q)
            q_copy["correct_answer"] = key[num]
            merged.append(q_copy)
        elif q.get("inline_answer"):
            q_copy = dict(q)
            q_copy["correct_answer"] = q["inline_answer"]
            merged.append(q_copy)
        else:
            missing.append(num)

    return merged, missing


_NUM_OPT_RE = re.compile(r"^\s*([1-4])\s*[.\):\-]\s*(.+)$")


def parse_questions_numbered_fallback(lines):
    """
    Кейбір файлдарда жауап нұсқалары A)/B)/C)/D) әрпімен емес,
    1)/2)/3)/4) санымен беріледі. Стандартты parse_questions_from_lines
    мұндайда ЕШБІР толық сұрақ таппайды (себебі А-D мүлде жоқ) —
    сол жағдайда ҒАНА бұл fallback шақырылады (main.py-да).

    Логикасы: сұрақ мәтінінен кейін дәйекті түрде 1,2,3,4 санымен
    басталатын 4 жол келсе, соларды A,B,C,D нұсқалары деп қабылдайды.
    Дәйектілік бұзылса (мыс. 1 орнына 3 келсе), ол жаңа сұрақтың
    басталуы деп есептеледі.

    Қайтарады: parse_questions_from_lines-мен бірдей форматта тізім.
    """
    questions_list = []
    current = None
    expected_next = 1

    def _is_complete(item):
        return item is not None and item["question"].strip() and len(item["options"]) == 4

    for raw_line in lines:
        line = (raw_line or "").strip()
        if not line:
            continue

        num_match = _NUM_OPT_RE.match(line)
        if (
            num_match
            and current is not None
            and current["question"].strip()
            and int(num_match.group(1)) == expected_next
            and len(current["options"]) < 4
        ):
            idx = int(num_match.group(1))
            letter = "ABCD"[idx - 1]
            current["options"][letter] = num_match.group(2).strip()
            expected_next = idx + 1
            continue

        # Жаңа мазмұнды жол — бұл жаңа сұрақтың мәтіні
        if _is_complete(current):
            questions_list.append(current)

        q_match = _Q_START_RE.match(line) or _Q_START_LABELED_RE.match(line)
        text = q_match.group(2).strip() if q_match else line

        if not text:
            continue

        current = {
            "number": len(questions_list) + 1,
            "question": text,
            "options": {},
            "inline_answer": None,
        }
        expected_next = 1

    if _is_complete(current):
        questions_list.append(current)

    return questions_list


def all_have_inline_answers(questions_list) -> bool:
    """Барлық сұрақта inline_answer (файл ішінде табылған жауап) бар ма?"""
    return bool(questions_list) and all(
        q.get("inline_answer") for q in questions_list
    )

# =========================================================
# AI CONVERTER ҮШІН КӨМЕКШІ ФУНКЦИЯЛАР
# ---------------------------------------------------------
# A/B/C/D нұсқасы жоқ, тек "тапсырма + Answers бөлімі" түріндегі
# дайын жаттығу файлдарын тану және жауап бөлімін мәтін ретінде
# оқу. Жоғарыдағы ескі парсерлерге ЕШБІР өзгеріс енгізілмеген.
# =========================================================

_ANSWER_TEXT_RE = re.compile(r"^\s*(\d{1,3})\s*[.\)\-–—:]\s*(.+?)\s*$")


def extract_numbered_answers(key_lines, allow_plain=True):
    """
    Жауап бөліміндегі жолдардан дұрыс жауап мәтіндерін ретімен алу.

        ["1. Has she been running?", "2. Has it been raining?"]
        -> ["Has she been running?", "Has it been raining?"]

    Нөмірленген жолдар болса, тек солар алынады (нөмірсіз тақырыптар
    елеусіз қалады). Нөмірленген жол мүлде болмаса және
    allow_plain=True болса, әр бос емес жол бір жауап деп саналады.
    """
    numbered = []
    plain = []
    for raw in key_lines or []:
        line = (raw or "").strip()
        if not line:
            continue
        match = _ANSWER_TEXT_RE.match(line)
        if match:
            numbered.append(match.group(2).strip())
        else:
            plain.append(line)

    if numbered:
        return numbered
    return plain if allow_plain else []


def is_prompt_style_exercise(question_lines, key_lines):
    """
    Файл A/B/C/D нұсқасыз "тапсырма + жауап" жаттығуы ма, соны анықтау
    (мыс. "1. she / run much recently?" + "Answers: 1. Has she been
    running much recently?"). Бұл жағдайда нөмірленген 1-4 жолдарды
    жауап нұсқасы деп қабылдайтын numbered fallback қате сұрақ жасап
    жіберуі мүмкін, сондықтан мұндай файлды AI Converter-ге бағыттау
    керек.

    True қайтарады, егер:
      1) файлда жауап бөлімі бар және ол "1-B" секілді әріптік кілт
         емес, толық жауап мәтіндері болса; немесе
      2) сұрақ бөлігінде 1, 2, 3, 4, 5... деп үзіліссіз өсетін кемінде
         5 нөмірленген жол болса (нұсқалар әрқашан 1-4 ғана, әр
         сұраққа қайта басталады, ал тапсырмалар тізімі үздіксіз өседі).
    """
    has_key_text = any((line or "").strip() for line in key_lines or [])
    if has_key_text and not parse_answer_key_from_lines(key_lines):
        return True

    expected = 1
    for raw in question_lines or []:
        line = (raw or "").strip()
        if not line:
            continue
        match = _Q_START_RE.match(line)
        if not match:
            expected = 1
            continue

        number = int(match.group(1))
        if number == expected:
            if number >= 5:
                return True
            expected += 1
        elif number == 1:
            expected = 2
        else:
            expected = 1

    return False