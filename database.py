import sqlite3


DB_NAME = "bot_database.db"


def init_db():
    """Деректер базасын және кестелерді құру (+ескі базаны жаңарту)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            score INTEGER DEFAULT 0,
            tests_completed INTEGER DEFAULT 0,
            total_correct INTEGER DEFAULT 0,
            total_answered INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            level TEXT NOT NULL,
            question_text TEXT NOT NULL UNIQUE,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            explanation TEXT
        )
    """)

    # Бұрын жасалған базаларда жаңа бағандар болмауы мүмкін —
    # қауіпсіз түрде қосамыз (бар болса, қате елемей өтеді).
    for stmt in (
        "ALTER TABLE users ADD COLUMN tests_completed INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN total_correct INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN total_answered INTEGER DEFAULT 0",
        "ALTER TABLE questions ADD COLUMN explanation TEXT",
    ):
        try:
            cursor.execute(stmt)
        except sqlite3.OperationalError:
            pass  # баған бұрыннан бар

    conn.commit()
    conn.close()


def register_user(user_id, full_name):
    """Пайдаланушыны тіркеу немесе атын жаңарту."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO users (user_id, full_name, score)
        VALUES (?, ?, 0)
        ON CONFLICT(user_id)
        DO UPDATE SET full_name = excluded.full_name
        """,
        (user_id, full_name)
    )

    conn.commit()
    conn.close()


def get_user_score(user_id):
    """Пайдаланушының ұпайын алу."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT score FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    if result:
        return result[0]

    return 0


def add_score(user_id, points=1):
    """Пайдаланушыға ұпай қосу."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET score = score + ?
        WHERE user_id = ?
        """,
        (points, user_id)
    )

    conn.commit()
    conn.close()


def record_test_completion(user_id, correct, total):
    """
    Тест аяқталғанда шақырылады: жалпы ұпайды (score), тапсырылған
    тест санын және жиналған дұрыс/жалпы жауап санын жаңартады.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET score = score + ?,
            tests_completed = tests_completed + 1,
            total_correct = total_correct + ?,
            total_answered = total_answered + ?
        WHERE user_id = ?
        """,
        (correct, correct, total, user_id),
    )

    conn.commit()
    conn.close()


def get_user_stats(user_id):
    """Пайдаланушының жеке статистикасы (📊 Менің статистикам үшін)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT tests_completed, total_correct, total_answered, score
        FROM users WHERE user_id = ?
        """,
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {
            "tests_completed": 0, "total_correct": 0,
            "total_answered": 0, "score": 0, "average_percent": 0,
        }

    tests_completed, total_correct, total_answered, score = row
    average_percent = (
        round((total_correct / total_answered) * 100)
        if total_answered else 0
    )

    return {
        "tests_completed": tests_completed or 0,
        "total_correct": total_correct or 0,
        "total_answered": total_answered or 0,
        "score": score or 0,
        "average_percent": average_percent,
    }


def get_all_students():
    """Рейтинг бойынша барлық оқушыларды алу."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT full_name, score
        FROM users
        ORDER BY score DESC
        """
    )

    students = cursor.fetchall()

    conn.close()

    return students


# =========================
# Сұрақтар базасы
# =========================

def add_question(
    topic,
    level,
    question_text,
    option_a,
    option_b,
    option_c,
    option_d,
    correct_answer,
    explanation=None,
):
    """
    Сұрақты базаға қосу.
    question_text бірдей сұрақ бұрын бар болса, қайталанған деп
    есептеліп, False қайтарылады және ештеңе қосылмайды.
    explanation — міндетті емес (әкімші панелінен қолмен енгізілген
    сұрақтарға ғана); файлдан жүктелгендерде әдетте бос қалады, ол
    кезде бот түсіндірмені өзі динамикалық жасайды.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO questions
                (topic, level, question_text,
                 option_a, option_b, option_c, option_d,
                 correct_answer, explanation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic,
                level,
                question_text,
                option_a,
                option_b,
                option_c,
                option_d,
                correct_answer,
                explanation,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def question_exists(question_text):
    """question_text базада бар-жоғын тексеру (импорт алдын ала қарауы үшін)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM questions WHERE question_text = ? LIMIT 1",
        (question_text,),
    )
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def get_topics():
    """Базадағы барлық бірегей тақырыптар тізімі."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT topic FROM questions ORDER BY topic")
    topics = [row[0] for row in cursor.fetchall()]

    conn.close()
    return topics


def get_levels_for_topic(topic):
    """Белгілі бір тақырып бойынша қолжетімді деңгейлер."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT DISTINCT level FROM questions WHERE topic = ? ORDER BY level",
        (topic,),
    )
    levels = [row[0] for row in cursor.fetchall()]

    conn.close()
    return levels


def count_questions(topic, level):
    """Тақырып пен деңгей бойынша сұрақ саны."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM questions WHERE topic = ? AND level = ?",
        (topic, level),
    )
    total = cursor.fetchone()[0]

    conn.close()
    return total


def get_random_questions(topic, level, limit=40):
    """
    Тақырып пен деңгейге сай кездейсоқ сұрақтар.
    Базада limit-тен аз сұрақ болса, барлығын қайтарады.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, question_text, option_a, option_b, option_c, option_d,
               correct_answer, explanation
        FROM questions
        WHERE topic = ? AND level = ?
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (topic, level, limit),
    )
    rows = cursor.fetchall()

    conn.close()
    return rows


# =========================================================
# Admin панелі: CRUD, парақтау, сүзгі, статистика
# =========================================================

_Q_COLUMNS = (
    "id, topic, level, question_text, option_a, option_b, "
    "option_c, option_d, correct_answer, explanation"
)


def get_question_by_id(qid):
    """Бір сұрақты толық түрде id арқылы алу (dict немесе None)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(f"SELECT {_Q_COLUMNS} FROM questions WHERE id = ?", (qid,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    keys = ("id", "topic", "level", "question_text", "option_a",
            "option_b", "option_c", "option_d", "correct_answer",
            "explanation")
    return dict(zip(keys, row))


def list_questions(topic=None, level=None, offset=0, limit=5):
    """Сұрақтарды парақтап (pagination), тақырып/деңгей бойынша сүзіп алу."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    where = []
    params = []
    if topic:
        where.append("topic = ?")
        params.append(topic)
    if level:
        where.append("level = ?")
        params.append(level)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    params += [limit, offset]
    cursor.execute(
        f"SELECT {_Q_COLUMNS} FROM questions {where_sql} "
        "ORDER BY id LIMIT ? OFFSET ?",
        params,
    )
    rows = cursor.fetchall()
    conn.close()

    keys = ("id", "topic", "level", "question_text", "option_a",
            "option_b", "option_c", "option_d", "correct_answer",
            "explanation")
    return [dict(zip(keys, row)) for row in rows]


def count_all_questions(topic=None, level=None):
    """Сүзгіге сай сұрақтардың жалпы саны (pagination үшін)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    where = []
    params = []
    if topic:
        where.append("topic = ?")
        params.append(topic)
    if level:
        where.append("level = ?")
        params.append(level)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    cursor.execute(f"SELECT COUNT(*) FROM questions {where_sql}", params)
    total = cursor.fetchone()[0]
    conn.close()
    return total


def update_question(qid, **fields):
    """
    Сұрақтың бір немесе бірнеше өрісін жаңарту.
    Мысалы: update_question(5, question_text="...", correct_answer="B")
    """
    allowed = {
        "topic", "level", "question_text", "option_a", "option_b",
        "option_c", "option_d", "correct_answer", "explanation",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [qid]

    try:
        cursor.execute(
            f"UPDATE questions SET {set_clause} WHERE id = ?", params
        )
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def delete_question(qid):
    """Сұрақты id арқылы өшіру. Сұрақ табылмаса False қайтарады."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM questions WHERE id = ?", (qid,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def get_admin_stats():
    """Әкімші панеліндегі 📊 Quiz статистикасы үшін жиынтық дерек."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM questions")
    total_questions = cursor.fetchone()[0]

    cursor.execute(
        "SELECT topic, COUNT(*) FROM questions GROUP BY topic ORDER BY topic"
    )
    by_topic = cursor.fetchall()

    cursor.execute(
        "SELECT level, COUNT(*) FROM questions GROUP BY level ORDER BY level"
    )
    by_level = cursor.fetchall()

    cursor.execute("SELECT COALESCE(SUM(tests_completed), 0) FROM users")
    tests_completed = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COALESCE(SUM(total_correct), 0), COALESCE(SUM(total_answered), 0) "
        "FROM users"
    )
    total_correct, total_answered = cursor.fetchone()
    avg_percent = (
        round((total_correct / total_answered) * 100) if total_answered else 0
    )

    conn.close()

    return {
        "total_users": total_users,
        "total_questions": total_questions,
        "by_topic": by_topic,
        "by_level": by_level,
        "tests_completed": tests_completed,
        "average_percent": avg_percent,
    }