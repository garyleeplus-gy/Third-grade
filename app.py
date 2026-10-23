import streamlit as st
import fitz  # PyMuPDF
from PIL import Image
import numpy as np
import pandas as pd
import sqlite3
import re
import json
import random
import hashlib
from datetime import datetime

# ==========================================
# 0. 頁面與行動裝置自適應設定 (RWD)
# ==========================================
st.set_page_config(
    page_title="小三全科複習神器｜自然・社會・數學・國語",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_PATH = "science_persistent_store.db"
PARSER_VERSION = "4.1"  # 自動清除多餘數學符號並升級資料庫
SUBJECT_LIST = ["🌿 自然", "🌏 社會", "🔢 數學", "📘 國語"]


# ==========================================
# 1. 基礎文字與數學符號清理工具函式
# ==========================================
def clean_math_symbols(text: str) -> str:
    """清除多餘的 LaTeX 符號 ($、\\times、\\text、imes、ext) 並轉為國小標準數學符號"""
    if not text:
        return ""
    s = str(text)
    # 修正因 \t 跳脫字元產生的 imes 與 ext
    s = re.sub(r'[\t\\]*imes\b', ' × ', s)
    s = re.sub(r'\\times\b', ' × ', s)
    s = re.sub(r'\\div\b', ' ÷ ', s)
    s = re.sub(r'\\cdot\b', '、', s)
    s = re.sub(r'[\t\\]*ext\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\boxed\{([^}]*)\}', r'(\1)', s)
    s = re.sub(r'\\frac\{([^}]*)\}\{([^}]*)\}', r'\1/\2', s)
    # 移除包夾在算式兩側的 $ 符號
    s = s.replace('$', '')
    # 修正多重空白
    s = re.sub(r' {2,}', ' ', s)
    return s.strip()


def clean_cite(text: str) -> str:
    """移除來源標記與多餘數學符號以維持畫面整潔"""
    s = re.sub(r'\s*\[cite:\s*[\d,\s\-]+\]', '', str(text))
    return clean_math_symbols(s)


def clean_pdf_text(raw_text: str) -> str:
    """清除 PDF 內嵌注音符號、浮水印與數學 OCR 雜訊"""
    if not raw_text:
        return ""
    # 移除緊跟在中文字後的注音符號與聲調
    cleaned = re.sub(r'(?<=[\u4e00-\u9fff])[\u3105-\u3129\u02CA\u02C7\u02CB\u02D9ˊˇˋ˙]+', '', str(raw_text))
    # 移除獨立成串的注音拼音與聲調（保留單獨作為代號的 ㄅㄆㄇㄈ）
    cleaned = re.sub(r'[\u3105-\u3129]{2,}[\u02CA\u02C7\u02CB\u02D9ˊˇˋ˙]*', '', cleaned)
    cleaned = re.sub(r'xyz88\.\w+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'有著作權.*?必究\s*\d*', '', cleaned)
    cleaned = re.sub(r'教師用書|貼心伴隨|敬請賜教|敬請|賜教', '', cleaned)
    # 修正南一數學 PDF 中常將數字「1」誤認成直線「|」或「I」的情況
    cleaned = re.sub(r'[|｜I](?=[個張盒瓶桶包袋枝條塊份雙打箱公毫平頂顆朵隻臺件元次天週星期年])', '1', cleaned)
    return clean_math_symbols(cleaned)


def detect_subject_from_name(name: str) -> str:
    """從檔案名稱或單元名稱自動判斷科目"""
    if "社會" in name:
        return "🌏 社會"
    elif "數學" in name or "隨堂演練" in name:
        return "🔢 數學"
    elif "國語" in name or "課堂練習" in name:
        return "📘 國語"
    return "🌿 自然"


def render_masked_text(text: str, hide_keywords: bool) -> str:
    """將 【關鍵字】 轉換為重點標示或遮罩填空"""
    text = clean_cite(text)
    if hide_keywords:
        return re.sub(r'【(.*?)】', r'<span class="hidden-word">＿＿＿＿</span>', text)
    else:
        return re.sub(r'【(.*?)】', r'<span class="mask-word">\1</span>', text)


def render_html_table(columns: list, rows: list, hide_kw: bool = False):
    html = '<div style="overflow-x:auto;"><table class="custom-table"><thead><tr>'
    for col in columns:
        html += f"<th>{clean_cite(col)}</th>"
    html += "</tr></thead><tbody>"
    for r in rows:
        html += "<tr>"
        for idx, cell in enumerate(r):
            c_str = clean_cite(cell)
            if hide_kw and idx > 0:
                c_str = '<span class="hidden-word">＿＿＿＿＿＿＿＿</span>'
            html += f"<td>{c_str}</td>"
        html += "</tr>"
    html += "</tbody></table></div>"
    st.markdown(html, unsafe_allow_html=True)


# ==========================================
# 2. SQLite 永久儲存資料庫引擎（支援多科目）
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS meta_info (key TEXT PRIMARY KEY, val TEXT)")
    c.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_pdfs (
            filename TEXT PRIMARY KEY,
            subject TEXT DEFAULT '🌿 自然',
            pdf_bytes BLOB,
            upload_time TEXT
        )
    """)
    c.execute("PRAGMA table_info(uploaded_pdfs)")
    u_cols = [r[1] for r in c.fetchall()]
    if "subject" not in u_cols:
        c.execute("ALTER TABLE uploaded_pdfs ADD COLUMN subject TEXT DEFAULT '🌿 自然'")

    c.execute("""
        CREATE TABLE IF NOT EXISTS custom_questions (
            q_id TEXT PRIMARY KEY,
            subject TEXT DEFAULT '🌿 自然',
            pdf_name TEXT,
            page_num INTEGER,
            seq_order INTEGER DEFAULT 0,
            q_json TEXT
        )
    """)
    c.execute("PRAGMA table_info(custom_questions)")
    c_cols = [r[1] for r in c.fetchall()]
    if "seq_order" not in c_cols:
        c.execute("ALTER TABLE custom_questions ADD COLUMN seq_order INTEGER DEFAULT 0")
    if "subject" not in c_cols:
        c.execute("ALTER TABLE custom_questions ADD COLUMN subject TEXT DEFAULT '🌿 自然'")

    c.execute("""
        CREATE TABLE IF NOT EXISTS wrong_questions (
            q_id TEXT PRIMARY KEY,
            subject TEXT DEFAULT '🌿 自然',
            q_json TEXT,
            user_last_ans TEXT,
            wrong_count INTEGER DEFAULT 1,
            updated_at TEXT
        )
    """)
    c.execute("PRAGMA table_info(wrong_questions)")
    w_cols = [r[1] for r in c.fetchall()]
    if "subject" not in w_cols:
        c.execute("ALTER TABLE wrong_questions ADD COLUMN subject TEXT DEFAULT '🌿 自然'")

    c.execute("""
        CREATE TABLE IF NOT EXISTS exam_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT DEFAULT '🌿 自然',
            exam_title TEXT,
            score INTEGER,
            correct_count INTEGER,
            total_count INTEGER,
            created_at TEXT
        )
    """)
    c.execute("PRAGMA table_info(exam_history)")
    h_cols = [r[1] for r in c.fetchall()]
    if "subject" not in h_cols:
        c.execute("ALTER TABLE exam_history ADD COLUMN subject TEXT DEFAULT '🌿 自然'")

    conn.commit()
    conn.close()


def get_question_id(q_dict: dict) -> str:
    raw = f"{q_dict.get('subject','')}_{q_dict.get('unit','')}_{q_dict.get('type','')}_{q_dict.get('q','')}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def db_save_pdf(filename: str, pdf_bytes: bytes, subject: str = None):
    if not subject:
        subject = detect_subject_from_name(filename)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO uploaded_pdfs (filename, subject, pdf_bytes, upload_time) VALUES (?, ?, ?, ?)",
        (filename, subject, sqlite3.Binary(pdf_bytes), datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()


def db_delete_pdf(filename: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM uploaded_pdfs WHERE filename = ?", (filename,))
    c.execute("DELETE FROM custom_questions WHERE pdf_name = ?", (filename,))
    conn.commit()
    conn.close()


def db_load_all_pdfs(subject: str = None) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if subject and subject != "🌟 全部科目":
        c.execute("SELECT filename, pdf_bytes, subject FROM uploaded_pdfs WHERE subject = ? ORDER BY upload_time DESC", (subject,))
    else:
        c.execute("SELECT filename, pdf_bytes, subject FROM uploaded_pdfs ORDER BY upload_time DESC")
    rows = c.fetchall()
    conn.close()
    return {r[0]: {"bytes": bytes(r[1]), "subject": r[2] or detect_subject_from_name(r[0])} for r in rows}


def db_replace_pdf_questions(pdf_name: str, questions: list, page_num: int = None, subject: str = None):
    if not subject:
        subject = detect_subject_from_name(pdf_name)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if page_num is not None:
        c.execute("DELETE FROM custom_questions WHERE pdf_name = ? AND page_num = ?", (pdf_name, page_num))
    else:
        c.execute("DELETE FROM custom_questions WHERE pdf_name = ?", (pdf_name,))

    for idx, q in enumerate(questions):
        q["subject"] = q.get("subject", subject)
        qid = get_question_id(q)
        p_num = q.get("page_num", page_num if page_num is not None else 1)
        c.execute(
            "INSERT OR REPLACE INTO custom_questions (q_id, subject, pdf_name, page_num, seq_order, q_json) VALUES (?, ?, ?, ?, ?, ?)",
            (qid, q["subject"], pdf_name, p_num, idx, json.dumps(q, ensure_ascii=False))
        )
    conn.commit()
    conn.close()


def db_save_custom_questions(questions: list, pdf_name: str = "手動/PDF匯入", page_num: int = 0, subject: str = "🌿 自然"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for idx, q in enumerate(questions):
        q_subj = q.get("subject", subject)
        q["subject"] = q_subj
        qid = get_question_id(q)
        p_num = q.get("page_num", page_num)
        p_name = q.get("pdf_name", pdf_name)
        c.execute(
            "INSERT OR REPLACE INTO custom_questions (q_id, subject, pdf_name, page_num, seq_order, q_json) VALUES (?, ?, ?, ?, ?, ?)",
            (qid, q_subj, p_name, p_num, idx + 100, json.dumps(q, ensure_ascii=False))
        )
    conn.commit()
    conn.close()


def db_load_custom_questions(subject: str = None) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if subject and subject != "🌟 全部科目":
        c.execute("SELECT q_json FROM custom_questions WHERE subject = ? ORDER BY pdf_name, page_num, seq_order", (subject,))
    else:
        c.execute("SELECT q_json FROM custom_questions ORDER BY pdf_name, page_num, seq_order")
    rows = c.fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]


def db_add_wrong_question(q_dict: dict, user_ans_str: str):
    qid = get_question_id(q_dict)
    subj = q_dict.get("subject", detect_subject_from_name(q_dict.get("unit", "")))
    q_dict["subject"] = subj
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT wrong_count FROM wrong_questions WHERE q_id = ?", (qid,))
    row = c.fetchone()
    w_count = (row[0] + 1) if row else 1
    c.execute(
        "INSERT OR REPLACE INTO wrong_questions (q_id, subject, q_json, user_last_ans, wrong_count, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (qid, subj, json.dumps(q_dict, ensure_ascii=False), str(user_ans_str), w_count, datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()


def db_remove_wrong_question(qid: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM wrong_questions WHERE q_id = ?", (qid,))
    conn.commit()
    conn.close()


def db_clear_all_wrong_questions(subject: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if subject and subject != "🌟 全部科目":
        c.execute("DELETE FROM wrong_questions WHERE subject = ?", (subject,))
    else:
        c.execute("DELETE FROM wrong_questions")
    conn.commit()
    conn.close()


def db_load_wrong_questions(subject: str = None) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if subject and subject != "🌟 全部科目":
        c.execute("SELECT q_id, q_json, user_last_ans, wrong_count, updated_at, subject FROM wrong_questions WHERE subject = ? ORDER BY updated_at DESC", (subject,))
    else:
        c.execute("SELECT q_id, q_json, user_last_ans, wrong_count, updated_at, subject FROM wrong_questions ORDER BY updated_at DESC")
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        item = json.loads(r[1])
        item["_qid"] = r[0]
        item["_last_ans"] = r[2]
        item["_wrong_count"] = r[3]
        item["_updated_at"] = r[4]
        item["subject"] = r[5] or item.get("subject", "🌿 自然")
        result.append(item)
    return result


def db_save_exam_history(title: str, score: int, correct_cnt: int, total_cnt: int, subject: str = "🌿 自然"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO exam_history (subject, exam_title, score, correct_count, total_count, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (subject, title, score, correct_cnt, total_cnt, datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()


def db_load_exam_history(subject: str = None) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if subject and subject != "🌟 全部科目":
        c.execute("SELECT subject, exam_title, score, correct_count, total_count, created_at FROM exam_history WHERE subject = ? ORDER BY id DESC LIMIT 30", (subject,))
    else:
        c.execute("SELECT subject, exam_title, score, correct_count, total_count, created_at FROM exam_history ORDER BY id DESC LIMIT 30")
    rows = c.fetchall()
    conn.close()
    return [{"科目": r[0], "測驗名稱": r[1], "分數": f"{r[2]} 分", "答對題數": f"{r[3]} / {r[4]}", "測驗時間": r[5]} for r in rows]


init_db()


# ==========================================
# 3. 動態大字體與 RWD 自適應樣式注入
# ==========================================
def apply_responsive_css(scale_pct: int):
    base_rem = round(scale_pct / 100.0, 2)
    title_rem = round(base_rem * 1.3, 2)
    card_rem = round(base_rem * 1.1, 2)

    st.markdown(f"""
<style>
.main .block-container {{
    padding-top: 1rem;
    padding-bottom: 3rem;
    padding-left: clamp(0.8rem, 2.5vw, 2.2rem);
    padding-right: clamp(0.8rem, 2.5vw, 2.2rem);
    max-width: 1360px;
}}
html, body, [data-testid="stAppViewContainer"] {{
    font-size: {scale_pct}% !important;
}}
div[data-testid="stMarkdownContainer"] p,
div[data-testid="stMarkdownContainer"] li {{
    font-size: {card_rem}rem !important;
    line-height: 1.75 !important;
}}
label[data-baseweb="radio"], label[data-baseweb="checkbox"] {{
    padding: 8px 14px !important;
    margin-bottom: 6px !important;
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 10px;
    min-height: 48px;
    display: flex !important;
    align-items: center !important;
}}
label[data-baseweb="radio"]:hover, label[data-baseweb="checkbox"]:hover {{
    background-color: #e0f2fe;
    border-color: #38bdf8;
}}
label[data-baseweb="radio"] div, label[data-baseweb="checkbox"] div {{
    font-size: {card_rem}rem !important;
    font-weight: 600 !important;
    color: #0f172a !important;
}}
div[data-baseweb="select"] > div,
div[data-baseweb="input"] input {{
    font-size: {card_rem}rem !important;
    min-height: 48px !important;
    font-weight: 600 !important;
}}
.review-card {{
    background-color: #f8fafc;
    border-left: 7px solid #10b981;
    padding: 16px 20px;
    border-radius: 12px;
    margin-bottom: 14px;
    box-shadow: 0 2px 5px rgba(0,0,0,0.06);
    font-size: {card_rem}rem !important;
    line-height: 1.8 !important;
    color: #1e293b;
}}
.exp-card {{
    background-color: #fffbeb;
    border-left: 7px solid #f59e0b;
    padding: 16px 20px;
    border-radius: 12px;
    margin-bottom: 14px;
    box-shadow: 0 2px 5px rgba(0,0,0,0.06);
    font-size: {card_rem}rem !important;
    line-height: 1.8 !important;
    color: #1e293b;
}}
.q-box {{
    background-color: #ffffff;
    border: 2px solid #e2e8f0;
    border-left: 8px solid #3b82f6;
    padding: 16px 20px;
    border-radius: 14px;
    margin-bottom: 12px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.05);
    font-size: {title_rem}rem !important;
    font-weight: 700;
    color: #0f172a;
}}
.mask-word {{
    background-color: #fef08a;
    color: #b45309;
    padding: 3px 8px;
    border-radius: 6px;
    font-weight: 800;
    border-bottom: 2px dashed #f59e0b;
}}
.hidden-word {{
    background-color: #cbd5e1;
    color: transparent;
    padding: 3px 10px;
    border-radius: 6px;
    user-select: none;
    border-bottom: 3px solid #475569;
    cursor: pointer;
}}
div.stButton > button, div[data-testid="stFormSubmitButton"] > button {{
    border-radius: 12px !important;
    font-size: {card_rem}rem !important;
    font-weight: 700 !important;
    min-height: 52px !important;
    padding: 0.6rem 1.2rem !important;
}}
.custom-table {{
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0 18px 0;
    font-size: {card_rem}rem !important;
    background: white;
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}}
.custom-table th {{
    background-color: #0284c7;
    color: #ffffff;
    text-align: left;
    padding: 10px 14px;
    font-weight: 700;
}}
.custom-table td {{
    padding: 10px 14px;
    border-bottom: 1px solid #e2e8f0;
    color: #1e293b;
    line-height: 1.6;
}}
.custom-table tr:nth-child(even) {{
    background-color: #f8fafc;
}}
@media (max-width: 768px) {{
    .main .block-container {{
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
    }}
    .q-box {{
        padding: 12px 14px !important;
        font-size: {card_rem}rem !important;
    }}
}}
</style>
""", unsafe_allow_html=True)


# ==========================================
# 4. 連連看 SVG 畫線元件（無縮排，100% 渲染圖形）
# ==========================================
def render_svg_matching(left_items: list, right_items: list, user_pairs: dict, correct_pairs: dict = None, show_grading: bool = False):
    """繪製連連看左右配對與彩色連線 SVG"""
    n_rows = max(len(left_items), len(right_items))
    row_h = 68
    svg_h = n_rows * row_h + 28
    colors = ["#2563eb", "#d97706", "#7c3aed", "#059669", "#db2777", "#0891b2"]

    svg_elements = []

    if not show_grading:
        for i, l_text in enumerate(left_items):
            chosen_r = user_pairs.get(l_text)
            if chosen_r and chosen_r in right_items:
                j = right_items.index(chosen_r)
                y1 = 40 + i * row_h
                y2 = 40 + j * row_h
                stroke_col = colors[i % len(colors)]
                svg_elements.append(
                    f'<line x1="280" y1="{y1}" x2="470" y2="{y2}" stroke="{stroke_col}" stroke-width="4.5" stroke-linecap="round"/>'
                )
    else:
        if correct_pairs:
            for i, l_text in enumerate(left_items):
                chosen_r = user_pairs.get(l_text)
                corr_r = correct_pairs.get(l_text)
                if chosen_r and chosen_r in right_items and chosen_r != corr_r:
                    j_wrong = right_items.index(chosen_r)
                    y1 = 40 + i * row_h
                    y2 = 40 + j_wrong * row_h
                    svg_elements.append(
                        f'<line x1="280" y1="{y1}" x2="470" y2="{y2}" stroke="#dc2626" stroke-width="3.5" stroke-dasharray="8,6" opacity="0.85" stroke-linecap="round"/>'
                    )
            for i, l_text in enumerate(left_items):
                corr_r = correct_pairs.get(l_text)
                if corr_r and corr_r in right_items:
                    j_corr = right_items.index(corr_r)
                    y1 = 40 + i * row_h
                    y2 = 40 + j_corr * row_h
                    svg_elements.append(
                        f'<line x1="280" y1="{y1}" x2="470" y2="{y2}" stroke="#16a34a" stroke-width="5" stroke-linecap="round"/>'
                    )

    for i, l_text in enumerate(left_items):
        cy = 40 + i * row_h
        l_clean = clean_cite(l_text).replace("<", "&lt;").replace(">", "&gt;")
        status_badge = ""
        box_fill = "#eff6ff"
        box_stroke = "#3b82f6"
        if show_grading and correct_pairs:
            is_ok = (user_pairs.get(l_text) == correct_pairs.get(l_text))
            status_badge = "✔️ " if is_ok else "❌ "
            box_fill = "#f0fdf4" if is_ok else "#fef2f2"
            box_stroke = "#16a34a" if is_ok else "#dc2626"

        svg_elements.append(
            f'<rect x="10" y="{cy-24}" width="270" height="48" rx="10" fill="{box_fill}" stroke="{box_stroke}" stroke-width="2.2"/>'
            f'<text x="20" y="{cy+6}" font-size="15.5" font-weight="bold" fill="#0f172a">{status_badge}{l_clean[:16]}</text>'
            f'<circle cx="280" cy="{cy}" r="6.5" fill="{box_stroke}"/>'
        )

    for j, r_text in enumerate(right_items):
        cy = 40 + j * row_h
        r_clean = clean_cite(r_text).replace("<", "&lt;").replace(">", "&gt;")
        svg_elements.append(
            f'<rect x="470" y="{cy-24}" width="320" height="48" rx="10" fill="#f0fdf4" stroke="#10b981" stroke-width="2.2"/>'
            f'<text x="484" y="{cy+6}" font-size="15.5" font-weight="bold" fill="#065f46">{r_clean[:20]}</text>'
            f'<circle cx="470" cy="{cy}" r="6.5" fill="#10b981"/>'
        )

    legend_html = ""
    if show_grading:
        legend_html = (
            '<div style="padding:8px 14px; background:#f8fafc; border-bottom:1px solid #e2e8f0; font-weight:700; font-size:0.95rem;">'
            '🟢 <span style="color:#16a34a;">綠色實線：正確完整解答連線</span> &nbsp;｜&nbsp; '
            '🔴 <span style="color:#dc2626;">紅色虛線：你連錯的線</span>'
            '</div>'
        )

    svg_html = (
        f'<div style="width:100%; overflow-x:auto; background:#ffffff; border:2px solid #cbd5e1; border-radius:12px; margin:8px 0;">'
        f'{legend_html}'
        f'<div style="padding:10px;">'
        f'<svg viewBox="0 0 800 {svg_h}" width="100%" height="{svg_h}" style="min-width:580px; display:block;">'
        f'{"".join(svg_elements)}'
        f'</svg>'
        f'</div>'
        f'</div>'
    )
    st.markdown(svg_html, unsafe_allow_html=True)

    if show_grading and correct_pairs:
        rows = []
        for l_text in left_items:
            u_val = user_pairs.get(l_text, "")
            c_val = correct_pairs.get(l_text, "")
            is_ok = (u_val == c_val)
            rows.append([
                clean_cite(l_text),
                clean_cite(u_val) if u_val else "⚠️ 未連線",
                "✔️ 答對" if is_ok else "❌ 答錯",
                f"🎯 {clean_cite(c_val)}"
            ])
        render_html_table(["左方題目項目", "你的連線選擇", "批改結果", "完整正確連線答案"], rows)


# ==========================================
# 5. 四大科目完整《重點複習》知識庫（無任何多餘符號）
# ==========================================
SUBJECT_REVIEW_DATA = {
    "🌿 自然": {
        "單元1：多采多姿的植物": {
            "icon": "🌱",
            "activities": {
                "活動1：植物是什麼": {
                    "points": [
                        "① 自然環境中包含有生命的【生物】和沒有生命的【非生物】（如石頭、水、空氣、陽光）；生物中有些是【動物】（如蜜蜂、蝴蝶、龜），有些是【植物】（如榕樹、長春花、朱槿）。",
                        "② 不同植物的身體外形各有不同，但大部分可分成【根】、【莖】、【葉】、【花】、【果實】和【種子】等六大構造，且不同構造各有不同的功能。",
                        "③ 圖解實例：【長春花（日日春）】具有粉紅色的【花】、綠色的【葉】與直立的【莖】、深入土裡的【根】，開花後結出細長的【果實】，果實成熟後會裂開露出裡面的【種子】。",
                        "④ 觀察植物時須注意安全，可以用手摸樹幹觸感、用手掌比較葉子大小、用眼睛欣賞花朵形狀與顏色，但【絕對不可將不知名植物的果實或葉片放入口中嘗味道】。"
                    ],
                    "tables": [
                        {
                            "title": "📊 自然環境中的生物與非生物分類表",
                            "columns": ["分類", "細項", "常見例子"],
                            "rows": [
                                ["生物（有生命，會長大）", "植物", "長春花（日日春）、榕樹、樟樹、朱槿、南美蟛蜞菊、仙丹花、牽牛花"],
                                ["生物（有生命，會長大）", "動物", "蝴蝶、蜜蜂、小鳥、魚、龜、麻雀"],
                                ["非生物（沒有生命）", "自然或人造物", "石頭、水、海水、空氣、陽光、單槓、汽車"]
                            ]
                        }
                    ],
                    "experiments": []
                },
                "活動2：植物如何獲取陽光和水": {
                    "points": [
                        "① 植物構造中最明顯的部位是【葉子】，主要功能是【獲取陽光、製造養分】；不同植物的葉子有不同的【葉形】、【葉緣】和【葉脈】等特徵，能幫助分辨不同的植物。",
                        "② 以【桂花葉】為例：【葉形】是葉子的形狀（如橢圓形、愛心形、手掌形、細長形）；【葉緣】是葉子的邊緣（如鋸齒狀、平滑完整）；【葉脈】是葉子上面的脈紋（分網狀脈與平行脈），可以【輸送水分和養分】；連接葉片與莖的部位稱為【葉柄】。",
                        "③ 葉子在莖或枝條上的排列方式稱為【葉序】。葉子是從莖上的【節】長出來的，可分為【互生】、【對生】和【輪生】。",
                        "④ 植物的枝條會相互【交錯】，且【向外、向上】長，盡量讓葉子都能照到更多【陽光】，以製造養分。",
                        "⑤ 植物的【莖】可以【支撐植物的身體】，讓植物往陽光的方向生長；依形態可分成【木本莖】、【草本莖】和【藤本莖】。",
                        "⑥ 大部分植物的【根】都長在土裡，能【抓住土壤、固定植物】，並【吸收土壤裡的水分】；依形態可分為【軸根】和【鬚根】。",
                        "⑦ 植物缺水時，葉子會下垂，將水澆在【土壤】上，由【根部】吸收水分，不久後葉子就會挺起來，恢復生氣。"
                    ],
                    "tables": [
                        {
                            "title": "🍃 葉子的排列方式（葉序）比較表",
                            "columns": ["葉序類型", "在「節」上的生長方式", "代表植物舉例"],
                            "rows": [
                                ["互生", "每個節上只長出 1 片葉子，左右交互排列", "榕樹、樟樹、朱槿"],
                                ["對生", "每個節上相對長出 2 片葉子", "桂花、青楓、福木、南美蟛蜞菊"],
                                ["輪生", "每個節上輪狀長出 3 片或以上的葉子", "黑板樹、軟枝黃蟬"]
                            ]
                        },
                        {
                            "title": "🪵 植物「莖」的三種形態比較表",
                            "columns": ["類型", "外形特徵", "代表植物與表面特徵"],
                            "rows": [
                                ["木本莖", "莖通常較粗壯，可以持續生長且長得較高，能獲得較多陽光", "小葉欖仁（莖表面有皮孔）、木棉（莖表面有瘤刺）、樟樹（莖表面有縱向裂紋，適合做木桌/家具）、榕樹、臺灣欒樹"],
                                ["草本莖", "莖通常較細，能支撐植物直立，會向四面八方長出較多枝條", "長春花（綠色的莖）、大花咸豐草（綠色或略為紫色的莖）、昭和草"],
                                ["藤本莖", "莖通常較柔軟，無法支撐植物直立，需要依靠其他物體來攀爬", "絲瓜（卷鬚纏繞）、牽牛花（纏繞向上）、小花蔓澤蘭"]
                            ]
                        },
                        {
                            "title": "🥕 植物「根」的兩種形態比較表",
                            "columns": ["類型", "外形特徵", "代表植物舉例"],
                            "rows": [
                                ["軸根", "有一條比較粗的主根，旁邊有許多細小的根，可以深入土壤生長", "小白菜、菠菜、莧菜"],
                                ["鬚根", "全部都細細小小的，像鬍鬚一樣，在土壤的淺層生長", "蒜、蔥、稻子"]
                            ]
                        }
                    ],
                    "experiments": [
                        {
                            "title": "🔬 實驗：怎麼知道植物的根會吸水",
                            "purpose": "了解植物的根具有吸收水分的功能。",
                            "steps": [
                                "1. 將植物的根放入裝水的透明容器中，再用【膠泥】封住容器口。在【水位處】做記號，放到通風良好的地方。",
                                "2. 一段時間後，觀察水位變化情形。"
                            ],
                            "result": "比較實驗前、後的水位，發現實驗後容器內的【水位下降了】。",
                            "conclusion": "植物生長所需的水是由【根部】吸收。"
                        }
                    ]
                },
                "活動3：花、果實和種子有什麼功能": {
                    "points": [
                        "① 四季開花的植物：春季【山櫻花、杜鵑】；夏季【金針花、荷花（蓮花）】；秋季【臺灣欒樹】；冬季【梅花】。",
                        "② 一朵花的基本構造包含：【花萼】（最外圍保護花瓣）、【花瓣】、【雄蕊】（頂端有【花粉】）和【雌蕊】（位於中央）。",
                        "③ 有些花有【顏色鮮豔的花瓣】、【花蜜】、【特殊的氣味】等，能吸引蜜蜂、蝴蝶等動物前來採食，幫助植物【雄蕊的花粉傳到雌蕊】，完成【繁衍後代】。",
                        "④ 大部分植物開花後會結出【果實】，果實裡有【種子】；果實可以【保護種子】，還能【幫助傳播種子】（如龍眼、木瓜吸引動物採食傳播；臺灣欒樹像氣球隨風飄揚傳播）。",
                        "⑤ 素養延伸：【小花蔓澤蘭】是【藤本莖】植物，攀爬包覆樹木會使樹木【照不到陽光】無法製造養分而死亡，可【連根拔除】或【切斷它的莖】清除。"
                    ],
                    "tables": [
                        {
                            "title": "🏡 植物與人類生活的關係（食衣住行育樂）",
                            "columns": ["生活層面", "用途說明", "具體舉例"],
                            "rows": [
                                ["食", "有些植物可供人們食用", "稻子（白米飯）、柳橙（果汁）、萵苣、番石榴"],
                                ["衣", "有些植物可供人們製作衣物", "棉花（棉衣）、亞麻"],
                                ["住", "有些房屋和家具是用植物的「莖」做成", "木屋、木桌、木椅、木櫃（如樟樹）"],
                                ["行", "有些交通工具或步道是用植物的「莖」做成", "木棧道、拼板舟、木筏"],
                                ["育／樂", "自然教學場所／美化環境", "植物園、校園／花鐘"]
                            ]
                        }
                    ],
                    "experiments": []
                }
            }
        },
        "單元2：生活中的力": {
            "icon": "🧲",
            "activities": {
                "活動1：力的現象有哪些": {
                    "points": [
                        "① 物體受力後可能有【形狀】或【運動狀態】（變快、變慢或停止）的變化。",
                        "② 受力形狀變化：推【牆壁】形狀不變；壓【膠泥】形狀改變且不再受力後【不會恢復】；拉【橡皮筋】形狀改變且不再受力後【會恢復原狀】。",
                        "③ 【力的三要素】：【力的大小】（用【線段的長短】表示）、【力的方向】（用【箭頭】表示）、【力的作用點】（施力時碰觸到物體的位置）。",
                        "④ 【壓球】受向下的力，形狀可能改變但不會移動位置；【拋球】受向上的力，會往上移動。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "活動2：磁力有什麼特性": {
                    "points": [
                        "① 磁鐵具有【磁力】，只能吸住【鐵製的物品】（如剪刀刀刃、迴紋針、釘書針、長尾夾、鐵尺、鐵粉），不能吸住銅、鋁、十元硬幣、塑膠或木頭。",
                        "② 同一個磁鐵磁力較強的兩端稱為【磁極】，分別是【N極】和【S極】，具有【同極相斥、異極相吸】的特性。",
                        "③ 生活應用：分離鐵粉與沙土（隔著塑膠袋吸鐵粉）、磁吸式鉛筆盒、釣魚遊戲、門擋、磁性螺絲起子、玩具火車車廂連接（異極相吸）。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "活動3：還有什麼不一樣的力": {
                    "points": [
                        "① 常見的力：【彈力】（原子筆彈簧縮回筆心、橡皮筋綁物品）、【風力】（風車轉動、風箏飄揚）、【浮力】（浮板、馬桶水箱浮球、郵輪）。",
                        "② 物體放入水中會受到【向上】的浮力（沉入水底的硬幣也有受浮力，只是浮力不夠）。原本會沉在水中的膠泥，捏成【容器形狀】（船形、碗形、杯子形）就容易浮在水面上。",
                        "③ 【水傳送動力】的應用：流水推動【水車轉動】、【強力水柱】沖走地面髒汙、手壓【水槍】透過水將空罐射倒。"
                    ],
                    "tables": [],
                    "experiments": []
                }
            }
        },
        "單元3：奇妙的空氣": {
            "icon": "💨",
            "activities": {
                "活動1～3：空氣的特性與乾淨空氣": {
                    "points": [
                        "① 空氣無色無味看不見，但無所不在：揮動塑膠袋捏住袋口會【鼓鼓的】，在水中擠壓袋口或將【海綿、粉筆】放入水中會有【氣泡】冒出。",
                        "② 杯底塞紙團倒過來【垂直】壓入水箱再取出，紙團仍是乾的，證明空氣【占有空間】。",
                        "③ 空氣【占有空間且沒有固定形狀】的應用：充氣（游泳圈、籃球、餅乾包裝防碎、氣泡袋防撞）；抽氣（衣物收納袋排出空氣節省空間）。",
                        "④ 【風是空氣流動形成的現象】：空氣流動越快，風越強（國旗/頭髮飄得越高、風車轉得越快）。",
                        "⑤ 堵住裝滿空氣的注射筒口壓下活塞，放手後活塞會【往上移回原來位置】，證明空氣【可以被壓縮】（應用：空氣槍玩具、胡蘿蔔簡易空氣發射器）。",
                        "⑥ 維護空氣品質：多搭乘【大眾運輸工具】、騎【腳踏車】上學、使用【風力發電】、【少砍伐森林、多種樹】（砍伐森林、燃燒稻草垃圾、工廠汽機車排廢氣會造成空汙）。"
                    ],
                    "tables": [
                        {
                            "title": "🚩 空氣品質指標 (AQI) 校園防護措施表",
                            "columns": ["AQI 等級", "因應防護措施"],
                            "rows": [
                                ["良好／普通", "戶外活動可安心"],
                                ["敏感", "戶外活動要小心、敏感體質要留意"],
                                ["不良", "避免劇烈運動，下課多待室內休息"],
                                ["非常不良", "暫停戶外活動，外出須配戴口罩"],
                                ["危害", "全部停止戶外活動，將門窗緊閉"]
                            ]
                        }
                    ],
                    "experiments": []
                }
            }
        },
        "單元4：廚房裡的科學": {
            "icon": "🧪",
            "activities": {
                "活動1～3：辨認材料、增加溶解量與水溶液酸鹼": {
                    "points": [
                        "① 感官辨識材料：用眼睛看外觀、用手摸觸感、用鼻子搓聞氣味（【不可放入口中嘗味道或用力吸聞粉末】）。",
                        "② 【溶解】與器材：讀取量筒水量要【平視水位中央最低處】刻度；用玻璃棒刮平湯匙稱為【1平匙】。利用食鹽能溶於水、沙子不能溶於水的特性，加水過濾可將【食鹽和沙子分離】。",
                        "③ 在固定水量與水溫下，物質溶解的量是【有限的】（50毫升常溫水加到第【4】平匙食鹽出現沉澱，代表最多完全溶解【3】平匙）。",
                        "④ 【提高水溫】與【增加水量】可以增加物質溶解的量；【攪拌】只能加快溶解速度，不能增加溶解量。",
                        "⑤ 【紫色高麗菜汁】酸鹼指示劑：將紫色高麗菜切細絲用【熱水】浸泡過濾製成；滴入【酸性水溶液】（醋、檸檬酸水）變【偏紅色】；滴入【中性水溶液】（食鹽水、砂糖水）維持【紫色】；滴入【鹼性水溶液】（小蘇打水）變【偏藍綠色】。"
                    ],
                    "tables": [
                        {
                            "title": "🧂 廚房五種材料特徵、溶解與酸鹼總表",
                            "columns": ["材料名稱", "外觀顏色", "觸感", "氣味", "能否完全溶於水", "加入紫色高麗菜汁顏色（酸鹼）"],
                            "rows": [
                                ["砂糖", "黃色或白色顆粒", "粗粗的", "有（甜味）", "能", "維持紫色（中性）"],
                                ["食鹽", "白色顆粒", "粗粗的", "無", "能", "維持紫色（中性）"],
                                ["小蘇打粉", "白色粉末", "細細的", "無", "能", "偏藍綠色（鹼性）"],
                                ["檸檬酸", "白色顆粒", "粗粗的", "無", "能", "偏紅色（酸性）"],
                                ["黑胡椒粒", "深棕色顆粒", "粗粗的", "有（辛辣味）", "不能", "無法判別"]
                            ]
                        }
                    ],
                    "experiments": []
                }
            }
        }
    },

    "🌏 社會": {
        "第一單元：我的家庭生活": {
            "icon": "👨‍👩‍👧‍👦",
            "activities": {
                "第1課：我和我的家人": {
                    "points": [
                        "① 每個人的家庭成員不一定相同：組成較簡單的如【父母和子女】；較多的包含【父母、子女、祖父（爺爺或外公）、祖母（奶奶或外婆）及其他親人】；也有人因失去家人而由其他家庭【收養】或住進【育幼院】。",
                        "② 家庭成員的改變原因：會因為【出生】、【死亡】（如奶奶過世使成員減少）、【結婚】、【離婚】等因素，使得人數增加或減少。",
                        "③ 家庭成員改變時，【心情】也會受影響；難過時可向家人或老師訴說，我們要學習接納、愛護家人，珍惜相處時光。",
                        "④ 【姓名】是【我】的代表（自我介紹時會用到）：漢人的姓名分為【姓】（可依據父姓或母姓）和【名字】（由父母或長輩決定，具有家人的【期待與祝福】）。",
                        "⑤ 原住民族的命名方式：(1) 不一定都有【姓】；(2) 有些採用【大自然的事物】命名；(3) 有些在孩子的名字後面加上【父親或母親的名字】表示關係（親子連名制）。我們要珍惜自己的姓名，也要【尊重】別人的姓名。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第2課：家庭的功能": {
                    "points": [
                        "① 家庭的照顧與關懷：家人辛苦照顧養育我們，我們可以主動照顧關懷家人，快樂時分享喜悅，悲傷時彼此安慰鼓勵。",
                        "② 家庭的保護：過馬路遵守交通規則，熟記家人電話及【愛心服務站】位置。當家中有人以暴力傷害我們或家人時，可(1)告訴師長、(2)撥打【110】或【113】保護專線求助。",
                        "③ 家庭的教育：接受父母或長輩指導，兄弟姐妹相互學習培養良好習慣；我們應一起分擔【家事】（如飯後收拾碗筷），共同為家庭付出。",
                        "④ 家庭的收入與消費：收入是家人辛苦【工作】獲得的；應善用收入，將部分作為必要的【消費】（食、衣、住、行、育、樂，如買車票、繳水電費、每日三餐），部分作為【儲蓄】。",
                        "⑤ 【儲蓄】的好處：(1) 供應急需（如生病住院支付醫藥費）；(2) 訂定儲蓄計畫，購買價格較高的物品（如買房子；注意：把錢存入郵局屬於儲蓄而非消費支出）。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第3課：親人的往來＆統整閱讀（孝親楷模）": {
                    "points": [
                        "① 正確稱呼親人是表達【尊重與禮貌】的方式。俗話說「百善【孝】為先」，父母愛護子女，子女要孝敬父母、尊敬長輩。",
                        "② 友愛兄弟姐妹：因為年齡、【個性】、專長不同，應互相禮讓與照顧；發生問題時要【心平氣和】討論解決。",
                        "③ 親人間的相處：平時保持聯絡、結伴出遊；每個家人的生活習慣、態度與【想法】不同，要互相尊重、接納與愛護。",
                        "④ 適當的稱呼與溫暖社會：平時借用對親人的稱呼來稱呼鄰居或長輩，是有禮貌的行為，能拉近彼此關係。把孝敬父母、友愛手足的心意擴大到需要幫助的人（如讓座給懷孕阿姨、幫鄰居奶奶提東西、募集發票捐公益、幫社區爺爺撿掉落物），就是【服務社會】的具體行動。"
                    ],
                    "tables": [
                        {
                            "title": "👪 爸爸與媽媽的親人稱呼總表（必考重點）",
                            "columns": ["親屬分類", "關係說明", "正確稱呼"],
                            "rows": [
                                ["爸爸的親人", "爸爸的爸爸 ／ 爸爸的媽媽", "祖父（爺爺） ／ 祖母（奶奶）"],
                                ["爸爸的親人", "爸爸的哥哥 ／ 伯伯的太太", "伯伯 ／ 伯母"],
                                ["爸爸的親人", "爸爸的弟弟 ／ 叔叔的太太", "叔叔 ／ 嬸嬸"],
                                ["爸爸的親人", "爸爸的姐妹 ／ 姑姑的先生", "姑姑 ／ 姑丈"],
                                ["爸爸的親人", "伯伯、叔叔的子女", "堂兄弟姐妹（如叔叔讀六年級的女兒稱「堂姐」）"],
                                ["爸爸的親人", "姑姑的子女", "表兄弟姐妹"],
                                ["媽媽的親人", "媽媽的爸爸 ／ 媽媽的媽媽", "祖父（外公） ／ 祖母（外婆）"],
                                ["媽媽的親人", "媽媽的兄弟 ／ 舅舅的太太", "舅舅 ／ 舅媽"],
                                ["媽媽的親人", "媽媽的姐妹 ／ 阿姨的先生", "阿姨 ／ 姨丈"],
                                ["媽媽的親人", "舅舅、阿姨的子女", "表兄弟姐妹"]
                            ]
                        }
                    ],
                    "experiments": []
                }
            }
        },
        "第二單元：我的學校生活": {
            "icon": "🏫",
            "activities": {
                "第1課：學習的方法": {
                    "points": [
                        "① 學習是我們的【權利】，也是上學的主要目的；年級升高，學習內容更豐富、範圍更廣（增加社會、自然科學、藝術等課程）。",
                        "② 遵守校園規範：教室內不追逐惡作劇；遊戲區與運動場遵守【安全規定】；電腦教室與自然科學教室遵守設備【使用規則】。",
                        "③ 提升學習效果：培養主動積極的學習【態度】，運用不同學習【方法】——【上課前】先讀課本、不懂做記號、回想經驗；【上課時】分享想法、記錄重點、勇於舉手【發問】；【下課後】複習課堂內容、請教同學、查書籍網路、和家人討論分享。",
                        "④ 尊重他人學習權利：遵守課堂【規範】，維護班級良好【秩序】及學習環境。",
                        "⑤ 妥善安排時間：放學後或假日可依自己的【興趣】、專長，參加【社團活動】、閱讀課外讀物、到圖書館或博物館參觀學習。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第2課：多元的學習＆統整閱讀（夜宿海生館）": {
                    "points": [
                        "① 學校規畫的多元學習活動：社團活動、園遊會、才藝表演、傳統技藝體驗（如原住民族長輩教做花環）、運動會（設計海報、大隊接力、舞蹈表演），能幫助我們充實【知識】、學習不同【技能】、培養【興趣】並與同學合作。",
                        "② 增廣見聞的【戶外教育】：包含參觀博物館、廟宇、到農場體驗等，能豐富學習經驗與樂趣。參與時應展現主動積極態度：(1)提出對參觀地點的想法、(2)事先將物品準備好、(3)【準時】集合。",
                        "③ 統整閱讀：國立海洋生物博物館位於【屏東】，參觀前可先記錄問題或上網查資料，並減少使用塑膠袋以減少海洋垃圾。"
                    ],
                    "tables": [],
                    "experiments": []
                }
            }
        },
        "第三單元：我的班級生活": {
            "icon": "🤝",
            "activities": {
                "第1課：班級裡的人際關係": {
                    "points": [
                        "① 與老師、同學相處：每位同學的外表、【個性】、想法與背景都不同，應培養【尊重】別人的態度，避免只用自己的【想法】和經驗看待對方。",
                        "② 關懷與協助：對身體不舒服的同學可報告老師或陪同去健康中心；對家庭遭逢突發事件的同學適時提供幫助。",
                        "③ 解決爭端：發生爭吵時堅持己見或互相責怪無法解決問題；應先【冷靜】下來、心平氣和坦白說出想法、耐心傾聽對方意見並誠心溝通討論。",
                        "④ 保護自己與他人：受到欺負時可(1)堅定表達想法、(2)大聲【呼救】尋求協助、(3)趕快離開現場、(4)與師長家人討論解決辦法。看到同學被欺負時應給予鼓勵、【關心】或安慰，並尋求【師長】協助，若無法解決可撥打「【1953】反霸凌專線」。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第2課：班級的自治活動＆統整閱讀": {
                    "points": [
                        "① 【班級自治】：由班上同學自己管理班級事務（包含召開班級會議、選舉班級幹部、訂定班級公約），能提升能力、培養服務精神並讓班級運作更有效率。",
                        "② 班級會議分工：【主席】負責主持會議、宣布開會/散會及決定發言順序；【司儀】負責宣布、引導會議流程；【記錄】負責記錄會議內容及表決結果。發言前要先【舉手】，別人發言時要專心【聆聽】。",
                        "③ 【開班會的 6 大流程順序】：(1) 選出主席、司儀、記錄 ➔ (2) 宣布開會 ➔ (3) 報告事項 ➔ (4) 討論事項 ➔ (5) 臨時動議 ➔ (6) 散會。",
                        "④ 【選舉班級幹部的流程】：(1) 提名 ➔ (2) 附議（有人提議或提名時，至少要有一人【附議】表示支持，才需討論表決） ➔ (3) 【舉手表決】（或投票表決） ➔ (4) 宣布當選人。",
                        "⑤ 訂定【班級公約】由全班共同討論表決通過，全班同學（包含幹部與所有同學）都應共同【遵守】。民主生活應有的態度是：【服從多數人的決定，尊重少數人的意見】，互相【配合】、彼此包容。"
                    ],
                    "tables": [],
                    "experiments": []
                }
            }
        },
        "第四單元：和諧的相處": {
            "icon": "⚖️",
            "activities": {
                "第1～3課：合作與競爭、平等與尊重、學校是個小社會": {
                    "points": [
                        "① 分工與合作：校園打掃、牆面彩繪、收拾餐桶等團體事務，需大家不分你我、分工合作，能培養【責任感】。",
                        "② 相互競爭：應使用正確方式維持競爭的【公平性】（如比賽踩線主動向裁判承認犯規）。競爭的缺點是帶來壓力；優點是能表現能力、學習別人優點、增加班級【向心力】。失敗時可思考失敗的【原因】獲得經驗。",
                        "③ 【性別平等】：每個人的能力、興趣、情緒表達與職業選擇（如男生當護理師、女生玩汽車），都不應受到【性別】的限制。",
                        "④ 保護【隱私】與【個人資料】：未經同意不可擅自翻看他人日記、信件或拿取物品；姓名、身分證字號、生日、地址、電話等個人資料不可隨便告訴他人或填寫在網站上。",
                        "⑤ 保護身體的【界線】：身體的隱私部位不可隨意讓他人觀看或觸碰，當隱私或身體受侵犯時應堅定說「【不】」，快跑離開並主動尋求【協助】。",
                        "⑥ 學校是個小社會：全校師生共同遵守的是【校規】，全班遵守的是【班級公約】；發現公物損壞應主動【報告】師長。"
                    ],
                    "tables": [
                        {
                            "title": "🏫 校園中為我們服務的人與團體職責表",
                            "columns": ["服務人員／團體", "主要負責工作說明"],
                            "rows": [
                                ["警衛", "管理學校門禁、巡邏校園，維護安全"],
                                ["輔導室老師", "協助學生解決生活或學習上的困擾"],
                                ["衛生隊／校園服務隊", "由學生組成，處理資源回收、減少垃圾量、維護整潔"],
                                ["健康中心護理師", "協助受傷或不舒服的師生包紮傷口、處理傷病"],
                                ["家長會／志工隊", "由家長組成，協助導護交通、說故事與校務支援"]
                            ]
                        }
                    ],
                    "experiments": []
                }
            }
        }
    },

    "🔢 數學": {
        "第1～3單元：數到10000、四位數加減、乘法": {
            "icon": "🧮",
            "activities": {
                "第1單元：數到10000": {
                    "points": [
                        "① 位值觀念：10 個百等於 1 個【千】（1000），10 個千等於 1 個【萬】（10000）。",
                        "② 定位板與換算：從右到左依序為【個位、十位、百位、千位】，缺位要補【0】（例如：6個千、3個十和4個一合起來是【6034】；18張百元鈔票可換成【1】張千元和【8】張百元，共【1800】元）。",
                        "③ 四位數比大小：先比位數，同位數由最高位（千位）往右比。用不重複數字組成四位數時，最高位不可為 0（例如用 2、3、5、0 組成最大四位數為【5320】，最小四位數為【2035】）。",
                        "④ 認識數線：數線上越往右邊的數越【大】，越往左邊的數越【小】；往右移是【加（＋）】，往左移是【減（－）】。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第2單元：四位數的加減": {
                    "points": [
                        "① 直式加法（進位）：將個位、十位、百位、千位對齊，由個位開始相加，滿 10 就向高一位【進 1】。",
                        "② 直式減法（連續退位）：各位數對齊由個位開始減，不夠減時向高一位【借 1 換成 10】再減（例如：4005 － 3892 ＝ 【113】、3000 － 1357 ＝ 【1643】）。",
                        "③ 加減估算：把數字看成最接近的【幾千】來估算（例如：4890 ＋ 980 約為 5000 ＋ 1000 ＝ 6000，最少要拿出【6】張千元鈔票才夠）。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第3單元：乘法": {
                    "points": [
                        "① 整十、整百乘以一位數：先算非 0 數字相乘，再於末尾補上對應個數的 0（例如：6 × 9 ＝ 54 ➔ 60 × 9 ＝ 【540】 ➔ 600 × 9 ＝ 【5400】）。",
                        "② 二、三位數乘以一位數直式：用乘數依序乘以被乘數的【個位、十位、百位】，有進位時記在高一位再相加（例如：375 × 8 ＝ 【3000】、625 × 8 ＝ 【5000】）。",
                        "③ 乘法估算：先找出被乘數最接近幾百再相乘（例如：一隻公仔賣 399 元，買 9 隻約花 400 × 9 ＝ 【3600】元）。"
                    ],
                    "tables": [],
                    "experiments": []
                }
            }
        },
        "第4～6單元：幾毫米、角與四邊形、除法": {
            "icon": "📐",
            "activities": {
                "第4單元：幾毫米 (mm)": {
                    "points": [
                        "① 長度單位換算：尺上 1 小格是 1【毫米 (mm)】；1 公分 (cm) ＝【10】毫米 (mm)；1 公尺 (m) ＝ 100 公分 ＝【1000】毫米 (mm)。",
                        "② 複名數換算與計算：例如 1 公尺 47 公分（1 m 47 cm）＝【1470】毫米 (mm)；5006 毫米 (mm) ＝【5】公尺 (m)【6】毫米 (mm)。做公分與毫米加減時，滿 10 毫米進為 1 公分，不夠減時借 1 公分換成 10 毫米。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第5單元：角、正方形和長方形": {
                    "points": [
                        "① 認識角：角是由 1 個【頂點】和 2 個直線的【邊】所組成的。角的大小與兩邊張開的程度有關，【把角的 2 個邊長縮短或延長，角的大小不會改變】。",
                        "② 直角、銳角與鈍角：像三角板中最大的角、正方形與長方形的角稱為【直角】；比直角小的角叫作【銳角】；比直角大的角叫作【鈍角】。",
                        "③ 【正方形】的性質：4 個邊【一樣長】，4 個角都是【直角】；【長方形】的性質：上下 2 個邊一樣長、左右 2 個邊一樣長，4 個角都是【直角】。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第6單元：除法": {
                    "points": [
                        "① 除法與餘數：【被除數 ÷ 除數 ＝ 商 … 餘數】，利用九九乘法表求商，且【餘數一定要小於除數】（餘數為 0 代表【整除】）。",
                        "② 0 和 1 的除法規律：任何數除以自己等於 1（如 8 ÷ 8 ＝ 【1】）；0 除以任何數等於 0（如 0 ÷ 7 ＝ 【0】）；任何數除以 1 等於自己（如 9 ÷ 1 ＝ 【9】）。",
                        "③ 【偶數（雙數）】與【奇數（單數）】：能被 2 整除（個位數為 0、2、4、6、8）的數稱為【偶數】；不能被 2 整除（個位數為 1、3、5、7、9）的數稱為【奇數】。"
                    ],
                    "tables": [],
                    "experiments": []
                }
            }
        },
        "第7～9單元：公升和毫升、分數、圓": {
            "icon": "⭕",
            "activities": {
                "第7單元：公升 (L) 和毫升 (mL)": {
                    "points": [
                        "① 容量單位換算：1 公升 (L) ＝【1000】毫升 (mL)。1 瓶養樂多（乳酸飲料）約【100 毫升】，家庭號鮮奶約 2【公升】。",
                        "② 複名數換算與計算：例如 5 公升 8 毫升 ＝【5008】毫升；2 公升 － 670 毫升 ＝ 2000 － 670 ＝【1330】毫升。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第8單元：分數": {
                    "points": [
                        "① 分母與分子：分數橫線下的數叫作【分母】（表示被平分成幾份），橫線上的數叫作【分子】（表示其中的幾份），例如「十分之七」記作【7/10】。",
                        "② 單位分數的累加：只有【分子】會增加，分母保持不變；當分子和分母一樣大時，值等於【1】（例如：1 ＝ 6/6 ＝ 8/8 ＝ 10/10 ＝ 12/12）。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第9單元：圓": {
                    "points": [
                        "① 圓的各部位名稱：圓中心固定的點叫作【圓心】（一個圓只有 1 個圓心）；圓的周界叫作【圓周】；從圓心到圓周的距離叫作【半徑】；通過圓心且兩端在圓周上的線段叫作【直徑】（一個圓有無限多條直徑與半徑，且直徑是圓周上任兩點最長的距離）。",
                        "② 半徑與直徑的關係：同一個圓中，【直徑 ＝ 半徑 × 2】，半徑是直徑的一半。使用圓規畫圓時，【圓規兩端張開的距離就是圓的「半徑」】（要畫直徑 6 公分的圓，圓規兩端要拉開到【3】公分）。"
                    ],
                    "tables": [],
                    "experiments": []
                }
            }
        }
    },

    "📘 國語": {
        "第1～4課：時間是什麼、妙用便利貼、提早五分鐘、水滾了": {
            "icon": "📗",
            "activities": {
                "第1課：時間是什麼 ＆ 第2課：妙用便利貼": {
                    "points": [
                        "① 第1課《時間是什麼》多音字：【待】ㄉㄞˋ（等待、優待）／ㄉㄞ（待在家裡）；【彈】ㄊㄢˊ（彈琴、彈簧、彈性）／ㄉㄢˋ（彈珠）；【樂】ㄌㄜˋ（快樂、樂於助人）／ㄩㄝˋ（音樂）。",
                        "② 第1課字形與成語：忙【碌】、【綠】色植物、記【錄】；狼【吞】虎嚥、燙到【舌】頭；成語：【迫不及待】、【忍氣吞聲】、【疲於奔命】、【對牛彈琴】、【長話短說】、【一勞永逸】。",
                        "③ 第2課《妙用便利貼》多音字與字形：【便】ㄅㄧㄢˋ（方便、便利貼、便條紙）／ㄆㄧㄢˊ（便宜）；【使】用、方【便】、成【效】、【郊】外踏青。",
                        "④ 第2課成語與句型：【循序漸進】、【輕而易舉】、【得過且過】、【排山倒海】、【冰天雪地】、【原封不動】；目的複句「【為了】…」、遞進複句「不但…，【而且】…」。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第3課：提早五分鐘 ＆ 第4課：水滾了": {
                    "points": [
                        "① 第3課《提早五分鐘》多音字：【覺】ㄐㄧㄠˋ（睡覺）／ㄐㄩㄝˊ（發覺、視覺）；【得】ㄉㄟˇ（你得回家了）／ㄉㄜˊ（獲得、得獎、得意）／ㄉㄜ（跑得快）；【角】ㄐㄧㄠˇ（直角、三角板）／ㄐㄩㄝˊ（主角、角色）。",
                        "② 第3課成語與修辭：【天涯海角】、【安居樂業】、【本末倒置】、【每況愈下】、【文質彬彬】、【心安理得】；連續三個結構相似的句子為【排比】修辭。",
                        "③ 第4課《水滾了》多音字與核心成語：【倒】ㄉㄠˋ（倒退、倒垃圾、倒茶水）／ㄉㄠˇ（跌倒、摔倒、倒閉）。徒弟康康用【揚湯止沸】（比喻沒有解決問題的根本），師傅教他用【釜底抽薪】（抽去鍋底下的柴火，比喻從根本解決問題）。",
                        "④ 第4課字形與成語：師【徒】、遷【徙】、【徒】手；成語：【打退堂鼓】（中途退縮放棄）、【骨瘦如柴】、【揚眉吐氣】、【適可而止】、【一知半解】。"
                    ],
                    "tables": [],
                    "experiments": []
                }
            }
        },
        "第5～8課：為梨花撐傘、小鉛筆大學問、風的味道、寄居蟹找新家": {
            "icon": "📙",
            "activities": {
                "第5課：為梨花撐傘 ＆ 第6課：小鉛筆大學問": {
                    "points": [
                        "① 第5課《為梨花撐傘》多音字與字形：【將】ㄐㄧㄤ（上將梨、將領）／ㄐㄧㄤˋ（將近、把）；【結】ㄐㄧㄝˊ（結果、結婚、蝴蝶結）／ㄐㄧㄝ（結實）；【當】ㄉㄤ（當作、當時）／ㄉㄤˋ（適當、上當）；【種】ㄓㄨㄥˇ（種類、種子）／ㄓㄨㄥˋ（種田）。字形：【綿】羊、【棉】被、【棉】花；燦【爛】、布告【欄】、【蘭】花、阻【攔】。",
                        "② 第5課成語：【甜言蜜語】、【海枯石爛】、【張口結舌】、【如膠似漆】、【一手遮天】。",
                        "③ 第6課《小鉛筆大學問》多音字與課文：【分】ㄈㄣ（部分）／ㄈㄣˋ（身分、分數）；【切】ㄑㄧㄝ（切斷、切菜）／ㄑㄧㄝˋ（咬牙切齒、密切、切記）；【削】ㄒㄧㄠ（削鉛筆、削皮）／ㄒㄩㄝˋ（削減、削足適履）。鉛筆芯的原料是【石墨】和【黏土】。",
                        "④ 第6課成語與修辭：【墨守成規】、【按部就班】、【隨機應變】、【大器晚成】、【出神入化】、【另眼相看】。「小鉛筆大學問」、「小兵立大功」運用了【映襯】修辭。"
                    ],
                    "tables": [],
                    "experiments": []
                },
                "第7課：風的味道 ＆ 第8課：寄居蟹找新家": {
                    "points": [
                        "① 第7課《風的味道》字形與修辭：山【坡】、【波】浪、【披】風、棉【被】；原【野】、【舒】服、【抒】發壓力。「風到處流浪」將風擬人化，屬於【轉化】修辭。",
                        "② 第7課成語：【乘風破浪】、【野心勃勃】、【發人深省】、【欣欣向榮】（草木蓬勃發展）、【賞心悅目】。",
                        "③ 第8課《寄居蟹找新家》多音字與字形：【漂】ㄆㄧㄠ（漂流、漂浮）／ㄆㄧㄠˋ（漂亮）；【應】ㄧㄥ（應該、應當）／ㄧㄥˋ（應對、答應）。字形：導【護】、收【穫】、獲【得】；安【靜】、乾【淨】、淨【灘】；羨【慕】、螢【幕】。",
                        "④ 第8課成語：【寄人籬下】、【金蟬脫殼】（藉計謀脫身）、【物以類聚】、【守口如瓶】（嚴守祕密）、【志同道合】。"
                    ],
                    "tables": [],
                    "experiments": []
                }
            }
        },
        "第9～12課＆語文統整：阿塱壹古道、秋千上的婚禮、一路平安、不一樣的團圓": {
            "icon": "📕",
            "activities": {
                "第9～12課核心字詞、多音字與成語": {
                    "points": [
                        "① 第9課《阿塱壹古道》：【挑】ㄊㄧㄠˇ（挑戰、挑弄）／ㄊㄧㄠ（挑選、挑水）；【著】ㄓㄨˋ（名著）／ㄓㄜ˙（記著）／ㄓㄠˊ（睡著、著涼）／ㄓㄨㄛˊ（穿著、著陸）；【曲】ㄑㄩˇ（歌曲）／ㄑㄩ（彎曲）。字形：【沿】途、一【般】、暈【船】、【採】蘋果、【踩】落葉；成語：【交頭接耳】、【如影隨形】、【投其所好】、【夜以繼日】（形容工作勤奮日夜不停）、【隔岸觀火】。",
                        "② 第10課《秋千上的婚禮》：【藏】ㄘㄤˊ（躲藏、收藏、藏新娘）／ㄗㄤˋ（寶藏、藏青色）；【參】ㄘㄢ（參加）／ㄕㄣ（人參）／ㄘㄣ（參差不齊）。魯凱族與排灣族婚禮習俗包含盪秋千、連杯酒、藏新娘。成語：【江郎才盡】、【不同凡響】、【有福同享】、【價值連城】、【相敬如賓】、【笑裡藏刀】。",
                        "③ 第11課《一路平安》：字形與字音——【休】【息】（ㄒㄧㄡ ㄒㄧˊ）、健康【檢】【查】（ㄐㄧㄢˇ ㄔㄚˊ）、工【廠】、汽車【零件】。成語：【斬草除根】、【烏煙瘴氣】、【息息相關】、【不修邊幅】（不注意衣飾儀容整潔）、【幸災樂禍】、【七零八落】。",
                        "④ 第12課《不一樣的團圓》：字形——【剩】餘、【鏡】片、【默】契；【握】手、【壓】力、【厭】煩；收【訊】、【迅】速。成語：【一團和氣】、【潛移默化】、【人去樓空】、【一視同仁】（平等對待毫無歧視）、【破鏡重圓】。"
                    ],
                    "tables": [
                        {
                            "title": "📑 國語 3 上當冊八大複句句型總表",
                            "columns": ["複句名稱", "常用關聯詞", "課文與生活例句"],
                            "rows": [
                                ["承接複句", "…讓… ／ 當… ／ 先…再…", "當風一吹過，風鈴清亮的聲音像是在招呼遠方的客人。"],
                                ["並列複句", "一會兒…一會兒… ／ 一邊…一邊… ／ 有人…有人…", "阿塱壹古道一邊是山，一邊是海，處處有意想不到的風景。"],
                                ["條件複句", "只要…就… ／ …才… ／ …才能…", "把車子修好，你們才能回家啊！"],
                                ["目的複句", "為了…", "為了能準時出門，小真寫了一張便利貼貼在書桌前面。"],
                                ["因果複句", "因為…所以… ／ 因為…只能…", "因為找不到殼，寄居蟹只能住在瓶蓋裡。"],
                                ["遞進複句", "…而且… ／ …不只…還… ／ 除了…還…", "使用便利貼，不只懂得安排時間，還可以用來提醒家人。"],
                                ["轉折複句", "…可是… ／ …竟然… ／ …沒想到…", "我躲得這麼隱密，妹妹竟然找得到我。"],
                                ["假設複句", "如果…", "如果你直接拿筆芯來書寫，不但容易斷裂，手還會變黑。"]
                            ]
                        }
                    ],
                    "experiments": []
                }
            }
        }
    }
}


# ==========================================
# 6. 四大科目內建全題型精選題庫（填空題皆為純輸入框）
# ==========================================
DEFAULT_QUIZ_BANK = [
    # ==================== 🌿 自然 ====================
    {
        "subject": "🌿 自然", "unit": "單元1：多采多姿的植物", "type": "連連看",
        "q": "【自然・連連看】下列植物的葉子在莖或枝條上的生長情形（葉序）分別是哪一種？請配對連線：",
        "left_items": ["(1) 朱槿", "(2) 軟枝黃蟬", "(3) 福木", "(4) 黑板樹", "(5) 南美蟛蜞菊", "(6) 樟樹"],
        "right_items": ["對生", "互生", "輪生"],
        "ans": {"(1) 朱槿": "互生", "(2) 軟枝黃蟬": "輪生", "(3) 福木": "對生", "(4) 黑板樹": "輪生", "(5) 南美蟛蜞菊": "對生", "(6) 樟樹": "互生"},
        "exp": "朱槿、樟樹為互生；福木、南美蟛蜞菊為對生；軟枝黃蟬、黑板樹為輪生。"
    },
    {
        "subject": "🌿 自然", "unit": "單元2：生活中的力", "type": "連連看",
        "q": "【自然・連連看】生活中有許多不同種類的力，下列情境分別是利用什麼力？請配對連線：",
        "left_items": ["(1) 輪船在海上航行", "(2) 風箏在空中飛", "(3) 原子筆筆心縮回", "(4) 跳棋吸在鐵製棋盤上"],
        "right_items": ["甲. 磁力", "乙. 水的浮力", "丙. 風力", "丁. 彈力"],
        "ans": {"(1) 輪船在海上航行": "乙. 水的浮力", "(2) 風箏在空中飛": "丙. 風力", "(3) 原子筆筆心縮回": "丁. 彈力", "(4) 跳棋吸在鐵製棋盤上": "甲. 磁力"},
        "exp": "輪船利用水的浮力；風箏利用風力；原子筆彈簧利用彈力；磁吸式跳棋利用磁力。"
    },
    {
        "subject": "🌿 自然", "unit": "單元1：多采多姿的植物", "type": "填充與代號題",
        "q": "【自然・填空題】植物果實傳播與力的三要素填空（請直接輸入答案或代號）：\n提示代號：甲.隨風飄揚傳播 ／ 乙.吸引動物採食傳播",
        "blanks": [
            {"label": "1.(1) 果實除了能吸引動物採食、幫助傳播種子外，還有什麼功能？", "ans": "保護種子"},
            {"label": "1.(2) 【龍葵】的果實傳播方式代號（填 甲 或 乙）：", "ans": "乙"},
            {"label": "1.(3) 【臺灣欒樹】的果實傳播方式代號（填 甲 或 乙）：", "ans": "甲"},
            {"label": "1.(4) 【蘋果】的果實傳播方式代號（填 甲 或 乙）：", "ans": "乙"},
            {"label": "2.(1) 踢球圖示中，甲（腳接觸球的位置）代表力的什麼要素？", "ans": "力的作用點"},
            {"label": "2.(2) 踢球圖示中，乙（箭頭所指處）代表力的什麼要素？", "ans": "力的方向"},
            {"label": "2.(3) 踢球圖示中，丙（線段總長度）代表力的什麼要素？", "ans": "力的大小"}
        ],
        "exp": "果實能保護種子；龍葵與蘋果吸引動物採食(乙)，臺灣欒樹隨風飄揚(甲)；腳接觸球是力的作用點，箭頭是力的方向，線段長短是力的大小。"
    },
    {
        "subject": "🌿 自然", "unit": "單元4：廚房裡的科學", "type": "選擇題",
        "q": "下列哪一種方法可以較準確且安全的分辨「食鹽」和「檸檬酸」？",
        "options": ["①嘗味道", "②觀察顏色", "③測試能不能完全溶於水", "④製作成水溶液後，加入紫色高麗菜汁觀察顏色變化"],
        "ans": "④製作成水溶液後，加入紫色高麗菜汁觀察顏色變化",
        "exp": "食鹽水加入紫色高麗菜汁維持紫色（中性），檸檬酸水則變成偏紅色（酸性）。"
    },

    # ==================== 🌏 社會 ====================
    {
        "subject": "🌏 社會", "unit": "第一單元：我的家庭生活", "type": "填充與代號題",
        "q": "【社會・重點填空】家庭成員、保護專線與親屬稱謂填空：",
        "blanks": [
            {"label": "(1) 家庭成員的組成會因為【 ＿＿ 】、死亡、結婚、離婚等因素而增加或減少：", "ans": "出生"},
            {"label": "(2) 當家中有人以暴力傷害我們或家人時，可撥打 110 或【 ＿＿ 】保護專線：", "ans": "113"},
            {"label": "(3) 爸爸的哥哥，我們要稱呼為【 ＿＿ 】：", "ans": "伯伯"},
            {"label": "(4) 媽媽的兄弟，我們要稱呼為【 ＿＿ 】：", "ans": "舅舅"},
            {"label": "(5) 叔叔的女兒年紀比我大，我應該稱呼她為【 ＿＿ 】：", "ans": "堂姐"},
            {"label": "(6) 俗話說：「百善【 ＿＿ 】為先」，孝敬父母與尊敬長輩是社會的美德：", "ans": "孝"}
        ],
        "exp": "家庭成員因出生、死亡、結婚、離婚而改變；家暴保護專線為110或113；爸爸的哥哥稱伯伯、媽媽的兄弟稱舅舅、叔叔的女兒稱堂姐或堂妹。"
    },
    {
        "subject": "🌏 社會", "unit": "第三單元：我的班級生活", "type": "填充與代號題",
        "q": "【社會・排序與填空】開班會的流程排序（請在各小題輸入順序數字 1 ～ 6）與反霸凌專線：",
        "blanks": [
            {"label": "(1) 討論事項（填入順序 1~6）：", "ans": "4"},
            {"label": "(2) 臨時動議（填入順序 1~6）：", "ans": "5"},
            {"label": "(3) 報告事項（填入順序 1~6）：", "ans": "3"},
            {"label": "(4) 散會（填入順序 1~6）：", "ans": "6"},
            {"label": "(5) 宣布開會（填入順序 1~6）：", "ans": "2"},
            {"label": "(6) 選出主席、司儀、記錄（填入順序 1~6）：", "ans": "1"},
            {"label": "(7) 在學校受到欺負無法解決時，可撥打「【 ＿＿ 】反霸凌專線」尋求幫助：", "ans": "1953"}
        ],
        "exp": "開班會流程：1.選出主席、司儀、記錄 ➔ 2.宣布開會 ➔ 3.報告事項 ➔ 4.討論事項 ➔ 5.臨時動議 ➔ 6.散會。反霸凌專線為 1953。"
    },
    {
        "subject": "🌏 社會", "unit": "第四單元：和諧的相處", "type": "連連看",
        "q": "【社會・配合連線題】下列是校園中為我們服務的人或團體，請配對連出他們負責的工作：",
        "left_items": ["(1) 管理學校門禁、巡邏校園，維護安全", "(2) 協助學生解決生活或學習上的困擾", "(3) 處理資源回收，減少垃圾量", "(4) 協助受傷的師生包紮傷口"],
        "right_items": ["甲. 衛生隊", "乙. 輔導室老師", "丙. 警衛", "丁. 健康中心護理師"],
        "ans": {
            "(1) 管理學校門禁、巡邏校園，維護安全": "丙. 警衛",
            "(2) 協助學生解決生活或學習上的困擾": "乙. 輔導室老師",
            "(3) 處理資源回收，減少垃圾量": "甲. 衛生隊",
            "(4) 協助受傷的師生包紮傷口": "丁. 健康中心護理師"
        },
        "exp": "警衛負責門禁巡邏(丙)；輔導室老師協助解決生活學習困擾(乙)；衛生隊處理資源回收(甲)；護理師協助包紮傷口(丁)。"
    },
    {
        "subject": "🌏 社會", "unit": "第一單元：我的家庭生活", "type": "勾選題",
        "q": "【社會・勾選題】家庭的消費支出包含下列哪些？請在正確的項目打 √：",
        "options": ["(1) 購買車票", "(2) 繳交水電費", "(3) 把錢存入郵局", "(4) 每日三餐"],
        "ans": ["(1) 購買車票", "(2) 繳交水電費", "(4) 每日三餐"],
        "exp": "買車票(行)、繳水電費(住)、每日三餐(食)屬於消費支出；把錢存入郵局屬於「儲蓄」。"
    },
    {
        "subject": "🌏 社會", "unit": "第三單元：我的班級生活", "type": "選擇題",
        "q": "臨時動議或選舉班級幹部時，當有人提出要討論的事情或提名人選，這時需要其他人做什麼表示支持，才可以繼續討論？",
        "options": ["①附議", "②記錄", "③表決", "④散會"],
        "ans": "①附議",
        "exp": "有人提議或提名時，至少要有一人「附議」表示支持，才有進行討論或表決的必要。"
    },

    # ==================== 🔢 數學 ====================
    {
        "subject": "🔢 數學", "unit": "第2單元：四位數的加減", "type": "連連看",
        "q": "【數學・進階挑戰連連看】把答案一樣的算式連起來：",
        "left_items": ["① 5613 + 1999 (=7612)", "② 3015 + 4327 (=7342)", "③ 691 + 4021 (=4712)", "④ 6103 - 498 (=5605)", "⑤ 2214 - 1190 (=1024)"],
        "right_items": ["ㄅ. 4950 - 238", "ㄆ. 4000 + 3612", "ㄇ. 2486 + 4856", "ㄈ. 3157 - 2133", "ㄉ. 6628 - 1023"],
        "ans": {
            "① 5613 + 1999 (=7612)": "ㄆ. 4000 + 3612",
            "② 3015 + 4327 (=7342)": "ㄇ. 2486 + 4856",
            "③ 691 + 4021 (=4712)": "ㄅ. 4950 - 238",
            "④ 6103 - 498 (=5605)": "ㄉ. 6628 - 1023",
            "⑤ 2214 - 1190 (=1024)": "ㄈ. 3157 - 2133"
        },
        "exp": "① 7612=ㄆ；② 7342=ㄇ；③ 4712=ㄅ；④ 5605=ㄉ；⑤ 1024=ㄈ。"
    },
    {
        "subject": "🔢 數學", "unit": "第4單元：幾毫米", "type": "連連看",
        "q": "【數學・單位換算連連看】1公分(cm) = 10毫米(mm)，請把相等的長度連起來：",
        "left_items": ["① 5 公分", "② 10 公分 9 毫米", "③ 800 毫米", "④ 7 公分 1 毫米", "⑤ 10 公分"],
        "right_items": ["ㄅ. 80 公分", "ㄆ. 50 毫米", "ㄇ. 109 毫米", "ㄈ. 100 毫米", "ㄉ. 71 毫米"],
        "ans": {
            "① 5 公分": "ㄆ. 50 毫米",
            "② 10 公分 9 毫米": "ㄇ. 109 毫米",
            "③ 800 毫米": "ㄅ. 80 公分",
            "④ 7 公分 1 毫米": "ㄉ. 71 毫米",
            "⑤ 10 公分": "ㄈ. 100 毫米"
        },
        "exp": "5公分=50毫米(ㄆ)；10公分9毫米=109毫米(ㄇ)；800毫米=80公分(ㄅ)；7公分1毫米=71毫米(ㄉ)；10公分=100毫米(ㄈ)。"
    },
    {
        "subject": "🔢 數學", "unit": "第1～3單元：數到10000、四位數加減、乘法", "type": "填充與代號題",
        "q": "【數學・計算與填空】請直接算出答案並填入空格中：",
        "blanks": [
            {"label": "(1) 6個千、3個十和4個一合起來是多少？", "ans": "6034"},
            {"label": "(2) 用 2、3、5、0 組成一個四位數（數字不重複），最大的數是多少？", "ans": "5320"},
            {"label": "(3) 用 2、3、5、0 組成一個四位數（數字不重複），最小的數是多少？", "ans": "2035"},
            {"label": "(4) 4005 － 3892 ＝ ？", "ans": "113"},
            {"label": "(5) 3000 － 1357 ＝ ？", "ans": "1643"},
            {"label": "(6) 375 × 8 ＝ ？", "ans": "3000"},
            {"label": "(7) 704 × 7 ＝ ？", "ans": "4928"}
        ],
        "exp": "(1) 6034；(2) 5320；(3) 2035；(4) 113；(5) 1643；(6) 3000；(7) 4928。"
    },
    {
        "subject": "🔢 數學", "unit": "第4～9單元：毫米、角、除法、公升毫升、分數、圓", "type": "填充與代號題",
        "q": "【數學・綜合應用填空】請直接填入正確數字或名稱：",
        "blanks": [
            {"label": "(1) 3 公尺 20 公分 ＝ 多少毫米(mm)？", "ans": "3200"},
            {"label": "(2) 把 ∠ㄆ 的 2 個邊長縮短，其他不變，還是會跟原來一樣大嗎？（填 會 或 不會）", "ans": "會"},
            {"label": "(3) 把 38 個草莓，每 8 個分成 1 盤，最多可分成幾盤？（只填盤數）", "ans": "4"},
            {"label": "(4) 承上題，還剩下幾個草莓？", "ans": "6"},
            {"label": "(5) 5 公升 8 毫升 ＝ 多少毫升(mL)？", "ans": "5008"},
            {"label": "(6) 1 盒果凍有 15 個，14/15 盒果凍有幾個？", "ans": "14"},
            {"label": "(7) 要畫一個「直徑 6 公分」的圓，要將圓規兩端拉開到幾公分？", "ans": "3"}
        ],
        "exp": "(1) 3200毫米；(2) 邊長縮短角的大小不變(會)；(3)(4) 38 ÷ 8 ＝ 4 … 6；(5) 5008毫升；(6) 14個；(7) 圓規張開距離是半徑，6 ÷ 2 ＝ 3公分。"
    },

    # ==================== 📘 國語 ====================
    {
        "subject": "📘 國語", "unit": "第1～4課：時間是什麼、妙用便利貼、提早五分鐘、水滾了", "type": "填充與代號題",
        "q": "【國語・字形與成語填空】請根據句意，在空格中填入正確的國字或成語：\n提示字詞：綠、碌、錄、吞、舌、使、便、效、郊、打退堂鼓、一知半解",
        "blanks": [
            {"label": "1. 校園巡禮時，學生拿著筆記本尋找老師指定的【 ＿＿ 】色植物（填1字）：", "ans": "綠"},
            {"label": "2. 承上句，學生忙【 ＿＿ 】的記【 ＿＿ 】著（依序填入2個字，如：碌錄）：", "ans": "碌錄"},
            {"label": "3. 吃東西不要狼【 ＿＿ 】虎嚥，尤其是吃熱食時容易燙到【 ＿＿ 】頭（依序填2字）：", "ans": "吞舌"},
            {"label": "4. 這臺咖啡機操作很簡單，爺爺【 ＿＿ 】用起來覺得很方【 ＿＿ 】（依序填2字）：", "ans": "使便"},
            {"label": "5. 看到這本課外書這麼厚，小明還沒開始讀就想【 ＿＿ 】了（填四字成語）：", "ans": "打退堂鼓"},
            {"label": "6. 這本科學雜誌太難了，我讀完之後還是【 ＿＿ 】，不太明白原理（填四字成語）：", "ans": "一知半解"}
        ],
        "exp": "1~2. 綠色、忙碌、記錄；3. 狼吞虎嚥、舌頭；4. 使用、方便；5. 打退堂鼓（中途退縮）；6. 一知半解。"
    },
    {
        "subject": "📘 國語", "unit": "第5～12課：為梨花撐傘～不一樣的團圓", "type": "填充與代號題",
        "q": "【國語・選字與成語填空】請根據句意，直接填入正確的國字或成語：\n提示字詞：護、穫、靜、淨、沿、般、船、採、踩、發人深省、乘風破浪、欣欣向榮、賞心悅目、不同凡響、有福同享、相敬如賓",
        "blanks": [
            {"label": "1. 媽媽參加導【 ＿＿ 】工作後，認識很多義工朋友，收【 ＿＿ 】很多（依序填2字）：", "ans": "護穫"},
            {"label": "2. 圖書館裡不但安【 ＿＿ 】，環境也被維護得乾【 ＿＿ 】又舒適（依序填2字）：", "ans": "靜淨"},
            {"label": "3. 我在果園裡【 ＿＿ 】蘋果時，不小心【 ＿＿ 】到一片落葉（依序填2字）：", "ans": "採踩"},
            {"label": "4. 今天的這場演講【 ＿＿ 】，讓聽眾受益良多（填四字成語）：", "ans": "發人深省"},
            {"label": "5. 春天來了，草木長出新芽，大地呈現【 ＿＿ 】的景象（填四字成語）：", "ans": "欣欣向榮"},
            {"label": "6. 爸爸和媽媽【 ＿＿ 】，從來不對彼此發脾氣（填四字成語）：", "ans": "相敬如賓"}
        ],
        "exp": "1. 導護、收穫；2. 安靜、乾淨；3. 採蘋果、踩落葉；4. 發人深省；5. 欣欣向榮；6. 相敬如賓。"
    },
    {
        "subject": "📘 國語", "unit": "第1～4課：時間是什麼、妙用便利貼、提早五分鐘、水滾了", "type": "選擇題",
        "q": "下面哪一種狀況是用「釜底抽薪」（從根本解決問題）的方式解決問題？",
        "options": [
            "①地板髒了只用墊子遮住，卻不把垃圾掃乾淨",
            "②為了上課不分心，他把鉛筆盒裡的玩具全部收起來",
            "③鬧鐘響了只塞住耳朵，卻不肯下床"
        ],
        "ans": "②為了上課不分心，他把鉛筆盒裡的玩具全部收起來",
        "exp": "「釜底抽薪」比喻從根本解決問題；①與③都只是「揚湯止沸」治標不治本。"
    },
    {
        "subject": "📘 國語", "unit": "第5～8課：為梨花撐傘、小鉛筆大學問、風的味道、寄居蟹找新家", "type": "選擇題",
        "q": "下列哪個「削」字讀音和其他的不同？",
        "options": ["①「削」鉛筆", "②「削」皮", "③「削」減"],
        "ans": "③「削」減",
        "exp": "削鉛筆、削皮讀作「ㄒㄧㄠ」；削減、削足適履讀作「ㄒㄩㄝˋ」。"
    },
    {
        "subject": "📘 國語", "unit": "第9～12課＆語文統整：阿塱壹古道、秋千上的婚禮、一路平安、不一樣的團圓", "type": "選擇題",
        "q": "下列哪個「藏」字讀音和其他的不同？",
        "options": ["①躲「藏」", "②「藏」書", "③「藏」青色"],
        "ans": "③「藏」青色",
        "exp": "躲藏、藏書讀作「ㄘㄤˊ」；藏青色、寶藏讀作「ㄗㄤˋ」。"
    }
]


# ==========================================
# 7. 四科考卷 PDF「做一做／連連看／填空／素養題」解析引擎（填空一律保持純輸入框）
# ==========================================
def get_page_specific_exercises(page_text: str, unit_label: str, source_name: str, page_num: int, subject: str) -> list:
    """
    針對已上傳考卷（自然、社會、數學、國語）的特殊圖表題與連連看進行精準題組建立。
    所有填空題均不設 options 下拉選單，100% 維持純填空輸入！
    """
    extra_qs = []

    # --- [自然 01_第一次月考A卷 第2頁] ---
    if ("龍葵" in page_text and "軟枝黃蟬" in page_text) or ("01_國小自然3上第一次月考A卷" in source_name and page_num == 2):
        extra_qs.extend([
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "填充與代號題",
                "q": "【3 做一做】第 1 題：植物果實的功能與傳播方式填空（代號：甲.隨風飄揚傳播 ／ 乙.吸引動物採食傳播）",
                "blanks": [
                    {"label": "(1) 果實除了能吸引動物採食，幫助傳播種子之外，還有什麼功能呢？", "ans": "保護種子"},
                    {"label": "(2)-1 【龍葵】的果實傳播方式代號（請填 甲 或 乙）：", "ans": "乙"},
                    {"label": "(2)-2 【臺灣欒樹】的果實傳播方式代號（請填 甲 或 乙）：", "ans": "甲"},
                    {"label": "(2)-3 【蘋果】的果實傳播方式代號（請填 甲 或 乙）：", "ans": "乙"}
                ],
                "exp": "(1) 保護種子；(2) 龍葵(乙)、臺灣欒樹(甲)、蘋果(乙)。"
            },
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "填充與代號題",
                "q": "【3 做一做】第 2 題：下圖是力的表示方法，請將力的三要素填入 ( ) 中（力的作用點 ／ 力的方向 ／ 力的大小）",
                "blanks": [
                    {"label": "甲.（腳接觸球的位置）：", "ans": "力的作用點"},
                    {"label": "乙.（箭頭指示處）：", "ans": "力的方向"},
                    {"label": "丙.（線段長短）：", "ans": "力的大小"}
                ],
                "exp": "甲：力的作用點、乙：力的方向、丙：力的大小。"
            },
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "填充與代號題",
                "q": "【3 做一做】第 3 題：葉脈是一條一條直線的請填 √，葉脈像互相交錯的網子一樣的請填 ○",
                "blanks": [
                    {"label": "甲. 桑樹（填 √ 或 ○）：", "ans": "○"},
                    {"label": "乙. 百合（填 √ 或 ○）：", "ans": "√"},
                    {"label": "丙. 桂花（填 √ 或 ○）：", "ans": "○"},
                    {"label": "丁. 樟樹（填 √ 或 ○）：", "ans": "○"},
                    {"label": "戊. 榕樹（填 √ 或 ○）：", "ans": "○"},
                    {"label": "己. 竹子（填 √ 或 ○）：", "ans": "√"}
                ],
                "exp": "桑樹、桂花、樟樹、榕樹為網狀脈(○)；百合、竹子為平行脈(√)。"
            },
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "連連看",
                "q": "【3 做一做】第 4 題：下列植物的葉子在莖或枝條上的生長情形（葉序）分別是哪一種？請連一連",
                "left_items": ["(1) 朱槿", "(2) 軟枝黃蟬", "(3) 福木", "(4) 黑板樹", "(5) 南美蟛蜞菊", "(6) 樟樹"],
                "right_items": ["對生", "互生", "輪生"],
                "ans": {
                    "(1) 朱槿": "互生",
                    "(2) 軟枝黃蟬": "輪生",
                    "(3) 福木": "對生",
                    "(4) 黑板樹": "輪生",
                    "(5) 南美蟛蜞菊": "對生",
                    "(6) 樟樹": "互生"
                },
                "exp": "朱槿、樟樹為互生；福木、南美蟛蜞菊為對生；軟枝黃蟬、黑板樹為輪生。"
            }
        ])

    # --- [自然 02_第一次月考B卷 第2頁] ---
    elif ("棕葉狗尾草" in page_text) or ("02_國小自然3上第一次月考B卷" in source_name and page_num == 2):
        extra_qs.extend([
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "填充與代號題",
                "q": "【3 做一做】第 1、3、4 題：生物非生物判斷(填○或×)、蔬菜的根分類(填 A.軸根 或 B.鬚根)與踢球力量大小排序",
                "blanks": [
                    {"label": "1.(1) 樟樹（生物填 ○，非生物填 ×）：", "ans": "○"},
                    {"label": "1.(2) 石頭（生物填 ○，非生物填 ×）：", "ans": "×"},
                    {"label": "1.(3) 海水（生物填 ○，非生物填 ×）：", "ans": "×"},
                    {"label": "1.(4) 麻雀（生物填 ○，非生物填 ×）：", "ans": "○"},
                    {"label": "1.(5) 汽車（生物填 ○，非生物填 ×）：", "ans": "×"},
                    {"label": "1.(6) 月橘（生物填 ○，非生物填 ×）：", "ans": "○"},
                    {"label": "3.(1) 小白菜（軸根填 A，鬚根填 B）：", "ans": "A"},
                    {"label": "3.(2) 菠菜（軸根填 A，鬚根填 B）：", "ans": "A"},
                    {"label": "3.(3) 蔥（軸根填 A，鬚根填 B）：", "ans": "B"},
                    {"label": "3.(4) 蒜（軸根填 A，鬚根填 B）：", "ans": "B"},
                    {"label": "3.(5) 莧菜（軸根填 A，鬚根填 B）：", "ans": "A"},
                    {"label": "3.(6) 稻子（軸根填 A，鬚根填 B）：", "ans": "B"},
                    {"label": "4. 踢足球用力大小由大到小依序排列（如 B>C>A>D）：", "ans": "B>C>A>D"}
                ],
                "exp": "生物：樟樹、麻雀、月橘(○)；軸根(A)：小白菜、菠菜、莧菜；鬚根(B)：蔥、蒜、稻子；出力大小：B>C>A>D。"
            },
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "連連看",
                "q": "【3 做一做】第 2 題：下列各種植物的葉形特徵是什麼？請連一連",
                "left_items": ["(1) 福木", "(2) 萬年青", "(3) 甘藷", "(4) 百合", "(5) 榕樹", "(6) 棕葉狗尾草"],
                "right_items": ["像愛心的形狀", "橢圓形", "細細長長的"],
                "ans": {
                    "(1) 福木": "橢圓形",
                    "(2) 萬年青": "細細長長的",
                    "(3) 甘藷": "像愛心的形狀",
                    "(4) 百合": "細細長長的",
                    "(5) 榕樹": "橢圓形",
                    "(6) 棕葉狗尾草": "細細長長的"
                },
                "exp": "甘藷葉像愛心；福木、榕樹葉為橢圓形；萬年青、百合、棕葉狗尾草為細細長長的。"
            }
        ])

    # --- [自然 03_第二次月考A卷 第2頁] ---
    elif ("健康時報" in page_text) or ("03_國小自然3上第二次月考A卷" in source_name and page_num == 2):
        extra_qs.extend([
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "勾選題",
                "q": "【3 做一做】第 1 題：下列生活中的物品，哪些可以被磁鐵吸住？請打 √",
                "options": ["(1) 刀片", "(2) 長尾夾", "(3) 鉛筆", "(4) 報紙"],
                "ans": ["(1) 刀片", "(2) 長尾夾"],
                "exp": "刀片與長尾夾為鐵製品，可以被磁鐵吸住。"
            },
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "填充與代號題",
                "q": "【3 做一做】第 2 ~ 4 題：塑膠袋壓入水箱水位排序(填 1~3)、磁鐵相吸相斥(填 甲 或 乙)、吐氣泡實驗(填 √ 或 ×)",
                "blanks": [
                    {"label": "2. 水位由高到低填入 1~3：【甲（小袋）】：", "ans": "3"},
                    {"label": "2. 水位由高到低填入 1~3：【乙（中袋）】：", "ans": "2"},
                    {"label": "2. 水位由高到低填入 1~3：【丙（大袋）】：", "ans": "1"},
                    {"label": "3.(1) U形磁鐵（異極相對，填 甲.相吸 或 乙.相斥）：", "ans": "甲"},
                    {"label": "3.(2) 圓形磁鐵（異極相對，填 甲.相吸 或 乙.相斥）：", "ans": "甲"},
                    {"label": "3.(3) 長條形磁鐵（N對N，填 甲.相吸 或 乙.相斥）：", "ans": "乙"},
                    {"label": "4.(1) 看到氣泡往上跑的現象，證明空氣可以被壓縮（填 √ 或 ×）：", "ans": "×"},
                    {"label": "4.(2) 氣泡往上跑完會再跑回杯子底部（填 √ 或 ×）：", "ans": "×"},
                    {"label": "4.(3) 氣泡上升後，水會跟著稍微上升，說明空氣占有空間（填 √ 或 ×）：", "ans": "√"}
                ],
                "exp": "水位由高到低：丙(1)、乙(2)、甲(3)；異極相吸(甲)、同極相斥(乙)；氣泡使水位上升說明空氣占有空間(√)。"
            }
        ])

    # --- [自然 04_第二次月考B卷 第2頁] ---
    elif ("阿宏利用吸管" in page_text) or ("04_國小自然3上第二次月考B卷" in source_name and page_num == 2):
        extra_qs.extend([
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "填充與代號題",
                "q": "【3 做一做】第 1、2、4(1) 題：磁力生活應用(填○或×)、磁鐵相吸相斥(吸住填○，排斥填×)與吹塑膠袋實驗填空",
                "blanks": [
                    {"label": "1.(1) 磁吸式鉛筆盒（填 ○ 或 ×）：", "ans": "○"},
                    {"label": "1.(2) 按下電燈開關（填 ○ 或 ×）：", "ans": "×"},
                    {"label": "1.(3) 磁吸式跳棋（填 ○ 或 ×）：", "ans": "○"},
                    {"label": "1.(4) 放風箏（填 ○ 或 ×）：", "ans": "×"},
                    {"label": "2.(1) U形磁鐵 N對N、S對S（吸住填 ○，排斥填 ×）：", "ans": "×"},
                    {"label": "2.(2) 圓形磁鐵異極相對（吸住填 ○，排斥填 ×）：", "ans": "○"},
                    {"label": "2.(3) 長條與U形磁鐵 S對S（吸住填 ○，排斥填 ×）：", "ans": "×"},
                    {"label": "2.(4) 長條形磁鐵 N對N（吸住填 ○，排斥填 ×）：", "ans": "×"},
                    {"label": "4.(1) 阿宏利用吸管吹壓在書本下的塑膠袋使書本抬升，證明空氣的哪一項特性？", "ans": "空氣占有空間"}
                ],
                "exp": "鉛筆盒與跳棋應用磁力(○)；同極相斥(×)、異極相吸(○)；吹脹塑膠袋抬起書本證明空氣占有空間。"
            },
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "勾選題",
                "q": "【3 做一做】第 3 題：請把會被磁鐵吸住的物品勾選起來",
                "options": ["(1) 剪刀刀刃", "(2) 膠帶", "(3) 鋁罐", "(4) 塑膠尺", "(5) 釘書針", "(6) 膠泥", "(7) 橡皮筋", "(8) 鐵釘", "(9) 鉛筆"],
                "ans": ["(1) 剪刀刀刃", "(5) 釘書針", "(8) 鐵釘"],
                "exp": "剪刀刀刃、釘書針、鐵釘為鐵製品，可被磁鐵吸住。"
            },
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "勾選題",
                "q": "【3 做一做】第 4.(2) 題：抽掉吸管發現放在塑膠袋口的紙片被吹走了，可證明空氣的什麼特性？請打 √",
                "options": ["① 空氣可以被壓縮", "② 空氣無色無味", "③ 空氣流動形成風", "④ 被壓縮的空氣傳送力量，使物體移動"],
                "ans": ["① 空氣可以被壓縮", "③ 空氣流動形成風", "④ 被壓縮的空氣傳送力量，使物體移動"],
                "exp": "書本壓住袋子使空氣被壓縮，抽出吸管時空氣流出形成風並傳送力量吹走紙片。"
            }
        ])

    # --- [自然 05_第三次月考A卷 第2頁] ---
    elif ("小蓮用40毫升" in page_text or "小賢的實驗紀錄表" in page_text) or ("05_國小自然3上第三次月考A卷" in source_name and page_num == 2):
        extra_qs.extend([
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "填充與代號題",
                "q": "【3 做一做】第 1 & 2 題：砂糖與食鹽溶解實驗紀錄 ＆ 酸鹼性填空",
                "blanks": [
                    {"label": "1.(1) 請問哪一個圖示是「一平匙」正確的分量？（填 1、2 或 3）：", "ans": "2"},
                    {"label": "1.(2)-1 在 40 毫升水中，【砂糖】最多溶解幾平匙？（正正T）：", "ans": "13"},
                    {"label": "1.(2)-2 在 40 毫升水中，【食鹽】最多溶解幾平匙？（正T）：", "ans": "8"},
                    {"label": "1.(3) 從實驗可以知道，哪一種物質可以溶解的量比較多？", "ans": "砂糖"},
                    {"label": "2.(1) 利用溶解的特性可不可以區分出檸檬酸、小蘇打粉、食鹽三種材料？（填 可以 或 不可以）：", "ans": "不可以"},
                    {"label": "2.(2)-1 檸檬酸水是什麼性？（填 酸性、中性 或 鹼性）：", "ans": "酸性"},
                    {"label": "2.(2)-2 小蘇打水是什麼性？（填 酸性、中性 或 鹼性）：", "ans": "鹼性"}
                ],
                "exp": "平匙需刮平(2)；砂糖13平匙、食鹽8平匙；三者皆能完全溶解故不可單靠溶解區分；檸檬酸水為酸性、小蘇打水為鹼性。"
            },
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "連連看",
                "q": "【3 做一做】第 3 題：下列調味品外觀的描述，請配對連出正確答案",
                "left_items": ["(1) 黃砂糖", "(2) 黑胡椒粒", "(3) 食鹽"],
                "right_items": [
                    "甲. 白色的，摸起來粗粗的，聞起來沒有氣味",
                    "乙. 黃色的，摸起來粗粗的，聞起來甜甜的",
                    "丙. 深棕色，摸起來粗粗的，聞起來有辛辣氣味"
                ],
                "ans": {
                    "(1) 黃砂糖": "乙. 黃色的，摸起來粗粗的，聞起來甜甜的",
                    "(2) 黑胡椒粒": "丙. 深棕色，摸起來粗粗的，聞起來有辛辣氣味",
                    "(3) 食鹽": "甲. 白色的，摸起來粗粗的，聞起來沒有氣味"
                },
                "exp": "黃砂糖為黃色粗顆粒有甜味(乙)；黑胡椒粒為深棕色粗顆粒有辛辣味(丙)；食鹽為白色粗顆粒無氣味(甲)。"
            }
        ])

    # --- [自然 06_第三次月考B卷 第2頁] ---
    elif ("仙草蜜" in page_text) or ("06_國小自然3上第三次月考B卷" in source_name and page_num == 2):
        extra_qs.extend([
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "填充與代號題",
                "q": "【3 做一做】第 1 & 3 題：生活中物質溶解的例子（是溶解填 ○，不是填 ×）＆ 仙草蜜砂糖水問答填空",
                "blanks": [
                    {"label": "1.(1) 紅茶加入砂糖（填 ○ 或 ×）：", "ans": "○"},
                    {"label": "1.(2) 熱湯加入食鹽（填 ○ 或 ×）：", "ans": "○"},
                    {"label": "1.(3) 洗衣機中加洗衣粉（填 ○ 或 ×）：", "ans": "○"},
                    {"label": "1.(4) 熱湯加入黑胡椒粒（填 ○ 或 ×）：", "ans": "×"},
                    {"label": "1.(5) 鮮奶加入麥片（填 ○ 或 ×）：", "ans": "×"},
                    {"label": "1.(6) 咖啡加入方糖（填 ○ 或 ×）：", "ans": "○"},
                    {"label": "1.(7) 薯條沾番茄醬（填 ○ 或 ×）：", "ans": "×"},
                    {"label": "1.(8) 水加入黑糖塊（填 ○ 或 ×）：", "ans": "○"},
                    {"label": "3.(1) 為什麼透明的水加入黃砂糖後會變成黃色？", "ans": "黃砂糖溶解在水中"},
                    {"label": "3.(2) 為什麼攪拌之後還有砂糖顆粒沉在底部？", "ans": "砂糖溶解的量是有限的"},
                    {"label": "3.(3) 可以用什麼方法讓剩下的砂糖顆粒溶解在水中呢？", "ans": "提高水溫或增加水量"}
                ],
                "exp": "砂糖、食鹽、洗衣粉、方糖、黑糖塊溶於水屬於溶解(○)；黑胡椒粒、麥片、番茄醬不會溶解(×)。提高水溫或增加水量可增加溶解量。"
            },
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "勾選題",
                "q": "【3 做一做】第 2 題：下列哪些作法可以維護空氣清新乾淨？請打 √",
                "options": ["(1) 使用風力發電", "(2) 砍伐森林", "(3) 騎腳踏車上學", "(4) 工廠排放廢氣"],
                "ans": ["(1) 使用風力發電", "(3) 騎腳踏車上學"],
                "exp": "使用風力發電與騎腳踏車上學有助於維護空氣品質。"
            }
        ])

    # --- [自然 07_期中考A卷 第2頁] ---
    elif ("白米飯" in page_text and "果汁" in page_text) or ("07_國小自然3上期中考A卷" in source_name and page_num == 2):
        extra_qs.extend([
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "填充與代號題",
                "q": "【3 做一做】第 1、2、4 題：植物產品配對(A.稻子/B.棉花/C.柳橙/D.樟樹)、根的分類(軸根填√，鬚根填○)、力的種類(ㄅ.水的浮力/ㄆ.風力/ㄇ.磁力/ㄈ.彈力)",
                "blanks": [
                    {"label": "1.(1) 家具（填代號 A~D）：", "ans": "D"},
                    {"label": "1.(2) 白米飯（填代號 A~D）：", "ans": "A"},
                    {"label": "1.(3) 果汁（填代號 A~D）：", "ans": "C"},
                    {"label": "1.(4) 衣服（填代號 A~D）：", "ans": "B"},
                    {"label": "2.(1) 小白菜（軸根填 √，鬚根填 ○）：", "ans": "√"},
                    {"label": "2.(2) 菠菜（軸根填 √，鬚根填 ○）：", "ans": "√"},
                    {"label": "2.(3) 蔥（軸根填 √，鬚根填 ○）：", "ans": "○"},
                    {"label": "2.(4) 蒜（軸根填 √，鬚根填 ○）：", "ans": "○"},
                    {"label": "2.(5) 莧菜（軸根填 √，鬚根填 ○）：", "ans": "√"},
                    {"label": "2.(6) 稻子（軸根填 √，鬚根填 ○）：", "ans": "○"},
                    {"label": "4.(1) 風箏（填代號 ㄅ、ㄆ、ㄇ 或 ㄈ）：", "ans": "ㄆ"},
                    {"label": "4.(2) 門擋（填代號 ㄅ、ㄆ、ㄇ 或 ㄈ）：", "ans": "ㄇ"},
                    {"label": "4.(3) 原子筆（填代號 ㄅ、ㄆ、ㄇ 或 ㄈ）：", "ans": "ㄈ"},
                    {"label": "4.(4) 游泳圈（填代號 ㄅ、ㄆ、ㄇ 或 ㄈ）：", "ans": "ㄅ"}
                ],
                "exp": "1. D、A、C、B；2. 小白菜/菠菜/莧菜為軸根(√)，蔥/蒜/稻子為鬚根(○)；4. ㄆ、ㄇ、ㄈ、ㄅ。"
            },
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "連連看",
                "q": "【3 做一做】第 3 題：下列磁鐵擺放的位置，會使兩個磁鐵相吸還是相斥呢？請連連看",
                "left_items": ["(1) U形磁鐵（N對N、S對S）", "(2) U形磁鐵（N對S、S對N）", "(3) 長條磁鐵（N極對N極）", "(4) 長條磁鐵垂直（S極對N極）"],
                "right_items": ["甲. 相吸", "乙. 相斥"],
                "ans": {
                    "(1) U形磁鐵（N對N、S對S）": "乙. 相斥",
                    "(2) U形磁鐵（N對S、S對N）": "甲. 相吸",
                    "(3) 長條磁鐵（N極對N極）": "乙. 相斥",
                    "(4) 長條磁鐵垂直（S極對N極）": "甲. 相吸"
                },
                "exp": "相同磁極靠近會互相排斥(乙.相斥)，不同磁極靠近會互相吸引(甲.相吸)。"
            }
        ])

    # --- [自然 08_期中考B卷 第2頁] ---
    elif ("豔紫荊花" in page_text or "豔紫荆花" in page_text or "組合2個磁鐵" in page_text) or ("08_國小自然3上期中考B卷" in source_name and page_num == 2):
        extra_qs.extend([
            {
                "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
                "type": "填充與代號題",
                "q": "【3 做一做】第 1、3、4 題：磁鐵數量實驗(填○或×)、植物葉序(A.互生/B.對生/C.輪生)、豔紫荊花構造(甲.花瓣/乙.花萼/丙.雄蕊/丁.雌蕊)",
                "blanks": [
                    {"label": "1.(1)-A 組合 2 個磁鐵能吸住的迴紋針數量最多（填 ○ 或 ×）：", "ans": "×"},
                    {"label": "1.(1)-B 組合 2 個磁鐵的磁力比 1 個磁鐵的磁力強（填 ○ 或 ×）：", "ans": "○"},
                    {"label": "1.(1)-C 每多組合 1 個磁鐵，就能多吸住 2 支迴紋針（填 ○ 或 ×）：", "ans": "×"},
                    {"label": "1.(2) 組合磁鐵的數量和磁力大小有什麼關係？", "ans": "磁力越強"},
                    {"label": "3.(1) 樟樹的葉序代號（填 A、B 或 C）：", "ans": "A"},
                    {"label": "3.(2) 青楓的葉序代號（填 A、B 或 C）：", "ans": "B"},
                    {"label": "3.(3) 黑板樹的葉序代號（填 A、B 或 C）：", "ans": "C"},
                    {"label": "3.(4) 福木的葉序代號（填 A、B 或 C）：", "ans": "B"},
                    {"label": "4.(1) 豔紫荊花構造(1)（雄蕊，填 甲、乙、丙 或 丁）：", "ans": "丙"},
                    {"label": "4.(2) 豔紫荊花構造(2)（雌蕊，填 甲、乙、丙 或 丁）：", "ans": "丁"},
                    {"label": "4.(3) 豔紫荊花構造(3)（花瓣，填 甲、乙、丙 或 丁）：", "ans": "甲"},
                    {"label": "4.(4) 豔紫荊花構造(4)（花萼，填 甲、乙、丙 或 丁）：", "ans": "乙"}
                ],
                "exp": "1. ×、○、×，越多磁鐵組合磁力越強；3. A、B、C、B；4. 丙、丁、甲、乙。"
            }
        ])

    # --- [數學 南一隨堂演練 第16頁 算式連連看] ---
    elif "5613+1999" in page_text or ("4950-238" in page_text):
        extra_qs.append({
            "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
            "type": "連連看",
            "q": "【一、把答案一樣的算式連起來】請配對連線：",
            "left_items": ["① 5613 + 1999", "② 3015 + 4327", "③ 691 + 4021", "④ 6103 - 498", "⑤ 2214 - 1190"],
            "right_items": ["ㄅ. 4950 - 238", "ㄆ. 4000 + 3612", "ㄇ. 2486 + 4856", "ㄈ. 3157 - 2133", "ㄉ. 6628 - 1023"],
            "ans": {
                "① 5613 + 1999": "ㄆ. 4000 + 3612",
                "② 3015 + 4327": "ㄇ. 2486 + 4856",
                "③ 691 + 4021": "ㄅ. 4950 - 238",
                "④ 6103 - 498": "ㄉ. 6628 - 1023",
                "⑤ 2214 - 1190": "ㄈ. 3157 - 2133"
            },
            "exp": "①=7612(ㄆ)；②=7342(ㄇ)；③=4712(ㄅ)；④=5605(ㄉ)；⑤=1024(ㄈ)。"
        })

    # --- [數學 南一隨堂演練 第29頁 公分毫米連連看] ---
    elif "10公分9毫米" in page_text and "800毫米" in page_text:
        extra_qs.append({
            "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
            "type": "連連看",
            "q": "【一、連連看】請把相等的長度連起來：",
            "left_items": ["① 5公分", "② 10公分9毫米", "③ 800毫米", "④ 7公分1毫米", "⑤ 10公分"],
            "right_items": ["ㄅ. 80公分", "ㄆ. 50毫米", "ㄇ. 109毫米", "ㄈ. 100毫米", "ㄉ. 71毫米"],
            "ans": {
                "① 5公分": "ㄆ. 50毫米",
                "② 10公分9毫米": "ㄇ. 109毫米",
                "③ 800毫米": "ㄅ. 80公分",
                "④ 7公分1毫米": "ㄉ. 71毫米",
                "⑤ 10公分": "ㄈ. 100毫米"
            },
            "exp": "1公分=10毫米：①➔ㄆ、②➔ㄇ、③➔ㄅ、④➔ㄉ、⑤➔ㄈ。"
        })

    # --- [數學 南一隨堂演練 第37頁 直角連連看] ---
    elif "哪個角是直角" in page_text and "比直角小" in page_text:
        extra_qs.append({
            "subject": subject, "unit": unit_label, "pdf_name": source_name, "page_num": page_num,
            "type": "連連看",
            "q": "【二、連連看】哪個角是直角？哪個角比直角小？哪個角比直角大？",
            "left_items": ["(1) 角1", "(2) 角2", "(3) 角3", "(4) 角4", "(5) 角5"],
            "right_items": ["比直角小", "直角", "比直角大"],
            "ans": {
                "(1) 角1": "比直角小",
                "(2) 角2": "直角",
                "(3) 角3": "比直角大",
                "(4) 角4": "比直角小",
                "(5) 角5": "比直角大"
            },
            "exp": "(1)、(4)是銳角（比直角小）；(2)是直角；(3)、(5)是鈍角（比直角大）。"
        })

    return extra_qs


def parse_all_questions_from_pdf(pdf_bytes: bytes, source_name: str, target_page: int = None, subject: str = None) -> list:
    """
    全方位解析四大科目（自然、社會、數學、國語）PDF 考卷中的：
    1. 是非題 (○ / ×)
    2. 選擇題 (支援①②③④與(1)(2)(3)(4)格式)
    3. 頁面精準做一做題組與連連看
    4. 通用規則解析：填空題（括號填空、數學算式填空、比大小、應用題「答：…」填空）、打√勾選題
       ※ 所有填充題一律保持純文字輸入框（無下拉選單）！
    """
    if not subject:
        subject = detect_subject_from_name(source_name)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parsed_list = []

    pages_to_scan = [target_page] if target_page is not None else range(len(doc))

    for page_idx in pages_to_scan:
        if page_idx < 0 or page_idx >= len(doc):
            continue
        page = doc.load_page(page_idx)
        raw_page_text = page.get_text("text")
        text = clean_pdf_text(raw_page_text)
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        joined_text = " ".join(lines)
        unit_label = f"考卷：{source_name} (第{page_idx+1}頁)"

        # 1. 解析是非題
        tf_pattern = re.compile(r'[\(（]\s*([○◯O×X✕✓√]?)\s*[\)）]\s*(\d+)\s*[\.、]\s*([^()（）①②③④]+?[。？\?])')
        for match in tf_pattern.finditer(joined_text):
            raw_ans, q_num, q_body = match.groups()
            q_body = clean_math_symbols(re.sub(r'\s+', '', q_body))
            if len(q_body) < 6 or any(k in q_body for k in ["選擇題", "是非題", "做一做", "勾選題", "配合題", "排序題"]):
                continue
            ans = "○" if raw_ans in ["○", "◯", "O", "✓", "√"] else ("×" if raw_ans in ["×", "X", "✕"] else "○")
            parsed_list.append({
                "subject": subject,
                "unit": unit_label,
                "pdf_name": source_name,
                "page_num": page_idx + 1,
                "type": "是非題",
                "q": f"是非第 {q_num} 題：{q_body}",
                "options": ["○", "×"],
                "ans": ans,
                "exp": f"來源：{source_name} 第 {page_idx+1} 頁"
            })

        # 2. 解析選擇題（支援自然/社會 4 選項與國語 3 選項）
        mc4_pattern = re.compile(
            r'[\(（]\s*([①②③④1234]?)\s*[\)）]\s*(\d+|\(\d+\))\s*[\.、]?\s*([^①②③④\(（]{4,120}?[？\?：:。])\s*'
            r'[①1]\s*([^②③④]+?)\s*[②2]\s*([^③④]+?)\s*[③3]\s*([^④]+?)\s*[④4]\s*([^\n。；①②③④\(（]{1,35})'
        )
        for match in mc4_pattern.finditer(joined_text):
            raw_ans, q_num, q_stem, opt1, opt2, opt3, opt4 = match.groups()
            q_stem = clean_math_symbols(re.sub(r'\s+', '', q_stem))
            o1 = clean_math_symbols(re.sub(r'\s+', '', opt1).rstrip('。'))
            o2 = clean_math_symbols(re.sub(r'\s+', '', opt2).rstrip('。'))
            o3 = clean_math_symbols(re.sub(r'\s+', '', opt3).rstrip('。'))
            o4 = clean_math_symbols(re.sub(r'\s+', '', opt4).rstrip('。'))
            o4 = re.split(r'(?:三、|四、|3做一做|做一做|素養題|勾選題|配合題|排序題)', o4)[0].strip()
            opts = [f"①{o1}", f"②{o2}", f"③{o3}", f"④{o4}"]
            ans_map = {"①": opts[0], "1": opts[0], "②": opts[1], "2": opts[1], "③": opts[2], "3": opts[2], "④": opts[3], "4": opts[3]}
            ans_val = ans_map.get(raw_ans, opts[0])
            parsed_list.append({
                "subject": subject,
                "unit": unit_label,
                "pdf_name": source_name,
                "page_num": page_idx + 1,
                "type": "選擇題",
                "q": f"選擇第 {q_num} 題：{q_stem}",
                "options": opts,
                "ans": ans_val,
                "exp": f"來源：{source_name} 第 {page_idx+1} 頁"
            })

        # 國語課堂練習常見 3 選項選擇題
        if subject == "📘 國語":
            mc3_pattern = re.compile(
                r'(\d+)\s*[\.、]\s*[\(（]\s*([①②③123]?)\s*[\)）]\s*([^①②③]{4,100}?)\s*'
                r'[①1]\s*([^②③]+?)\s*[②2]\s*([^③]+?)\s*[③3]\s*([^\n①②③1234\(（]{1,35})'
            )
            for match in mc3_pattern.finditer(joined_text):
                q_num, raw_ans, q_stem, opt1, opt2, opt3 = match.groups()
                q_stem = clean_math_symbols(re.sub(r'\s+', '', q_stem))
                o1 = clean_math_symbols(re.sub(r'\s+', '', opt1).rstrip('。'))
                o2 = clean_math_symbols(re.sub(r'\s+', '', opt2).rstrip('。'))
                o3 = re.split(r'(?:三、|四、|\d+\.)', clean_math_symbols(re.sub(r'\s+', '', opt3)).rstrip('。'))[0].strip()
                opts = [f"①{o1}", f"②{o2}", f"③{o3}"]
                ans_map = {"①": opts[0], "1": opts[0], "②": opts[1], "2": opts[1], "③": opts[2], "3": opts[2]}
                ans_val = ans_map.get(raw_ans, opts[0])
                parsed_list.append({
                    "subject": subject,
                    "unit": unit_label,
                    "pdf_name": source_name,
                    "page_num": page_idx + 1,
                    "type": "選擇題",
                    "q": f"選擇第 {q_num} 題：{q_stem}",
                    "options": opts,
                    "ans": ans_val,
                    "exp": f"來源：{source_name} 第 {page_idx+1} 頁"
                })

        # 3. 載入該頁專屬的精準題組
        specific_qs = get_page_specific_exercises(joined_text, unit_label, source_name, page_idx + 1, subject)
        if specific_qs:
            parsed_list.extend(specific_qs)

        # 4. 通用深度「填空題／計算題／代號題」解析器（全部維持純填空 input）
        general_blanks = []

        if subject == "🔢 數學":
            math_paren_pat = re.compile(r'([^\n()（）]{2,30}?[\(（]\s*([0-9><=＋－×÷Lmlcm大和小左右邊頂點圓心圓周半徑直徑長方形正方形會不會甲乙丙丁ㄅㄆㄇㄈ]{1,10})\s*[\)）][^\n()（）]{0,15})')
            for m in math_paren_pat.finditer(joined_text):
                expr_full, ans_val = m.groups()
                expr_clean = clean_math_symbols(re.sub(r'\s+', '', expr_full))
                if any(w in expr_clean for w in ["配合課本", "單位:", "題型", "誤差範圍", "僅供參考", "填大或小", "填長或短", "填半徑或直徑", "填會或不會"]):
                    continue
                if re.match(r'^[\(（]\d+[\)）]$', expr_clean):
                    continue
                masked_expr = re.sub(r'[\(（]\s*' + re.escape(ans_val) + r'\s*[\)）]', '【 ＿＿ 】', expr_clean, count=1)
                general_blanks.append({"label": masked_expr, "ans": clean_math_symbols(ans_val)})

            math_ans_pat = re.compile(r'(\(\d+\)[^答]{8,90}?)\s*答\s*[:：]\s*([0-9\u4e00-\u9fff\.…、，]+)')
            for m in math_ans_pat.finditer(joined_text):
                stem_part, ans_part = m.groups()
                stem_clean = clean_math_symbols(re.sub(r'\s+', '', stem_part))
                ans_clean = clean_math_symbols(re.sub(r'\s+', '', ans_part).rstrip('。'))
                general_blanks.append({"label": f"應用題 {stem_clean[:42]}... ➔ 答：", "ans": ans_clean})

        else:
            if not specific_qs:
                sub_code_pattern = re.compile(r'[\(（]\s*([√✓○◯×X✕△A-F甲乙丙丁ㄅㄆㄇㄈ1-6])\s*[\)）]\s*([①②③④⑤⑥\(\d\)\d\.、\s]*[\u4e00-\u9fff]{2,18})')
                for sm in sub_code_pattern.finditer(joined_text):
                    ans_sym, item_lbl = sm.groups()
                    item_lbl_clean = clean_math_symbols(re.sub(r'\s+', '', item_lbl))
                    if len(item_lbl_clean) > 18 or len(item_lbl_clean) < 2 or "每題" in item_lbl_clean or "共" in item_lbl_clean:
                        continue
                    ans_norm = "√" if ans_sym in ["√", "✓"] else ("○" if ans_sym in ["○", "◯"] else ("×" if ans_sym in ["×", "X", "✕"] else ans_sym))
                    general_blanks.append({
                        "label": f"{item_lbl_clean}（請直接填入答案符號或代號）：",
                        "ans": ans_norm
                    })

            if not specific_qs:
                inline_fill_pattern = re.compile(r'([\u4e00-\u9fff0-9A-Za-z，、：]{2,25}?[\(（]\s*([\u4e00-\u9fffA-DSNsN0-9]{1,8})\s*[\)）][\u4e00-\u9fff]{0,12})')
                for im in inline_fill_pattern.finditer(joined_text):
                    full_phr, ans_tok = im.groups()
                    full_phr_clean = clean_math_symbols(re.sub(r'\s+', '', full_phr))
                    if any(skip_w in full_phr_clean for skip_w in ["每題", "每格", "共", "分)", "爺爺或外公", "奶奶或外婆", "日日春", "蓮花", "並列複句", "承接複句", "條件複句", "因果複句", "目的複句", "轉折複句", "遞進複句", "假設複句", "擬人化", "視覺", "聽覺", "觸覺", "嗅覺"]):
                        continue
                    if re.match(r'^[\(（]\d+[\)）]', full_phr_clean):
                        continue
                    masked_phr = re.sub(r'[\(（]\s*' + re.escape(ans_tok) + r'\s*[\)）]', '【 ＿＿ 】', full_phr_clean, count=1)
                    general_blanks.append({
                        "label": masked_phr,
                        "ans": clean_math_symbols(ans_tok)
                    })

        if general_blanks:
            for chunk_i in range(0, len(general_blanks), 10):
                chunk = general_blanks[chunk_i:chunk_i + 10]
                parsed_list.append({
                    "subject": subject,
                    "unit": unit_label,
                    "pdf_name": source_name,
                    "page_num": page_idx + 1,
                    "type": "填充與代號題",
                    "q": f"【本頁填空題組 {(chunk_i // 10) + 1}】（共 {len(chunk)} 格，請直接輸入答案）",
                    "blanks": chunk,
                    "exp": f"來源：{source_name} 第 {page_idx+1} 頁填空解析"
                })

    return parsed_list


def ensure_db_questions_upgraded():
    """當 PARSER_VERSION 更新時，自動將 SQLite 內已儲存的 PDF 重新深度解析並清除多餘符號"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT val FROM meta_info WHERE key = 'parser_version'")
        row = c.fetchone()
        current_ver = row[0] if row else ""

        if current_ver != PARSER_VERSION:
            pdfs = db_load_all_pdfs()
            for fname, info in pdfs.items():
                subj = info.get("subject") or detect_subject_from_name(fname)
                upgraded_qs = parse_all_questions_from_pdf(info["bytes"], fname, subject=subj)
                db_replace_pdf_questions(fname, upgraded_qs, subject=subj)
            c.execute("INSERT OR REPLACE INTO meta_info (key, val) VALUES ('parser_version', ?)", (PARSER_VERSION,))
            conn.commit()
        conn.close()
    except Exception:
        pass


ensure_db_questions_upgraded()


def render_pdf_page_image(pdf_bytes: bytes, page_num: int, hide_answers: bool = False, zoom: float = 2.2) -> Image.Image:
    """將 PDF 轉為高解析圖片，並可一鍵濾除教師版紅/藍解答字體與連線"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(page_num)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    if hide_answers:
        arr = np.array(img, dtype=np.int16)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        red_mask = (r > 140) & ((r - g) > 25) & ((r - b) > 15)
        blue_mask = (b > 130) & ((b - r) > 25) & ((b - g) > 10)
        arr[red_mask | blue_mask] = [255, 255, 255]
        img = Image.fromarray(arr.astype(np.uint8))

    return img


# ==========================================
# 8. 互動題目渲染與自動批改核心函數（填空題 100% 使用純輸入框）
# ==========================================
def render_interactive_question(idx: int, item: dict, form_prefix: str):
    """渲染單一題目（是非、選擇、勾選、純文字填空、連連看）"""
    q_type = item.get("type", "選擇題")
    subj_tag = item.get("subject", "")
    q_header = clean_cite(item["q"]).replace("\n", "<br>")
    st.markdown(
        f'<div class="q-box">第 {idx+1} 題 <span style="font-size:0.85em; color:#2563eb;">[{subj_tag}] [{item.get("unit","")}] [{q_type}]</span><br>{q_header}</div>',
        unsafe_allow_html=True
    )

    if q_type in ["是非題", "選擇題"]:
        cleaned_opts = [clean_cite(o) for o in item["options"]]
        st.radio(
            f"作答_{form_prefix}_{idx}",
            cleaned_opts,
            index=None,
            key=f"{form_prefix}_q_{idx}",
            label_visibility="collapsed",
            horizontal=(q_type == "是非題")
        )

    elif q_type == "勾選題":
        st.caption("👇 請在正確的項目打 √（可複選）：")
        cols = st.columns(2)
        for opt_i, opt_label in enumerate(item["options"]):
            with cols[opt_i % 2]:
                st.checkbox(
                    clean_cite(opt_label),
                    key=f"{form_prefix}_q_{idx}_chk_{opt_i}"
                )

    elif q_type == "填充與代號題":
        st.caption("👇 請直接在下方空格輸入答案（不必選單，直接填入文字、數字、代號或符號）：")
        for b_i, b_item in enumerate(item.get("blanks", [])):
            b_label = clean_cite(b_item["label"])
            st.text_input(
                b_label,
                placeholder="請直接輸入答案...",
                key=f"{form_prefix}_q_{idx}_blank_{b_i}"
            )

    elif q_type == "連連看":
        st.caption("👇 請為左邊的每一個項目選擇右邊對應的連線目標（選完後下方會即時畫出彩色連線！）：")
        left_items = [clean_cite(x) for x in item.get("left_items", [])]
        right_items = [clean_cite(x) for x in item.get("right_items", [])]
        current_pairs = {}
        cols = st.columns(2)
        for l_i, l_text in enumerate(left_items):
            with cols[l_i % 2]:
                sel = st.selectbox(
                    f"🔗 {l_text} 連到 👉",
                    ["-- 請選擇連線對象 --"] + right_items,
                    key=f"{form_prefix}_q_{idx}_match_{l_i}"
                )
                if sel != "-- 請選擇連線對象 --":
                    current_pairs[l_text] = sel
        render_svg_matching(left_items, right_items, current_pairs, show_grading=False)


def is_blank_answer_correct(user_val: str, ans_val: str) -> bool:
    """智慧型填空比對：支援全形半形符號、勾選圈選同義字與關鍵詞比對"""
    u = clean_math_symbols(user_val).replace(" ", "").replace(" ", "").upper()
    a = clean_math_symbols(ans_val).replace(" ", "").replace(" ", "").upper()
    if not u:
        return False
    if u == a:
        return True
    sym_groups = [
        {"√", "✓", "V", "打√", "打V"},
        {"○", "◯", "O", "0", "畫○", "畫O"},
        {"×", "X", "✕", "打×", "打X"},
        {"△", "Δ", "三角形"}
    ]
    for grp in sym_groups:
        if u in grp and a in grp:
            return True
    u_core = re.sub(r'[.、，。：:（）()「」]', '', u)
    a_core = re.sub(r'[.、，。：:（）()「」]', '', a)
    if u_core and u_core == a_core:
        return True
    if len(u_core) >= 2 and (u_core in a_core or a_core in u_core):
        return True
    if len(a_core) == 1 and u_core.startswith(a_core):
        return True
    return False


def grade_interactive_question(idx: int, item: dict, form_prefix: str) -> tuple:
    """自動批改單一題目，回傳 (is_all_correct, user_ans_summary, correct_ans_summary)"""
    q_type = item.get("type", "選擇題")

    if q_type in ["是非題", "選擇題"]:
        u_ans = st.session_state.get(f"{form_prefix}_q_{idx}")
        expected_ans = clean_cite(item["ans"])
        is_ok = (u_ans == expected_ans)
        return is_ok, (u_ans if u_ans else "未作答"), expected_ans

    elif q_type == "勾選題":
        chosen = []
        for opt_i, opt_label in enumerate(item["options"]):
            if st.session_state.get(f"{form_prefix}_q_{idx}_chk_{opt_i}", False):
                chosen.append(clean_cite(opt_label))
        expected = [clean_cite(x) for x in item["ans"]]
        is_ok = (set(chosen) == set(expected))
        u_str = "、".join(chosen) if chosen else "未勾選"
        c_str = "、".join(expected)
        return is_ok, u_str, c_str

    elif q_type == "填充與代號題":
        blanks = item.get("blanks", [])
        all_ok = True
        u_parts = []
        c_parts = []
        for b_i, b_item in enumerate(blanks):
            raw_val = st.session_state.get(f"{form_prefix}_q_{idx}_blank_{b_i}", "")
            u_val = str(raw_val).strip()
            ans_val = clean_cite(b_item["ans"]).strip()
            sub_ok = is_blank_answer_correct(u_val, ans_val)
            if not sub_ok:
                all_ok = False
            u_parts.append(f"({b_i+1}) {u_val if u_val else '未填'}{'✔️' if sub_ok else '❌'}")
            c_parts.append(f"({b_i+1}) {ans_val}")
        return all_ok, " ｜ ".join(u_parts), " ｜ ".join(c_parts)

    elif q_type == "連連看":
        left_items = [clean_cite(x) for x in item.get("left_items", [])]
        right_items = [clean_cite(x) for x in item.get("right_items", [])]
        raw_expected_map = item.get("ans", {})
        expected_map = {clean_cite(k): clean_cite(v) for k, v in raw_expected_map.items()}
        user_map = {}
        all_ok = True
        u_parts = []
        c_parts = []
        for l_i, l_text in enumerate(left_items):
            sel = st.session_state.get(f"{form_prefix}_q_{idx}_match_{l_i}", "-- 請選擇連線對象 --")
            u_val = "" if sel == "-- 請選擇連線對象 --" else clean_cite(sel)
            if u_val:
                user_map[l_text] = u_val
            exp_val = expected_map.get(l_text, "")
            sub_ok = (u_val == exp_val)
            if not sub_ok:
                all_ok = False
            u_parts.append(f"{l_text}➔{u_val if u_val else '未連'}{'✔️' if sub_ok else f'❌(應連:{exp_val})'}")
            c_parts.append(f"{l_text}➔{exp_val}")

        st.markdown(f"##### 🎯 第 {idx+1} 題【連連看完整解答修正圖】")
        render_svg_matching(left_items, right_items, user_map, correct_pairs=expected_map, show_grading=True)
        return all_ok, " ； ".join(u_parts), " ； ".join(c_parts)

    return False, "未知題型", ""


# ==========================================
# 9. 側邊欄：科目切換、學習模式與字體縮放控制
# ==========================================
with st.sidebar:
    st.title("🎓 小三全科複習神器")
    st.caption("115上｜自然・社會・數學・國語")

    active_subject = st.selectbox(
        "📚 選擇學習科目",
        SUBJECT_LIST,
        index=0
    )

    font_scale = st.slider(
        "🔍 畫面字體大小（推薦 125%~145%）",
        min_value=100,
        max_value=160,
        value=125,
        step=5
    )
    apply_responsive_css(font_scale)

    mode = st.radio(
        "📌 選擇學習模式",
        [
            "📖 完整重點複習（含遮罩背誦）",
            "🎮 全題型題庫闖關（含連連看/填空）",
            "📄 考卷 PDF 直接作答＆自動批改",
            "❌ 錯題複習本＆永久備份管理"
        ]
    )

    saved_pdfs_subj = db_load_all_pdfs(active_subject)
    custom_qs_subj = db_load_custom_questions(active_subject)
    wrong_qs_subj = db_load_wrong_questions(active_subject)
    default_qs_subj = [q for q in DEFAULT_QUIZ_BANK if q.get("subject") == active_subject]
    combined_quiz_bank = default_qs_subj + custom_qs_subj

    st.divider()
    st.markdown(f"**🎯 目前科目：** `{active_subject}`")
    st.markdown(f"**📚 本科題庫數：** `{len(combined_quiz_bank)}` 題")
    st.markdown(f"**📕 本科錯題數：** `{len(wrong_qs_subj)}` 題 *(永久存檔)*")
    st.markdown(f"**📂 本科已存考卷：** `{len(saved_pdfs_subj)}` 份 *(永久存檔)*")


# ==========================================
# 模式 1：完整重點複習（依所選科目顯示自然、社會、數學、國語）
# ==========================================
if mode == "📖 完整重點複習（含遮罩背誦）":
    st.header(f"📖 {active_subject}｜3 上完整重點複習知識庫")
    st.info("💡 開啟「🙈 遮罩【關鍵字】填空自測」，系統會將所有重點關鍵字隱藏，讓你在手機或電腦上隨時背誦自測！")

    subj_data = SUBJECT_REVIEW_DATA.get(active_subject, {})
    col_u, col_m, col_s = st.columns([2.2, 1.4, 2])
    with col_u:
        selected_unit = st.selectbox("📂 選擇複習單元／課次", ["🌟 全部單元完整總覽"] + list(subj_data.keys()))
    with col_m:
        hide_kw = st.toggle("🙈 遮罩【關鍵字】填空自測", value=False)
    with col_s:
        search_kw = st.text_input("🔍 關鍵字快速搜尋", "")

    units_to_show = list(subj_data.keys()) if selected_unit == "🌟 全部單元完整總覽" else [selected_unit]

    for u_name in units_to_show:
        u_data = subj_data[u_name]
        st.subheader(f"{u_data['icon']} {u_name}")

        for act_name, act_content in u_data["activities"].items():
            full_act_str = json.dumps(act_content, ensure_ascii=False)
            if search_kw and (search_kw not in full_act_str and search_kw not in act_name):
                continue

            with st.expander(f"📌 {act_name}", expanded=True):
                st.markdown("#### 📝 核心重點整理")
                for pt in act_content["points"]:
                    if search_kw and search_kw not in pt:
                        continue
                    rendered_pt = render_masked_text(pt, hide_kw)
                    st.markdown(f'<div class="review-card">{rendered_pt}</div>', unsafe_allow_html=True)

                for tb in act_content["tables"]:
                    st.markdown(f"#### {clean_cite(tb['title'])}")
                    render_html_table(tb["columns"], tb["rows"], hide_kw=hide_kw)

                if act_content["experiments"]:
                    st.markdown("#### 🔬 核心實驗室")
                    for exp in act_content["experiments"]:
                        steps_html = "<br>".join([render_masked_text(s, hide_kw) for s in exp["steps"]])
                        exp_html = (
                            f'<div class="exp-card">'
                            f'<strong>{clean_cite(exp["title"])}</strong><br>'
                            f'🎯 <b>實驗目的：</b>{render_masked_text(exp["purpose"], hide_kw)}<br>'
                            f'🧪 <b>實驗流程：</b><br>{steps_html}<br>'
                            f'📊 <b>實驗結果：</b>{render_masked_text(exp["result"], hide_kw)}<br>'
                            f'💡 <b>實驗結論：</b>{render_masked_text(exp["conclusion"], hide_kw)}'
                            f'</div>'
                        )
                        st.markdown(exp_html, unsafe_allow_html=True)


# ==========================================
# 模式 2：全題型互動題庫闖關（支援四科切換、純填空輸入、SVG連連看）
# ==========================================
elif mode == "🎮 全題型題庫闖關（含連連看/填空）":
    st.header(f"🎮 {active_subject}｜全題型互動題庫闖關")

    all_units = sorted(list(set(q["unit"] for q in combined_quiz_bank)))
    all_types = ["全部題型", "連連看", "填充與代號題", "勾選題", "是非題", "選擇題"]

    c1, c2, c3 = st.columns([2.2, 1.3, 1])
    with c1:
        selected_quiz_units = st.multiselect("📂 選擇測驗單元範圍", all_units, default=all_units)
    with c2:
        q_type_filter = st.selectbox("🎯 題型篩選", all_types)
    with c3:
        num_q = st.number_input("抽題數量", min_value=1, max_value=100, value=10, step=5)

    filtered_pool = [
        q for q in combined_quiz_bank
        if q["unit"] in selected_quiz_units and (q_type_filter == "全部題型" or q["type"] == q_type_filter)
    ]

    quiz_state_key = f"active_quiz_{active_subject}"
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        if st.button("🎲 隨機抽題開始新測驗", use_container_width=True, type="primary"):
            sample_size = min(int(num_q), len(filtered_pool))
            st.session_state[quiz_state_key] = random.sample(filtered_pool, sample_size) if filtered_pool else []
    with col_b2:
        if st.button("📋 載入範圍內全部題目（依序作答）", use_container_width=True):
            st.session_state[quiz_state_key] = list(filtered_pool)

    if quiz_state_key not in st.session_state or not st.session_state[quiz_state_key]:
        sample_size = min(int(num_q), len(filtered_pool))
        st.session_state[quiz_state_key] = random.sample(filtered_pool, sample_size) if filtered_pool else []

    exam_questions = st.session_state[quiz_state_key]

    if not exam_questions:
        st.warning("⚠️ 此篩選條件下沒有題目，請調整單元或題型篩選！")
    else:
        form_prefix = f"quiz_{hashlib.md5(active_subject.encode()).hexdigest()[:4]}"
        for idx, item in enumerate(exam_questions):
            render_interactive_question(idx, item, form_prefix=form_prefix)
            st.divider()

        if st.button("✅ 交卷並自動批改錯誤", type="primary", use_container_width=True):
            correct_count = 0
            total_count = len(exam_questions)
            st.subheader("📊 自動批改結果與詳解")

            for idx, item in enumerate(exam_questions):
                is_ok, u_ans_str, c_ans_str = grade_interactive_question(idx, item, form_prefix=form_prefix)
                if is_ok:
                    correct_count += 1
                    st.success(
                        f"✔️ **第 {idx+1} 題【完全正確】！** ({item['type']})\n\n"
                        f"- 題目：{clean_cite(item['q'])}\n"
                        f"- 你的作答：`{u_ans_str}`\n"
                        f"- 💡 解析：{clean_cite(item.get('exp', ''))}"
                    )
                else:
                    st.error(
                        f"❌ **第 {idx+1} 題【有誤，已自動加入錯題本】！** ({item['type']})\n\n"
                        f"- 題目：{clean_cite(item['q'])}\n"
                        f"- 你的作答：`{u_ans_str}`\n"
                        f"- 🎯 **完整正確答案：`{c_ans_str}`**\n"
                        f"- 💡 解析：{clean_cite(item.get('exp', ''))}"
                    )
                    db_add_wrong_question(item, u_ans_str)

            final_score = round((correct_count / total_count) * 100)
            db_save_exam_history(f"{active_subject} 題庫闖關 ({q_type_filter})", final_score, correct_count, total_count, subject=active_subject)
            st.metric("🏆 本次測驗得分", f"{final_score} 分", f"答對 {correct_count} / {total_count} 題")
            if final_score == 100:
                st.balloons()


# ==========================================
# 模式 3：考卷 PDF 直接作答 ＆ 自動批改（支援四科考卷分類與純填空輸入）
# ==========================================
elif mode == "📄 考卷 PDF 直接作答＆自動批改":
    st.header("📄 考卷 PDF 匯入｜分科管理 ＋ 原卷消去解答 ＋ 全題型直接作答")
    st.info("💡 可上傳**自然、社會、數學、國語**任何科目的 PDF 考卷！系統會自動判斷科目（也可手動指定），並將考卷內的**填空題（保持純輸入框）、連連看、是非題、選擇題、勾選題**全部提取至右側供直接作答與自動批改。")

    c_up1, c_up2 = st.columns([1.2, 2.8])
    with c_up1:
        upload_subj_choice = st.selectbox("📥 上傳考卷的科目分類", ["🤖 依檔名自動判斷科目"] + SUBJECT_LIST)
    with c_up2:
        uploaded_files = st.file_uploader(
            "上傳一份或多份 PDF 考卷（自動永久存檔）",
            type=["pdf"],
            accept_multiple_files=True
        )

    if uploaded_files:
        new_count = 0
        for uf in uploaded_files:
            raw_bytes = uf.read()
            assigned_subj = detect_subject_from_name(uf.name) if upload_subj_choice == "🤖 依檔名自動判斷科目" else upload_subj_choice
            db_save_pdf(uf.name, raw_bytes, subject=assigned_subj)
            extracted_qs = parse_all_questions_from_pdf(raw_bytes, uf.name, subject=assigned_subj)
            db_replace_pdf_questions(uf.name, extracted_qs, subject=assigned_subj)
            new_count += 1
        if new_count > 0:
            st.success(f"🎉 成功儲存並解析 {new_count} 份 PDF 考卷！")
            st.rerun()

    filter_pdf_subj = st.radio(
        "📂 篩選要檢視／作答的考卷科目",
        ["🌟 全部科目"] + SUBJECT_LIST,
        horizontal=True
    )
    pdfs_filtered = db_load_all_pdfs(filter_pdf_subj)

    if not pdfs_filtered:
        st.warning(f"目前「{filter_pdf_subj}」尚無已上傳的 PDF 考卷，請從上方上傳檔案。")
    else:
        c_sel, c_pg, c_mask, c_reparse, c_del = st.columns([2.2, 0.9, 1.3, 1.2, 0.9])
        with c_sel:
            chosen_pdf_name = st.selectbox("📂 選擇已存檔的 PDF 考卷", list(pdfs_filtered.keys()))
        pdf_info = pdfs_filtered[chosen_pdf_name]
        pdf_bytes = pdf_info["bytes"]
        pdf_subj = pdf_info["subject"]
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)

        with c_pg:
            page_num = st.number_input(f"頁碼 (1~{total_pages})", min_value=1, max_value=total_pages, value=1)
        with c_mask:
            hide_ans_img = st.toggle("🪄 擦除紅/藍解答", value=True)
        with c_reparse:
            if st.button("🔄 重新解析本卷", use_container_width=True):
                re_qs = parse_all_questions_from_pdf(pdf_bytes, chosen_pdf_name, subject=pdf_subj)
                db_replace_pdf_questions(chosen_pdf_name, re_qs, subject=pdf_subj)
                st.toast("✅ 已重新解析本卷所有題目（填空保持純輸入框）！")
                st.rerun()
        with c_del:
            if st.button("🗑️ 刪除此卷", use_container_width=True):
                db_delete_pdf(chosen_pdf_name)
                st.rerun()

        col_img, col_ans_sheet = st.columns([1.1, 1.05])

        with col_img:
            st.subheader(f"📄 考卷畫面 `{pdf_subj}`（第 {page_num} 頁）")
            img_rendered = render_pdf_page_image(pdf_bytes, page_num - 1, hide_answers=hide_ans_img, zoom=2.2)
            st.image(img_rendered, use_container_width=True)

        with col_ans_sheet:
            st.subheader(f"✍️ 第 {page_num} 頁｜直接作答與自動批改區")

            fresh_page_qs = parse_all_questions_from_pdf(pdf_bytes, chosen_pdf_name, target_page=page_num - 1, subject=pdf_subj)
            all_saved_custom = db_load_custom_questions()
            saved_page_qs = [
                q for q in all_saved_custom
                if q.get("pdf_name") == chosen_pdf_name and int(q.get("page_num", 0)) == page_num
            ]

            if len(fresh_page_qs) >= len(saved_page_qs) and fresh_page_qs:
                db_replace_pdf_questions(chosen_pdf_name, fresh_page_qs, page_num=page_num, subject=pdf_subj)
                page_questions = fresh_page_qs
            else:
                page_questions = saved_page_qs if saved_page_qs else fresh_page_qs

            with st.expander("➕ 手動新增／補充本頁自訂題目（永久存檔）"):
                add_type = st.selectbox("選擇要新增的題型", ["填充與代號題", "連連看", "勾選題"], key="add_t")
                add_q_title = st.text_input("題目說明", value=f"第 {page_num} 頁・補充作答題", key="add_title")

                if add_type == "填充與代號題":
                    raw_blanks = st.text_area("每行輸入一格：`小題標籤:正確答案`", value="(1) 第1格:答案A\n(2) 第2格:答案B", key="add_blanks")
                    if st.button("💾 新增此純填空題", key="save_fill"):
                        blanks_list = []
                        for line in raw_blanks.strip().split("\n"):
                            if ":" in line or "：" in line:
                                parts = re.split(r'[:：]', line, maxsplit=1)
                                blanks_list.append({"label": parts[0].strip(), "ans": parts[1].strip()})
                        if blanks_list:
                            new_q = {
                                "subject": pdf_subj,
                                "unit": f"考卷：{chosen_pdf_name} (第{page_num}頁)",
                                "pdf_name": chosen_pdf_name,
                                "page_num": page_num,
                                "type": "填充與代號題",
                                "q": add_q_title,
                                "blanks": blanks_list,
                                "exp": f"{chosen_pdf_name} 第 {page_num} 頁補充題"
                            }
                            db_save_custom_questions([new_q], pdf_name=chosen_pdf_name, page_num=page_num, subject=pdf_subj)
                            st.rerun()

                elif add_type == "連連看":
                    raw_pairs = st.text_area("每行輸入一組正確配對：`左邊項目=右邊項目`", value="(1) 項目1=目標A\n(2) 項目2=目標B", key="add_match")
                    if st.button("💾 新增此連連看", key="save_match"):
                        l_list, r_list, ans_map = [], [], {}
                        for line in raw_pairs.strip().split("\n"):
                            if "=" in line:
                                l_str, r_str = [x.strip() for x in line.split("=", 1)]
                                l_list.append(l_str)
                                if r_str not in r_list:
                                    r_list.append(r_str)
                                ans_map[l_str] = r_str
                        if l_list:
                            new_q = {
                                "subject": pdf_subj,
                                "unit": f"考卷：{chosen_pdf_name} (第{page_num}頁)",
                                "pdf_name": chosen_pdf_name,
                                "page_num": page_num,
                                "type": "連連看",
                                "q": add_q_title,
                                "left_items": l_list,
                                "right_items": r_list,
                                "ans": ans_map,
                                "exp": f"{chosen_pdf_name} 第 {page_num} 頁連連看"
                            }
                            db_save_custom_questions([new_q], pdf_name=chosen_pdf_name, page_num=page_num, subject=pdf_subj)
                            st.rerun()

                elif add_type == "勾選題":
                    raw_opts = st.text_input("所有選項（用逗號分隔）", value="(1)選項1, (2)選項2, (3)選項3, (4)選項4", key="add_chk_opts")
                    raw_ans = st.text_input("應打 √ 的正確選項（用逗號分隔）", value="(1)選項1, (3)選項3", key="add_chk_ans")
                    if st.button("💾 新增此勾選題", key="save_chk"):
                        opts_l = [x.strip() for x in re.split(r'[,，]', raw_opts) if x.strip()]
                        ans_l = [x.strip() for x in re.split(r'[,，]', raw_ans) if x.strip()]
                        new_q = {
                            "subject": pdf_subj,
                            "unit": f"考卷：{chosen_pdf_name} (第{page_num}頁)",
                            "pdf_name": chosen_pdf_name,
                            "page_num": page_num,
                            "type": "勾選題",
                            "q": add_q_title,
                            "options": opts_l,
                            "ans": ans_l,
                            "exp": f"{chosen_pdf_name} 第 {page_num} 頁勾選題"
                        }
                        db_save_custom_questions([new_q], pdf_name=chosen_pdf_name, page_num=page_num, subject=pdf_subj)
                        st.rerun()

            if not page_questions:
                st.info("📌 本頁為重點課文頁或生字介紹頁（無測驗題目）。請切換至下一頁練習題進行作答，或展開上方「➕ 手動新增」自訂題目！")
            else:
                st.success(f"📋 本頁共載入 **{len(page_questions)}** 大題，請直接於下方填寫作答：")
                form_prefix = f"pdf_{hashlib.md5(chosen_pdf_name.encode()).hexdigest()[:6]}_p{page_num}"
                for idx, q_item in enumerate(page_questions):
                    render_interactive_question(idx, q_item, form_prefix=form_prefix)
                    st.divider()

                if st.button("🎯 送出本頁答案並自動批改", type="primary", use_container_width=True, key=f"grade_btn_{form_prefix}"):
                    c_cnt = 0
                    t_cnt = len(page_questions)
                    st.subheader("📊 本頁自動批改結果與完整解答")
                    for idx, q_item in enumerate(page_questions):
                        is_ok, u_str, c_str = grade_interactive_question(idx, q_item, form_prefix=form_prefix)
                        if is_ok:
                            c_cnt += 1
                            st.success(f"✔️ **第 {idx+1} 題【完全正確】！**\n- 你的作答：`{u_str}`\n- 💡 解析：{clean_cite(q_item.get('exp',''))}")
                        else:
                            st.error(
                                f"❌ **第 {idx+1} 題【有誤，已自動存入錯題本】！**\n"
                                f"- 你的作答：`{u_str}`\n"
                                f"- 🎯 **完整正確答案：`{c_str}`**\n"
                                f"- 💡 解析：{clean_cite(q_item.get('exp',''))}"
                            )
                            db_add_wrong_question(q_item, u_str)

                    page_score = round((c_cnt / t_cnt) * 100)
                    db_save_exam_history(f"{chosen_pdf_name} 第{page_num}頁", page_score, c_cnt, t_cnt, subject=pdf_subj)
                    st.metric("🏆 本頁得分", f"{page_score} 分", f"答對 {c_cnt} / {t_cnt} 大題")
                    if page_score == 100:
                        st.balloons()


# ==========================================
# 模式 4：錯題複習本 ＆ 永久備份管理（支援分科篩選）
# ==========================================
elif mode == "❌ 錯題複習本＆永久備份管理":
    st.header("❌ 專屬錯題複習本 ＆ 永久資料庫管理")

    subj_filter_wrong = st.radio(
        "📂 選擇要檢視的錯題與成績科目",
        ["🌟 全部科目"] + SUBJECT_LIST,
        horizontal=True
    )

    tab_wrong, tab_history, tab_backup = st.tabs([
        "📕 錯題本直接重測（答對自動消除）",
        "📈 歷史測驗成績紀錄",
        "💾 題庫與錯題永久備份檔 (匯出/匯入)"
    ])

    with tab_wrong:
        wrong_qs = db_load_wrong_questions(subj_filter_wrong)
        if not wrong_qs:
            st.success(f"🎉 太棒了！目前「{subj_filter_wrong}」錯題本是空的！")
        else:
            st.warning(f"📌 目前共有 **{len(wrong_qs)}** 題錯題（永久儲存在 SQLite 資料庫中）。在下方重新作答並批改答對後，就會自動從錯題本消除！")

            if st.button(f"🗑️ 一鍵清空「{subj_filter_wrong}」錯題紀錄"):
                db_clear_all_wrong_questions(subj_filter_wrong)
                st.rerun()

            st.divider()
            for idx, wq in enumerate(wrong_qs):
                st.caption(f"📕 科目：`{wq.get('subject','')}` ｜ 累計答錯 `{wq.get('_wrong_count', 1)}` 次 ｜ 上次錯誤作答：`{wq.get('_last_ans', '')}`")
                render_interactive_question(idx, wq, form_prefix="wrong_retry")
                c_check, c_del = st.columns([2, 1])
                with c_check:
                    if st.button(f"✅ 批改第 {idx+1} 題錯題", key=f"retry_btn_{idx}", type="primary", use_container_width=True):
                        is_ok, u_str, c_str = grade_interactive_question(idx, wq, form_prefix="wrong_retry")
                        if is_ok:
                            st.success(f"🎉 太棒了！第 {idx+1} 題答對了：`{u_str}`！已從錯題本移除！")
                            db_remove_wrong_question(wq["_qid"])
                            st.rerun()
                        else:
                            st.error(f"❌ 還是有誤喔！你的作答：`{u_str}`\n\n🎯 **完整正確答案：`{c_str}`**\n\n💡 解析：{clean_cite(wq.get('exp',''))}")
                with c_del:
                    if st.button(f"🗑️ 手動移除此題", key=f"del_wq_{idx}", use_container_width=True):
                        db_remove_wrong_question(wq["_qid"])
                        st.rerun()
                st.divider()

    with tab_history:
        history_list = db_load_exam_history(subj_filter_wrong)
        if not history_list:
            st.info("尚無測驗紀錄，快去題庫闖關或考卷作答試試看吧！")
        else:
            render_html_table(
                ["科目", "測驗名稱", "分數", "答對題數", "測驗時間"],
                [[h["科目"], h["測驗名稱"], h["分數"], h["答對題數"], h["測驗時間"]] for h in history_list]
            )

    with tab_backup:
        st.subheader("📦 四科完整學習資料一鍵備份／還原")
        backup_payload = {
            "custom_questions": db_load_custom_questions(),
            "wrong_questions": db_load_wrong_questions(),
            "exam_history": db_load_exam_history()
        }
        backup_json_str = json.dumps(backup_payload, ensure_ascii=False, indent=2)

        st.download_button(
            label="⬇️ 下載四科完整題庫＋錯題本＋成績備份檔 (.json)",
            data=backup_json_str.encode("utf-8"),
            file_name="grade3_all_subjects_backup.json",
            mime="application/json",
            use_container_width=True,
            type="primary"
        )

        st.divider()
        uploaded_backup = st.file_uploader("📥 上傳先前下載的 .json 備份檔進行還原", type=["json"])
        if uploaded_backup is not None:
            data = json.load(uploaded_backup)
            if "custom_questions" in data:
                db_save_custom_questions(data["custom_questions"])
            if "wrong_questions" in data:
                for wq in data["wrong_questions"]:
                    db_add_wrong_question(wq, wq.get("_last_ans", "備份還原"))
            st.success("🎉 備份資料已成功寫入永久資料庫！")
            st.rerun()
