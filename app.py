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
    page_title="小三自然科學｜重點複習＆全題型互動題庫",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_PATH = "science_persistent_store.db"


# ==========================================
# 1. SQLite 永久儲存資料庫引擎（重啟網頁不消失）
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 儲存上傳的 PDF 二進位檔案
    c.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_pdfs (
            filename TEXT PRIMARY KEY,
            pdf_bytes BLOB,
            upload_time TEXT
        )
    """)
    # 儲存匯入或自訂的題目
    c.execute("""
        CREATE TABLE IF NOT EXISTS custom_questions (
            q_id TEXT PRIMARY KEY,
            pdf_name TEXT,
            page_num INTEGER,
            q_json TEXT
        )
    """)
    # 儲存錯題本
    c.execute("""
        CREATE TABLE IF NOT EXISTS wrong_questions (
            q_id TEXT PRIMARY KEY,
            q_json TEXT,
            user_last_ans TEXT,
            wrong_count INTEGER DEFAULT 1,
            updated_at TEXT
        )
    """)
    # 儲存測驗歷史紀錄
    c.execute("""
        CREATE TABLE IF NOT EXISTS exam_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_title TEXT,
            score INTEGER,
            correct_count INTEGER,
            total_count INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_question_id(q_dict: dict) -> str:
    raw = f"{q_dict.get('unit','')}_{q_dict.get('type','')}_{q_dict.get('q','')}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def db_save_pdf(filename: str, pdf_bytes: bytes):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO uploaded_pdfs (filename, pdf_bytes, upload_time) VALUES (?, ?, ?)",
        (filename, sqlite3.Binary(pdf_bytes), datetime.now().strftime("%Y-%m-%d %H:%M"))
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


def db_load_all_pdfs() -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT filename, pdf_bytes FROM uploaded_pdfs ORDER BY upload_time DESC")
    rows = c.fetchall()
    conn.close()
    return {r[0]: bytes(r[1]) for r in rows}


def db_save_custom_questions(questions: list, pdf_name: str = "手動/PDF匯入", page_num: int = 0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for q in questions:
        qid = get_question_id(q)
        p_num = q.get("page_num", page_num)
        p_name = q.get("pdf_name", pdf_name)
        c.execute(
            "INSERT OR REPLACE INTO custom_questions (q_id, pdf_name, page_num, q_json) VALUES (?, ?, ?, ?)",
            (qid, p_name, p_num, json.dumps(q, ensure_ascii=False))
        )
    conn.commit()
    conn.close()


def db_load_custom_questions() -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT q_json FROM custom_questions")
    rows = c.fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]


def db_add_wrong_question(q_dict: dict, user_ans_str: str):
    qid = get_question_id(q_dict)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT wrong_count FROM wrong_questions WHERE q_id = ?", (qid,))
    row = c.fetchone()
    w_count = (row[0] + 1) if row else 1
    c.execute(
        "INSERT OR REPLACE INTO wrong_questions (q_id, q_json, user_last_ans, wrong_count, updated_at) VALUES (?, ?, ?, ?, ?)",
        (qid, json.dumps(q_dict, ensure_ascii=False), str(user_ans_str), w_count, datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()


def db_remove_wrong_question(qid: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM wrong_questions WHERE q_id = ?", (qid,))
    conn.commit()
    conn.close()


def db_clear_all_wrong_questions():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM wrong_questions")
    conn.commit()
    conn.close()


def db_load_wrong_questions() -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT q_id, q_json, user_last_ans, wrong_count, updated_at FROM wrong_questions ORDER BY updated_at DESC")
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        item = json.loads(r[1])
        item["_qid"] = r[0]
        item["_last_ans"] = r[2]
        item["_wrong_count"] = r[3]
        item["_updated_at"] = r[4]
        result.append(item)
    return result


def db_save_exam_history(title: str, score: int, correct_cnt: int, total_cnt: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO exam_history (exam_title, score, correct_count, total_count, created_at) VALUES (?, ?, ?, ?, ?)",
        (title, score, correct_cnt, total_cnt, datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()


def db_load_exam_history() -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT exam_title, score, correct_count, total_count, created_at FROM exam_history ORDER BY id DESC LIMIT 30")
    rows = c.fetchall()
    conn.close()
    return [{"測驗名稱": r[0], "分數": f"{r[1]} 分", "答對題數": f"{r[2]} / {r[3]}", "測驗時間": r[4]} for r in rows]


init_db()


# ==========================================
# 2. 動態大字體與 RWD 自適應樣式注入
# ==========================================
def apply_responsive_css(scale_pct: int):
    base_rem = round(scale_pct / 100.0, 2)
    title_rem = round(base_rem * 1.35, 2)
    card_rem = round(base_rem * 1.12, 2)

    st.markdown(f"""
    <style>
        /* 全域容器自適應 */
        .main .block-container {{
            padding-top: 1rem;
            padding-bottom: 3rem;
            padding-left: clamp(0.8rem, 3vw, 2.5rem);
            padding-right: clamp(0.8rem, 3vw, 2.5rem);
            max-width: 1280px;
        }}
        /* 全面放大一般文字、題目、選項、輸入框與表格字體 */
        html, body, [data-testid="stAppViewContainer"] {{
            font-size: {scale_pct}% !important;
        }}
        div[data-testid="stMarkdownContainer"] p,
        div[data-testid="stMarkdownContainer"] li {{
            font-size: {card_rem}rem !important;
            line-height: 1.75 !important;
        }}
        /* 放大單選 Radio 與多選 Checkbox 觸控區域與字體 */
        label[data-baseweb="radio"], label[data-baseweb="checkbox"] {{
            padding: 8px 12px !important;
            margin-bottom: 6px !important;
            background-color: #f8fafc;
            border: 1px solid #cbd5e1;
            border-radius: 10px;
            min-height: 48px;
            display: flex !important;
            align-items: center !important;
            transition: all 0.15s ease;
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
        /* 放大下拉選單與文字輸入框 */
        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] input {{
            font-size: {card_rem}rem !important;
            min-height: 48px !important;
            font-weight: 600 !important;
        }}
        /* 重點複習卡片與實驗卡片 */
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
            padding: 18px 20px;
            border-radius: 14px;
            margin-bottom: 14px;
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
        /* 按鈕放大與觸控友善 */
        div.stButton > button, div[data-testid="stFormSubmitButton"] > button {{
            border-radius: 12px !important;
            font-size: {card_rem}rem !important;
            font-weight: 700 !important;
            min-height: 52px !important;
            padding: 0.6rem 1.2rem !important;
        }}
        /* 表格字體放大 */
        .custom-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 12px 0 20px 0;
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
            padding: 12px 15px;
            font-weight: 700;
        }}
        .custom-table td {{
            padding: 12px 15px;
            border-bottom: 1px solid #e2e8f0;
            color: #1e293b;
            line-height: 1.6;
        }}
        .custom-table tr:nth-child(even) {{
            background-color: #f8fafc;
        }}
        /* 手機窄螢幕自適應微調 */
        @media (max-width: 768px) {{
            .main .block-container {{
                padding-left: 0.6rem !important;
                padding-right: 0.6rem !important;
            }}
            .q-box {{
                padding: 14px !important;
                font-size: {card_rem}rem !important;
            }}
            .custom-table th, .custom-table td {{
                padding: 8px 10px !important;
            }}
        }}
    </style>
    """, unsafe_allow_html=True)


def clean_cite(text: str) -> str:
    return re.sub(r'\s*\[cite:\s*[\d,\s]+\]', '', str(text))


def render_masked_text(text: str, hide_keywords: bool) -> str:
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
# 3. 連連看 SVG 動態畫線視覺化元件
# ==========================================
def render_svg_matching(left_items: list, right_items: list, user_pairs: dict, correct_pairs: dict = None, show_grading: bool = False):
    """
    動態繪製連連看左右配對與彩色連線 SVG，自適應手機與電腦寬度
    """
    n_rows = max(len(left_items), len(right_items))
    row_h = 68
    svg_h = n_rows * row_h + 24
    colors = ["#2563eb", "#d97706", "#7c3aed", "#059669", "#db2777", "#0891b2"]

    svg_lines = []
    # 左側項目區塊
    for i, l_text in enumerate(left_items):
        cy = 38 + i * row_h
        l_clean = clean_cite(l_text)
        svg_lines.append(
            f'<rect x="10" y="{cy-24}" width="260" height="48" rx="10" fill="#eff6ff" stroke="#3b82f6" stroke-width="2"/>'
            f'<text x="24" y="{cy+6}" font-size="16" font-weight="bold" fill="#1e3a8a">{l_clean[:16]}</text>'
            f'<circle cx="270" cy="{cy}" r="6" fill="#2563eb"/>'
        )

    # 右側項目區塊
    for j, r_text in enumerate(right_items):
        cy = 38 + j * row_h
        r_clean = clean_cite(r_text)
        svg_lines.append(
            f'<rect x="450" y="{cy-24}" width="320" height="48" rx="10" fill="#f0fdf4" stroke="#10b981" stroke-width="2"/>'
            f'<text x="466" y="{cy+6}" font-size="16" font-weight="bold" fill="#065f46">{r_clean[:20]}</text>'
            f'<circle cx="450" cy="{cy}" r="6" fill="#10b981"/>'
        )

    # 繪製使用者連線
    for i, l_text in enumerate(left_items):
        chosen_r = user_pairs.get(l_text)
        if chosen_r and chosen_r in right_items:
            j = right_items.index(chosen_r)
            y1 = 38 + i * row_h
            y2 = 38 + j * row_h
            if show_grading and correct_pairs:
                is_ok = (correct_pairs.get(l_text) == chosen_r)
                stroke_col = "#16a34a" if is_ok else "#dc2626"
                dash = "" if is_ok else 'stroke-dasharray="8,6"'
            else:
                stroke_col = colors[i % len(colors)]
                dash = ""
            svg_lines.append(
                f'<line x1="270" y1="{y1}" x2="450" y2="{y2}" stroke="{stroke_col}" stroke-width="4" {dash} stroke-linecap="round"/>'
            )

    svg_html = f"""
    <div style="width:100%; overflow-x:auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:12px; padding:10px; margin:8px 0;">
        <svg viewBox="0 0 780 {svg_h}" width="100%" height="{svg_h}" style="min-width:560px;">
            {''.join(svg_lines)}
        </svg>
    </div>
    """
    st.markdown(svg_html, unsafe_allow_html=True)


# ==========================================
# 4. 第一份《重點複習》100% 完整資料庫
# ==========================================
COMPLETE_REVIEW_DATA = {
    "單元1：多采多姿的植物": {
        "icon": "🌱",
        "activities": {
            "活動1：植物是什麼": {
                "points": [
                    "① 自然環境中包含有生命的【生物】和沒有生命的【非生物】（如石頭、水、空氣、陽光）；生物中有些是【動物】（如蜜蜂、蝴蝶、龜），有些是【植物】（如榕樹、長春花、朱槿）[cite: 1, 10, 31, 64]。",
                    "② 不同植物的身體外形各有不同，但大部分可分成【根】、【莖】、【葉】、【花】、【果實】和【種子】等六大構造，且不同構造各有不同的功能[cite: 1, 10]。",
                    "③ 圖解實例：【長春花（日日春）】具有粉紅色的【花】、綠色的【葉】與直立的【莖】、深入土裡的【根】，開花後結出細長的【果實】，果實成熟後會裂開露出裡面的【種子】[cite: 1]。",
                    "④ 觀察植物時須注意安全，可以用手摸樹幹觸感、用手掌比較葉子大小、用眼睛欣賞花朵形狀與顏色，但【絕對不可將不知名植物的果實或葉片放入口中嘗味道】[cite: 64, 86]。"
                ],
                "tables": [
                    {
                        "title": "📊 自然環境中的生物與非生物分類表（課本與評量實例）",
                        "columns": ["分類", "細項", "常見例子"],
                        "rows": [
                            ["生物（有生命，會長大）", "植物", "長春花（日日春）、榕樹、樟樹、朱槿、南美蟛蜞菊、仙丹花、牽牛花[cite: 1, 31, 64]"],
                            ["生物（有生命，會長大）", "動物", "蝴蝶、蜜蜂、小鳥、魚、龜、麻雀[cite: 12, 31, 64, 89]"],
                            ["非生物（沒有生命）", "自然或人造物", "石頭、水、海水、空氣、陽光、單槓、汽車[cite: 10, 31, 32, 64, 89]"]
                        ]
                    }
                ],
                "experiments": []
            },
            "活動2：植物如何獲取陽光和水": {
                "points": [
                    "① 植物構造中最明顯的部位是【葉子】，主要功能是【獲取陽光、製造養分】；不同植物的葉子有不同的【葉形】、【葉緣】和【葉脈】等特徵，能幫助分辨不同的植物[cite: 2, 10, 80]。",
                    "② 以【桂花葉】為例：【葉形】是葉子的形狀（如橢圓形、愛心形、手掌形、細長形）；【葉緣】是葉子的邊緣（如鋸齒狀、平滑完整）；【葉脈】是葉子上面的脈紋（分網狀脈與平行脈），可以【輸送水分和養分】；連接葉片與莖的部位稱為【葉柄】[cite: 2, 10, 65, 87, 89]。",
                    "③ 葉子在莖或枝條上的排列方式稱為【葉序】。葉子是從莖上的【節】長出來的，不同植物的葉子在節上生長的方式不同，可分為【互生】、【對生】和【輪生】[cite: 2, 10]。",
                    "④ 植物的枝條會相互【交錯】，且【向外、向上】長，盡量讓葉子都能照到更多【陽光】，以製造養分[cite: 2, 13]。",
                    "⑤ 植物的【莖】可以【支撐植物的身體】，讓植物往陽光的方向生長，以獲取更多的陽光；依形態可分成【木本莖】、【草本莖】和【藤本莖】[cite: 3, 10]。",
                    "⑥ 大部分植物的【根】都長在土裡，能【抓住土壤、固定植物】，幫助植物向上生長以獲取陽光，並【吸收土壤裡的水分】；依形態可分為【軸根】和【鬚根】[cite: 5, 6, 11, 13]。",
                    "⑦ 植物缺水時，葉子會下垂，將水澆在【土壤】上（而非葉子或莖上），由【根部】吸收水分，不久後葉子就會挺起來，恢復生氣[cite: 6, 12, 29, 65]。"
                ],
                "tables": [
                    {
                        "title": "🍃 葉子的排列方式（葉序）比較表",
                        "columns": ["葉序類型", "在「節」上的生長方式", "代表植物舉例"],
                        "rows": [
                            ["互生", "每個節上只長出 1 片葉子，左右交互排列", "榕樹、樟樹、朱槿[cite: 2, 10, 76, 87]"],
                            ["對生", "每個節上相對長出 2 片葉子", "桂花、青楓、福木、南美蟛蜞菊[cite: 2, 10, 76, 87]"],
                            ["輪生", "每個節上輪狀長出 3 片或以上的葉子", "黑板樹、軟枝黃蟬[cite: 2, 10, 76, 87]"]
                        ]
                    },
                    {
                        "title": "🍃 常見植物的葉脈與葉形補充表",
                        "columns": ["特徵分類", "形態說明", "代表植物舉例"],
                        "rows": [
                            ["網狀脈", "葉脈像互相交錯的網子一樣", "桂花、桑樹、樟樹、榕樹、甘藷[cite: 2, 65, 87]"],
                            ["平行脈", "葉脈是一條一條平行的直線", "百合、竹子、萬年青、棕葉狗尾草[cite: 65, 87, 89]"],
                            ["常見葉形", "愛心形／橢圓形／手掌形／細長形", "愛心形：甘藷；橢圓形：福木、榕樹；手掌形：構樹、青楓；細長形：萬年青、百合、棕葉狗尾草[cite: 29, 86, 89, 101]"]
                        ]
                    },
                    {
                        "title": "🪵 植物「莖」的三種形態比較表",
                        "columns": ["類型", "外形特徵", "代表植物與表面特徵"],
                        "rows": [
                            ["木本莖", "莖通常較粗壯，可以持續生長且長得較高，能獲得較多陽光", "小葉欖仁（莖表面有皮孔）、木棉（莖表面有瘤刺）、樟樹（莖表面有縱向裂紋，適合做木桌/家具）、榕樹、臺灣欒樹[cite: 3, 10, 13, 100]"],
                            ["草本莖", "莖通常較細，能支撐植物直立，會向四面八方長出較多枝條，獲取更多陽光", "長春花（綠色的莖）、大花咸豐草（綠色或略為紫色的莖）、昭和草[cite: 4, 10, 80]"],
                            ["藤本莖", "莖通常較柔軟，無法支撐植物直立，需要依靠其他物體（如支架、棚架）來攀爬，獲取更多陽光", "絲瓜（莖會產生卷鬚纏繞其他物體向上生長）、牽牛花（莖會纏繞在其他物體上向上生長）、小花蔓澤蘭[cite: 4, 10, 14, 98, 100]"]
                        ]
                    },
                    {
                        "title": "🥕 植物「根」的兩種形態比較表",
                        "columns": ["類型", "外形特徵", "代表植物舉例"],
                        "rows": [
                            ["軸根", "有一條比較粗的主根，旁邊有許多細小的根，可以深入土壤生長", "小白菜、菠菜、莧菜[cite: 5, 11]"],
                            ["鬚根", "全部都細細小小的，像鬍鬚一樣，在土壤的淺層生長", "蒜、蔥、稻子[cite: 5, 11, 89, 99]"]
                        ]
                    }
                ],
                "experiments": [
                    {
                        "title": "🔬 實驗：怎麼知道植物的根會吸水",
                        "purpose": "了解植物的根具有吸收水分的功能[cite: 6]。",
                        "steps": [
                            "1. 將植物的根放入裝水的透明容器中，再用【膠泥】封住容器口（防止水分自然蒸發影響實驗）。在【水位處】做記號，放到通風良好的地方[cite: 6]。",
                            "2. 一段時間後，觀察水位變化情形[cite: 6]。"
                        ],
                        "result": "比較實驗前、後的水位，發現實驗後容器內的【水位下降了】[cite: 6]。",
                        "conclusion": "植物生長所需的水是由【根部】吸收[cite: 6, 11]。"
                    }
                ]
            },
            "活動3：花、果實和種子有什麼功能": {
                "points": [
                    "① 一年四季都可以看到不同植物正在開花：春季【山櫻花、杜鵑】；夏季【金針花、荷花（蓮花）】；秋季【臺灣欒樹】；冬季【梅花】[cite: 7, 11]。",
                    "② 一朵花的基本構造包含：【花萼】（最外圍綠色像小葉片，保護與支撐花瓣）、【花瓣】、【雄蕊】（頂端有【花粉】）和【雌蕊】（位於花朵中央）（代表圖例：月橘、杜鵑、洋紫荊）[cite: 7, 11, 14, 81, 89]。",
                    "③ 有些花有【顏色鮮豔的花瓣】、【花蜜】、【特殊的氣味】等，能吸引蜜蜂、蝴蝶等動物前來採食，幫助植物【雄蕊的花粉傳到雌蕊】，讓植物能【繁衍後代】[cite: 7, 11, 31]。",
                    "④ 大部分植物開花後會結出【果實】，果實裡有【種子】（歷程：花 → 未成熟果實 → 成熟果實，如月橘花、番石榴花）[cite: 8, 11]。",
                    "⑤ 不同植物的果實構造、顏色、氣味和種子數量等都不太相同。果實可以【保護種子】，還能【幫助傳播種子】，讓種子在適合的地方長成新植物[cite: 8, 11]。",
                    "⑥ 【龍眼、木瓜、龍葵、蘋果】的果實芳香甜美，可以【吸引動物採食傳播】；【臺灣欒樹】的果實像氣球（還有蒲公英），可以【隨風飄揚傳播】[cite: 8, 12, 39, 87]。",
                    "⑦ 植物除了提供人們食、衣、住、行、育、樂各方面生活所需、美化環境，也能作為【動物的食物來源】、提供【動物棲息的環境】（如鳥築巢），要好好珍惜它們[cite: 9, 11]。",
                    "⑧ 素養延伸：【小花蔓澤蘭】是生長速度極快的【藤本莖】植物，會攀爬包覆樹木，使樹木【照不到陽光】無法製造養分而死亡；戶外看到時可將其【連根拔除】或【切斷它的莖】讓整株枯萎再清除[cite: 14]。【臺灣欒樹】為臺灣特有原生種行道樹，初秋開金黃色花（別稱【臺灣金雨樹】、【國慶樹】），果實像紅褐色小燈籠，成熟乾枯變褐色，因有綠葉、黃花、紅褐果、褐果四色變化，又稱【四色樹】[cite: 81]。"
                ],
                "tables": [
                    {
                        "title": "🏡 植物與人類生活的關係（食衣住行育樂）",
                        "columns": ["生活層面", "用途說明", "具體舉例"],
                        "rows": [
                            ["食", "有些植物可供人們食用", "稻子（白米飯）、柳橙（果汁）、萵苣、番石榴（吃果實）[cite: 9, 80, 99]"],
                            ["衣", "有些植物可供人們製作衣物", "棉花（棉衣）、亞麻[cite: 9, 12, 30]"],
                            ["住", "有些房屋和家具是用植物的「莖」（木材）做成", "木屋、木桌、木椅、木櫃（如樟樹）[cite: 9, 13, 30, 87]"],
                            ["行", "有些交通工具或步道是用植物的「莖」做成", "木棧道、拼板舟、木筏[cite: 9, 66]"],
                            ["育", "可以作為自然教學的場所", "植物園、校園[cite: 9, 66]"],
                            ["樂", "用來美化環境", "應用植物開花規律設計的花鐘、種植鮮豔植物[cite: 9, 66]"]
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
                    "① 生活中可以看見許多力的現象，例如踢足球、推動計分板、踩扁飲料罐、搬桌子等。力雖然看不見，但物體受力後可能有【形狀】或【運動狀態】的變化[cite: 15, 24, 67]。",
                    "② 物體受力時，運動狀態可能【變快】、【變慢】或【停止】（例如：用力踢靜止的球，球會往前滾動；用力擋住滾過來的球，球會停下來；球靜止在球場中央則無法觀察到受力的作用變化）[cite: 16, 24, 67]。",
                    "③ 表示對物體的施力情形時，包含【力的三要素】：【力的大小】（用【線段的長短】表示，線段越長代表力越大）、【力的方向】（用【箭頭】表示施力方向）、【力的作用點】（施力時碰觸到物體的位置）[cite: 16, 24, 67, 80]。",
                    "④ 以踢足球為例：①力的方向：朝向球門的方向（若沒注意方向則踢不進球門）；②力的大小：用力的大小（若力太小則在進球門前就停下）；③力的作用點：腳碰觸到足球的位置[cite: 16, 30, 67]。",
                    "⑤ 【壓球】時，球受到向下的力，【形狀可能改變，但不會移動位置】；【拋球】時，球受到向上的力，【會往上移動】[cite: 17, 27]。"
                ],
                "tables": [
                    {
                        "title": "💪 力的作用與物體變化整理表",
                        "columns": ["變化面向", "受力情形", "現象與舉例"],
                        "rows": [
                            ["物體形狀", "受力時不會改變", "推牆壁、拉竹筷[cite: 15, 24, 88]"],
                            ["物體形狀", "受力時改變，不再受力後「不會恢復」", "壓膠泥、踩扁飲料罐/鋁罐、捏陶土、折斷塑膠尺[cite: 15, 24, 80, 86]"],
                            ["物體形狀", "受力時改變，不再受力後「會恢復原狀」", "拉橡皮筋（過度用力仍會斷裂）、壓縮或拉伸彈簧[cite: 15, 24, 67, 80, 88]"],
                            ["運動狀態", "靜止→移動、變快、變慢、移動→停止", "踢飛靜止的球、球撞門柱反彈、捕手/守門員接住球使球停止[cite: 16, 24, 67, 88]"]
                        ]
                    }
                ],
                "experiments": [
                    {
                        "title": "🔬 實驗：物體受力形狀的變化",
                        "purpose": "了解物體受力時、不再受力後形狀的變化[cite: 15]。",
                        "steps": [
                            "用力推牆、壓膠泥、拉橡皮筋，以及停止用力時，觀察物體的形狀有沒有變化[cite: 15]。"
                        ],
                        "result": "1. 牆壁受力後形狀不會改變；膠泥、橡皮筋的形狀會改變。 2. 膠泥不再受力後形狀不會恢復，橡皮筋則會恢復原本的形狀[cite: 15]。",
                        "conclusion": "物體受力時，有些物體的形狀會改變。會改變形狀的物體不再受力後，有的形狀會恢復，有的不會[cite: 15, 24]。"
                    }
                ]
            },
            "活動2：磁力有什麼特性": {
                "points": [
                    "① 磁鐵具有【磁力】，可以吸住【鐵製的物品】（或含有鐵的物品），所以能將紙條吸在鐵製冰箱門或黑板上（代表冰箱門與黑板含有鐵，而非裡面一定有磁鐵），卻不能吸在木門上[cite: 17, 24, 26]。",
                    "② 注意：磁鐵【不是】能吸住所有的金屬製品（例如銅、鋁罐、十元硬幣等金屬就無法被磁鐵吸住）[cite: 17, 26, 92]。",
                    "③ 同一個磁鐵各部位的磁力強弱不一樣，磁力比較強的部位稱為【磁極】，每個磁鐵都有兩個磁極，分別是【N極】和【S極】（長條形與U形磁鐵在兩端，圓形磁鐵在圓形的上下兩面）[cite: 18, 24, 68]。將越多磁鐵組合在一起，磁力越強[cite: 101]。",
                    "④ 當不同磁鐵的磁極互相靠近時，會有【同極相斥】（N與N、S與S互相排斥）、【異極相吸】（N與S互相吸引）的現象[cite: 18, 19, 25]。",
                    "⑤ 利用磁鐵【隔著物品仍具有磁力】及【吸引鐵製品】的特性，可以將【鐵粉和沙土分離】；例如打翻鐵粉時，利用磁鐵【隔著塑膠袋】吸住鐵粉，再將塑膠袋翻過來，便能快速清理和回收鐵粉[cite: 19, 25, 26, 68]。"
                ],
                "tables": [
                    {
                        "title": "🧲 磁鐵能否吸住的物品分類表",
                        "columns": ["分類", "共同特性", "物品舉例"],
                        "rows": [
                            ["可以被磁鐵吸住", "鐵製品或含有鐵", "剪刀刀刃處、迴紋針、釘書針、長尾夾、鐵尺、鐵釘、鐵粉、美工刀片、冰箱門[cite: 17, 30, 91, 92, 93]"],
                            ["無法被磁鐵吸住", "非鐵製品（塑膠、木頭、紙、其他金屬）", "十元硬幣、鋁罐、彩色筆、色紙、報紙、橡皮擦、鉛筆、膠帶、塑膠尺、木塊、木屑、膠泥、橡皮筋[cite: 17, 90, 91, 92, 93]"]
                        ]
                    },
                    {
                        "title": "🛠️ 磁鐵特性在生活中的應用舉例",
                        "columns": ["應用的磁鐵特性", "生活物品與原理說明"],
                        "rows": [
                            ["吸引鐵製品", "磁吸式鉛筆盒（吸住盒蓋鐵片）、釣魚遊戲（吸住魚上鐵製螺絲）、門擋（吸住門後鐵片固定門）、磁性螺絲起子（前端磁鐵吸住鐵製螺絲釘）、迴紋針收納座、磁吸式跳棋（吸住含鐵跳棋底盤）、磁鐵掛鉤（吸在含鐵白板上固定物品）[cite: 20, 25]"],
                            ["異極相吸", "玩具火車車廂之間利用磁鐵異極相吸連接在一起[cite: 20, 25, 27]"],
                            ["同極相斥", "用另一個磁鐵的相同磁極靠近，可將桌上的磁鐵隔空推開[cite: 82, 101]"]
                        ]
                    }
                ],
                "experiments": [
                    {
                        "title": "🔬 實驗1：磁鐵能吸住哪些物品",
                        "purpose": "了解能被磁鐵吸住的物品共同特性[cite: 17]。",
                        "steps": [
                            "選出十元硬幣、彩色筆、色紙、橡皮擦、鉛筆、膠帶、剪刀、迴紋針、釘書針、長尾夾等物品，先推測再用磁鐵測試[cite: 17]。"
                        ],
                        "result": "可被吸住：剪刀刀刃處、迴紋針、釘書針、長尾夾；無法被吸住：十元硬幣、彩色筆、色紙、橡皮擦、鉛筆、膠帶[cite: 17]。",
                        "conclusion": "【鐵製的物品】才能被磁鐵吸住[cite: 17, 24]。"
                    },
                    {
                        "title": "🔬 實驗2：比較磁鐵不同部位的磁力強弱",
                        "purpose": "了解磁鐵不同部位的磁力強弱不同[cite: 18]。",
                        "steps": [
                            "手拿長條形磁鐵靠近迴紋針，觀察磁鐵不同部位吸住的迴紋針數量[cite: 18]。"
                        ],
                        "result": "同一個長條形磁鐵的【兩端】可以吸住較多迴紋針，磁力比較強[cite: 18]。",
                        "conclusion": "利用吸住迴紋針的數量可知磁力強弱；磁力較強的部位稱為【磁極】，分別是【N極】和【S極】[cite: 18, 24]。"
                    },
                    {
                        "title": "🔬 實驗3：比較磁極互相靠近時的現象",
                        "purpose": "了解兩個磁鐵的相同磁極或不同磁極互相靠近時的現象[cite: 19]。",
                        "steps": [
                            "1. 觀察兩個磁鐵的相同磁極（N-N、S-S）互相靠近。 2. 觀察兩個磁鐵的不同磁極（N-S）互相靠近[cite: 19]。"
                        ],
                        "result": "相同磁極互相靠近會【互相排斥】；不同磁極互相靠近會【互相吸引】[cite: 19]。",
                        "conclusion": "磁鐵的磁極具有【同極相斥、異極相吸】的特性[cite: 19, 25]。"
                    }
                ]
            },
            "活動3：還有什麼不一樣的力": {
                "points": [
                    "① 生活中除了磁力，還有【彈力】、【風力】、【浮力】等不同種類的力[cite: 21, 25]。",
                    "② 將物體放入水中，會受到【向上】的力（圖示箭頭朝上 ↑），稱為【浮力】；但如果浮力不夠時，物體便會沉入水中（注意：沉入水中的硬幣、彈珠、磁鐵等物體【仍然有受到水的浮力】，只是浮力不夠支撐）[cite: 21, 25, 26, 29, 69]。",
                    "③ 物體在水中的浮沉情形和【物體的形狀】有關；原本沉在水中的物體做成【容器形狀】（如船形、碗形、杯子形）後，比較容易浮在水面上[cite: 22, 25, 27]。",
                    "④ 水除了具有浮力，也可以使物體移動、【傳送動力】，例如：流動的水推動【水車轉動】、打掃時利用【水柱的力量】將地面髒汙沖走、玩【水槍射擊】時手的力量透過水槍中的水將空罐射倒[cite: 23, 25]。"
                ],
                "tables": [
                    {
                        "title": "🌊 生活中各種力的特性與應用舉例",
                        "columns": ["力的種類", "特性說明", "生活應用舉例"],
                        "rows": [
                            ["彈力", "利用伸縮彈簧或橡皮筋產生彈力", "原子筆利用彈簧彈力控制筆心縮回、橡皮筋綁住物品[cite: 21, 25]"],
                            ["風力", "吹動的風能推動物品", "風車轉動（風力發電）、風箏在空中飄揚、推動帆船/風帆前進、幫助蒲公英傳播種子[cite: 21, 25, 39, 72]"],
                            ["水的浮力", "讓物品在水中受到向上浮的力", "游泳浮板、馬桶水箱內的浮球、郵輪/輪船在海上航行、游泳圈、塑膠碗/便當盒/紙碗/紙船浮於水面[cite: 21, 25, 30, 69]"],
                            ["水傳送動力", "利用流動的水或水柱傳遞力量使物體移動", "流水推動水車扇葉持續轉動、強力水柱沖洗地面髒汙、壓水槍射倒空罐[cite: 23, 25, 31]"]
                        ]
                    }
                ],
                "experiments": [
                    {
                        "title": "🔬 實驗：觀察膠泥的浮沉",
                        "purpose": "了解原本會沉在水中的物體，改變形狀之後可以浮在水面上（探討「膠泥的形狀」對浮沉的影響）[cite: 22, 28]。",
                        "steps": [
                            "1. 準備幾塊【形狀與重量都相同】的膠泥[cite: 22, 28]。",
                            "2. 將膠泥都放入水中，確定每塊膠泥都會沉入水中後，再拿出來[cite: 22, 28]。",
                            "3. 改變膠泥的形狀（如捏成船形、碗形、杯子形、長條形、球形等），依序放入水中觀察[cite: 22, 27, 82]。"
                        ],
                        "result": "相同重量、不同形狀的膠泥，捏成長條形、球形、塊狀仍會沉入水底；做成【容器形狀】（船形、碗形、杯子形）比較容易浮在水面上[cite: 22, 27, 82]。",
                        "conclusion": "原本會沉在水中的物體，做成【容器形狀】後，會比較容易浮在水面上（生活應用：船）[cite: 22, 25, 28]。"
                    }
                ]
            }
        }
    },
    "單元3：奇妙的空氣": {
        "icon": "💨",
        "activities": {
            "活動1：空氣在哪裡": {
                "points": [
                    "① 地球上除了動物、植物等生物，還有非生物的物質，例如石頭、水（看得見）和【空氣】（看不見、沒有顏色、沒有氣味）等；我們呼吸需要空氣，周圍充滿了空氣[cite: 32, 41, 70]。",
                    "② 有些物品有縫隙，縫隙裡也有空氣的存在，例如【海綿】和【粉筆】，把這些物品放進水中（或在水中擠壓海綿），會有【氣泡】冒出來[cite: 33, 43]。",
                    "③ 空氣和石頭、水等物質一樣都【占有空間】，而且【沒有固定形狀】，能用來填充不同形狀的物品[cite: 34, 41]。",
                    "④ 填充空氣才能使用的物品：【游泳圈】、【籃球】、【輪胎】、【氣球】、【餅乾包裝】（充入氮氣使包裝鼓鼓的，可防止餅乾碎裂、減少水氣保持酥脆並防壞）、【氣泡袋】（避免物品碰撞損壞）、【汽車安全氣囊】（車禍時瞬間充氣減緩撞擊力保護駕駛）[cite: 34, 41, 62, 83]。",
                    "⑤ 使用時要【抽掉空氣】以免占據空間的物品：【衣物收納袋】（將袋裡的空氣排出，才能節省收納空間）[cite: 34, 41]。"
                ],
                "tables": [],
                "experiments": [
                    {
                        "title": "🔬 實驗1：怎樣知道塑膠袋裡裝了空氣",
                        "purpose": "了解空氣就在我們的周圍[cite: 32]。",
                        "steps": [
                            "1. 把塑膠袋口張開，來回揮動[cite: 32]。",
                            "2. 捏住袋口，用點力擠壓塑膠袋後，觀察現象[cite: 32]。",
                            "3. 捏住袋口放入水中，再稍微鬆開袋口並擠壓塑膠袋，觀察現象[cite: 32]。"
                        ],
                        "result": "裝了空氣、被捏住袋口的塑膠袋【鼓鼓的】（證明空氣占有空間），而且在水中擠壓塑膠袋時，袋口會有【氣泡】跑出來[cite: 32, 41, 43]。",
                        "conclusion": "空氣是無所不在的，在我們周圍充滿了空氣[cite: 32, 41]。"
                    },
                    {
                        "title": "🔬 實驗2：紙團溼了嗎",
                        "purpose": "察覺空氣占有空間[cite: 33]。",
                        "steps": [
                            "1. 把紙團塞緊在杯子底部[cite: 33]。",
                            "2. 把杯子倒過來，【垂直】壓入水箱底部[cite: 33]。",
                            "3. 再把杯子垂直拿出水面，並觀察紙團[cite: 33]。"
                        ],
                        "result": "杯子裡的紙團取出後【還是乾的】，沒有被水沾溼[cite: 34, 41]。",
                        "conclusion": "雖然空氣看不見，但是它和石頭、水等物質一樣，都【占有空間】[cite: 34, 41]。"
                    }
                ]
            },
            "活動2：空氣還有什麼特性": {
                "points": [
                    "① 我們可以從放風箏、玩風車、吹電風扇、搧扇子等生活現象察覺風的存在；【風是空氣流動所形成的現象】[cite: 35, 36, 42]。",
                    "② 【空氣流動越快，風就越強】；可從【頭髮或國旗/鯉魚旗飄揚的高度】（飄得越高風越強；煙囪的煙若直直往上飄代表沒有風）、【風車轉動的快慢】（轉得越快風越強）、【樹枝晃動程度】來判斷風的強弱[cite: 36, 42, 45, 71, 90]。",
                    "③ 空氣不但會流動形成風、占有空間、沒有固定形狀，還【可以被壓縮】（例如幫輪胎打滿氣後還能再多打一些氣進去；壓下堵住筒口的空注射筒活塞）[cite: 37, 42, 62]。",
                    "④ 利用空氣【會流動、占有空間、可以被壓縮】等特性，可以設計【空氣槍玩具】、【風車】，或用注射筒與胡蘿蔔片製作【簡易空氣發射器】（往前推活塞，筒內空氣被壓縮後【傳送力量、快速流動】，使胡蘿蔔片發射出去）[cite: 38, 42]。"
                ],
                "tables": [],
                "experiments": [
                    {
                        "title": "🔬 實驗1：空氣流動了",
                        "purpose": "了解空氣流動會形成風[cite: 35]。",
                        "steps": [
                            "1. 在注射筒前端黏貼一段紙條[cite: 35]。",
                            "2. 將空注射筒活塞拉至 20 毫升的位置，使注射筒充滿空氣[cite: 35]。",
                            "3. 再往前推注射筒活塞，觀察紙條的變化[cite: 35]。"
                        ],
                        "result": "往前推動注射筒的活塞，空氣從筒口流出來形成風，使【紙條飄動】[cite: 36]。",
                        "conclusion": "【風】是空氣流動所形成的現象[cite: 36, 42]。"
                    },
                    {
                        "title": "🔬 實驗2：空氣被擠壓的情形",
                        "purpose": "知道空氣可以被壓縮[cite: 37]。",
                        "steps": [
                            "1. 將空注射筒活塞拉至 20 毫升的位置[cite: 37]。",
                            "2. 把注射筒的筒口【抵住橡皮擦】（堵住筒口）[cite: 37]。",
                            "3. 用力壓下活塞，直到不能再往下壓為止[cite: 37]。",
                            "4. 放開活塞，觀察活塞移動情形（注意：此實驗過程沒有利用到「空氣流動形成風」的特性）[cite: 37, 44]。"
                        ],
                        "result": "壓下注射筒活塞，裡面的空氣會被壓縮；放開活塞後，活塞會【往上移動，回到原來的位置】[cite: 38, 60]。",
                        "conclusion": "空氣占有空間，【可以被壓縮】[cite: 38, 42]。"
                    }
                ]
            },
            "活動3：乾淨空氣重要嗎": {
                "points": [
                    "① 地球上的【動物和植物】都需要呼吸空氣才能生存[cite: 39, 42]。",
                    "② 空氣與風的其他用途：風可以幫助【蒲公英傳播種子】、推動【風帆/帆船】在水面上航行；充滿空氣的【游泳圈】可幫我們浮在水面上[cite: 39, 42]。",
                    "③ 造成【空氣汙染】的原因：1. 【砍伐森林】使地面缺乏植物覆蓋，遇到風造成塵土飛揚；2. 【燃燒稻草、垃圾和廢棄物】產生濃煙和難聞氣味；3. 【工廠和汽、機車排放的廢氣】等，都會使空氣品質變差，造成身體不舒服或生病[cite: 39, 60, 72]。",
                    "④ 【維護空氣品質】的作法：1. 多搭乘【大眾運輸工具】（如捷運、公車，減少使用汽機車排放廢氣）；2. 短程以【腳踏車】或走路作為交通工具；3. 用環保低汙染的方式發電（如【風力發電】）；4. 【少砍伐山林、多種樹】[cite: 40, 42, 72]。",
                    "⑤ 【空氣品質指標 (AQI)】是行政院環境部每天監測空氣中影響健康的氣體濃度後換算而成的指標值；學校會依當日等級放置空氣品質旗幟，做好防護措施[cite: 40]。"
                ],
                "tables": [
                    {
                        "title": "🚩 空氣品質指標 (AQI) 等級與校園因應防護措施表",
                        "columns": ["空氣品質等級", "對健康的影響與因應防護措施"],
                        "rows": [
                            ["良好", "戶外活動可安心[cite: 40]"],
                            ["普通", "戶外活動可安心[cite: 40]"],
                            ["敏感", "戶外活動要小心、敏感體質要留意[cite: 40]"],
                            ["不良", "避免劇烈運動，下課多待室內休息[cite: 40]"],
                            ["非常不良", "暫停戶外活動，外出須配戴口罩[cite: 40]"],
                            ["危害", "全部停止戶外活動，將門窗緊閉[cite: 40]"]
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
            "活動1：如何辨認廚房中的材料": {
                "points": [
                    "① 廚房裡的調味品和粉末材料，可以利用【眼睛（視覺）】看外觀顏色、【手（觸覺）】摸顆粒粗細觸感、以及【鼻子（嗅覺）】搓聞氣味來辨認特徵[cite: 46, 55, 61]。",
                    "② 安全守則：辨識未知粉末材料時，【絕對不可放入口中嘗味道】，也【不可直接湊近用力吸聞粉末】，以免發生危險[cite: 58, 61, 94]。",
                    "③ 【溶解】：將砂糖或食鹽加入水中後，顆粒慢慢看不見，與水均勻混合成為水溶液的現象，稱為溶解（例如黃砂糖完全溶解後，看不見顆粒，整杯糖水呈透明黃色；食鹽與小蘇打粉完全溶解後皆為透明無色水溶液）[cite: 47, 55, 73, 97]。",
                    "④ 溶解的生活應用：煮湯時加入【食鹽】調味、在檸檬汁或紅茶中加入【砂糖/方糖】、在開水中加入【維他命C發泡錠】、清潔餐具時在水中加入【小蘇打粉】、洗衣時在水中加入【洗衣粉】（反例：熱油炸薯條、可樂加冰塊、湯裡加蔥、鮮奶加麥片皆非溶解）[cite: 49, 55, 73, 78, 95, 97]。",
                    "⑤ 利用溶解特性【分離物質】：不小心將【食鹽和沙子】混在一起時，加水攪拌讓食鹽完全溶解成食鹽水溶液，而沙子不能溶解在水中，即可將沙子過濾分離出來[cite: 49, 55, 61]。"
                ],
                "tables": [
                    {
                        "title": "🧂 廚房五種調味品與粉末材料特徵＆溶解總表",
                        "columns": ["材料名稱", "用眼睛看（外觀）", "用手觸摸（觸感）", "搓聞有無氣味", "能否完全溶於水", "水溶液酸鹼性"],
                        "rows": [
                            ["砂糖", "黃色（或白色）、顆粒狀", "粗粗的", "有（甜甜氣味）", "能（完全溶解）", "中性[cite: 47, 48, 54, 95]"],
                            ["食鹽", "白色、顆粒狀", "粗粗的", "無", "能（完全溶解）", "中性[cite: 47, 48, 54]"],
                            ["小蘇打粉", "白色、粉末狀", "細細的", "無", "能（完全溶解）", "鹼性[cite: 47, 48, 54]"],
                            ["黑胡椒粒", "深棕色、顆粒狀", "粗粗的", "有（辛辣氣味）", "不能（有顆粒沉澱）", "無法判別[cite: 47, 48, 56, 95]"],
                            ["檸檬酸", "白色、顆粒狀", "粗粗的", "無", "能（完全溶解）", "酸性[cite: 47, 48, 54]"]
                        ]
                    }
                ],
                "experiments": [
                    {
                        "title": "🔬 實驗1：用感官觀察調味品和粉末材料",
                        "purpose": "知道不同調味品和粉末材料有不同的特性[cite: 46]。",
                        "steps": [
                            "分別利用眼睛、手和鼻子來看外觀、摸觸感和搓聞氣味，觀察砂糖、食鹽、小蘇打粉、黑胡椒粒、檸檬酸的特性[cite: 46]。"
                        ],
                        "result": "小蘇打粉摸起來細細的，其餘四種皆為粗粗的顆粒；砂糖與黑胡椒粒有氣味，食鹽、小蘇打粉與檸檬酸無氣味（注意：食鹽與檸檬酸外觀皆為白色粗顆粒、無氣味、皆能溶於水，需靠酸鹼指示劑如紫色高麗菜汁才能安全準確分辨）[cite: 47, 58]。",
                        "conclusion": "不同的調味品和粉末材料具有不同的特性[cite: 47]。"
                    },
                    {
                        "title": "🔬 實驗2：調味品和粉末材料在水中的溶解情形",
                        "purpose": "了解不同調味品和粉末材料在水中的溶解情形[cite: 48]。",
                        "steps": [
                            "1. 準備 5 個杯子，分別標示砂糖、食鹽、黑胡椒粒、小蘇打粉和檸檬酸[cite: 48]。",
                            "2. 用【量筒】分別量取 100 毫升的水倒入各杯中（讀取量筒水量時，眼睛要【平視水位中央最低處】的刻度，水量才準確）[cite: 48]。",
                            "3. 在杯中加入 1【平匙】對應的材料（用玻璃棒刮平湯匙上的材料即為平匙，可確保每次舀取的量相同）[cite: 48, 74]。",
                            "4. 用乾淨的攪拌棒分別攪拌 1 分鐘後，觀察溶解情形[cite: 48]。"
                        ],
                        "result": "【砂糖、食鹽、小蘇打粉和檸檬酸】能完全溶解在水中；【黑胡椒粒】不能完全溶解在水中[cite: 48, 55]。",
                        "conclusion": "有些物質能完全溶解在水中成為水溶液，有些不能；利用此特性可區分部分材料[cite: 48, 55]。"
                    }
                ]
            },
            "活動2：如何增加物質溶解的量": {
                "points": [
                    "① 煮湯時加入食鹽調味，湯裡溶解的食鹽越多，湯就會越鹹；但在固定的水量與水溫中，食鹽溶解的量是【有限的】[cite: 50, 51, 55]。",
                    "② 水的【溫度】會影響物質在水中溶解的量：水量相同時，【水的溫度較高】，食鹽溶不掉的顆粒量較少，在水中【溶解的量較多】（同理，紅茶裡砂糖沉澱時，加熱紅茶即可讓杯底砂糖繼續溶解）[cite: 51, 52, 56, 57]。",
                    "③ 除了【提高水溫】，【增加水量】也可以增加物質在水中溶解的量[cite: 52, 56]。",
                    "④ 當杯底有溶不掉的食鹽顆粒時，即使持續不斷【攪拌】，也【無法】讓食鹽繼續溶解；攪拌的目的只是幫助食鹽均勻分散到水中，【加快食鹽溶解的速度】[cite: 52, 56]。"
                ],
                "tables": [],
                "experiments": [
                    {
                        "title": "🔬 實驗1：食鹽溶解的量",
                        "purpose": "了解食鹽在水中溶解的量是有限的[cite: 50]。",
                        "steps": [
                            "1. 用量筒量取 50 毫升的常溫水，倒入燒杯中[cite: 50]。",
                            "2. 加入 1 平匙食鹽到水中，攪拌至【完全溶解後】，在紀錄表上畫記 1 平匙[cite: 50, 74]。",
                            "3. 重複步驟 2，直到杯底有【溶不掉的食鹽顆粒】出現，即停止畫記[cite: 50]。"
                        ],
                        "result": "當加到第【4】平匙時，杯中開始有溶不掉的食鹽顆粒出現，因此 50 毫升常溫水中最多可以完全溶解【3】平匙的食鹽[cite: 51, 58]。",
                        "conclusion": "大多數可溶解在水中的物質，溶解的量是【有限的】[cite: 51, 55]。"
                    },
                    {
                        "title": "🔬 實驗2：不同水溫對食鹽溶解的影響",
                        "purpose": "了解食鹽在溫度較高的水中可以溶解的量較多[cite: 51]。",
                        "steps": [
                            "1. 在裝有 50 毫升【常溫水】的燒杯，以及裝有 50 毫升【熱水】的燒杯中，分別加入固定的食鹽量（例如 4 平匙）[cite: 51]。",
                            "2. 持續攪拌兩杯，直到杯底的食鹽顆粒無法繼續溶解，比較兩杯杯底食鹽顆粒的量[cite: 51]。"
                        ],
                        "result": "常溫水的杯底溶不掉的食鹽顆粒較多；熱水的杯底溶不掉的食鹽顆粒較少，表示食鹽在【熱水】中溶解的量比常溫水多[cite: 52]。",
                        "conclusion": "溫度會影響物質在水中溶解的量，水溫較高，在水中溶解的量較多[cite: 52, 56]。"
                    }
                ]
            },
            "活動3：怎麼辨認水溶液的酸鹼": {
                "points": [
                    "① 調味品和粉末材料完全溶解在水中成為水溶液後，可依性質分為【酸性】、【中性】和【鹼性】水溶液；不能完全溶解在水中的物質（如黑胡椒粒），則無法判別出酸鹼性[cite: 52, 56]。",
                    "② 雖然酸性水溶液通常聞起來酸酸的，但【只用感官無法準確判斷水溶液的酸鹼性】[cite: 52, 56]。",
                    "③ 利用【紫色高麗菜汁】遇到不同酸鹼性的水溶液會改變顏色的特性，可以準確判斷水溶液的酸鹼性：遇到【酸性】變【偏紅色】；遇到【中性】維持【紫色】；遇到【鹼性】變【偏藍綠色】[cite: 52, 54, 56]。",
                    "④ 除了紫色高麗菜，還有【蝶豆花瓣】、【紫色葡萄皮】、【紅鳳菜葉】等植物汁液，在不同酸鹼性的水溶液中也會呈現不同的顏色[cite: 54]。",
                    "⑤ 素養延伸：將【蝶豆花瓣汁】包入晶球製成變色晶球飲料，放入【冰水、砂糖水】（中性）呈【藍色】；放入【汽水、檸檬汁】（酸性）呈【紫紅色】；放入【小蘇打水】（鹼性）呈【綠色】。若要讓晶球有甜味又不變色，可加入【砂糖】調味[cite: 59]。"
                ],
                "tables": [
                    {
                        "title": "🟣 紫色高麗菜汁＆蝶豆花晶球在各水溶液中的顏色變化表",
                        "columns": ["水溶液酸鹼性", "代表水溶液名稱", "加入紫色高麗菜汁後的顏色", "蝶豆花晶球呈現顏色"],
                        "rows": [
                            ["酸性水溶液", "醋、檸檬酸水、檸檬汁、汽水", "否（變成偏紅色）", "紫紅色[cite: 54, 56, 59]"],
                            ["中性水溶液", "食鹽水、砂糖水、冰水", "是（維持原本紫色）", "藍色[cite: 54, 56, 59]"],
                            ["鹼性水溶液", "小蘇打水", "否（變成偏藍綠色）", "綠色[cite: 54, 56, 59]"]
                        ]
                    }
                ],
                "experiments": [
                    {
                        "title": "🔬 實驗：紫色高麗菜汁顏色與酸鹼的關係",
                        "purpose": "了解紫色高麗菜汁加入不同酸鹼性的水溶液時，顏色變化的情形[cite: 53]。",
                        "steps": [
                            "1. 製作紫色高麗菜汁：將紫色高麗菜【切成細絲】，用【熱水】浸泡一段時間，待汁液冷卻後倒出來【過濾】，即為紫色的紫色高麗菜汁[cite: 53, 75]。",
                            "2. 準備醋、檸檬酸水、食鹽水、砂糖水和小蘇打水，分別放入調色盤中[cite: 53]。",
                            "3. 【滴管的使用方法】：①先輕壓滴管的吸頭，再將滴管放入紫色高麗菜汁中 → ②緩慢放開吸頭，吸取紫色高麗菜汁 → ③輕壓吸頭，讓汁液滴入各水溶液中觀察顏色[cite: 53]。"
                        ],
                        "result": "醋、檸檬酸水變成【偏紅色】；食鹽水、砂糖水【維持紫色】；小蘇打水變成【偏藍綠色】[cite: 54]。",
                        "conclusion": "紫色高麗菜汁在酸性和鹼性水溶液中會呈現不同顏色，可利用此特性來判斷水溶液的酸鹼性[cite: 54, 56]。"
                    }
                ]
            }
        }
    }
}


# ==========================================
# 5. 全題型互動題庫（是非、選擇、連連看、填充/代號、勾選題）
# ==========================================
DEFAULT_QUIZ_BANK = [
    # --- 連連看配對題 (Matching) ---
    {
        "unit": "單元1：多采多姿的植物",
        "type": "連連看",
        "q": "【做一做・連連看】下列植物的葉子在莖或枝條上的生長情形（葉序）分別是哪一種？請配對連線：",
        "left_items": ["(1) 朱槿", "(2) 軟枝黃蟬", "(3) 福木", "(4) 黑板樹", "(5) 南美蟛蜞菊", "(6) 樟樹"],
        "right_items": ["互生（一節1片）", "對生（一節2片）", "輪生（一節3片以上）"],
        "ans": {
            "(1) 朱槿": "互生（一節1片）",
            "(2) 軟枝黃蟬": "輪生（一節3片以上）",
            "(3) 福木": "對生（一節2片）",
            "(4) 黑板樹": "輪生（一節3片以上）",
            "(5) 南美蟛蜞菊": "對生（一節2片）",
            "(6) 樟樹": "互生（一節1片）"
        },
        "exp": "朱槿、樟樹、榕樹為互生；福木、南美蟛蜞菊、桂花、青楓為對生；軟枝黃蟬、黑板樹為輪生[cite: 2, 76, 87]。"
    },
    {
        "unit": "單元1：多采多姿的植物",
        "type": "連連看",
        "q": "【做一做・連連看】下列各種植物的葉形特徵是什麼？請配對連線：",
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
        "exp": "甘藷葉像愛心；福木、榕樹葉呈橢圓形；萬年青、百合、棕葉狗尾草葉子細細長長的（且為平行脈）[cite: 89]。"
    },
    {
        "unit": "單元2：生活中的力",
        "type": "連連看",
        "q": "【做一做・連連看】生活中有許多不同種類的力，下列情境分別是利用什麼力？請配對連線：",
        "left_items": ["(1) 輪船在海上航行", "(2) 風箏在空中飛", "(3) 原子筆筆心縮回", "(4) 跳棋吸在鐵製棋盤上"],
        "right_items": ["甲. 磁力", "乙. 水的浮力", "丙. 風力", "丁. 彈力"],
        "ans": {
            "(1) 輪船在海上航行": "乙. 水的浮力",
            "(2) 風箏在空中飛": "丙. 風力",
            "(3) 原子筆筆心縮回": "丁. 彈力",
            "(4) 跳棋吸在鐵製棋盤上": "甲. 磁力"
        },
        "exp": "輪船利用水的浮力；風箏利用風力；原子筆彈簧利用彈力；磁吸式跳棋利用磁力[cite: 20, 21, 77]。"
    },
    {
        "unit": "單元3：奇妙的空氣",
        "type": "連連看",
        "q": "【重點複習・連連看】下列物品或活動分別應用了空氣的什麼特性？請配對連線：",
        "left_items": ["① 餅乾包裝鼓鼓的", "② 放風箏", "③ 空氣槍玩具", "④ 玩風帆"],
        "right_items": ["A. 空氣占有空間", "B. 空氣可以被壓縮", "C. 空氣流動形成風"],
        "ans": {
            "① 餅乾包裝鼓鼓的": "A. 空氣占有空間",
            "② 放風箏": "C. 空氣流動形成風",
            "③ 空氣槍玩具": "B. 空氣可以被壓縮",
            "④ 玩風帆": "C. 空氣流動形成風"
        },
        "exp": "餅乾包裝充氣防碎應用空氣占有空間；空氣槍玩具應用空氣可被壓縮；放風箏與玩風帆應用空氣流動形成風[cite: 44]。"
    },
    {
        "unit": "單元4：廚房裡的科學",
        "type": "連連看",
        "q": "【做一做・連連看】將小蘇打粉、食鹽、醋、檸檬酸製成水溶液，分別加入紫色高麗菜汁後會呈現什麼顏色？",
        "left_items": ["(1) 小蘇打粉", "(2) 食鹽", "(3) 醋", "(4) 檸檬酸"],
        "right_items": ["甲. 紫色（中性）", "乙. 偏紅色（酸性）", "丙. 偏藍綠色（鹼性）"],
        "ans": {
            "(1) 小蘇打粉": "丙. 偏藍綠色（鹼性）",
            "(2) 食鹽": "甲. 紫色（中性）",
            "(3) 醋": "乙. 偏紅色（酸性）",
            "(4) 檸檬酸": "乙. 偏紅色（酸性）"
        },
        "exp": "紫色高麗菜汁遇酸性（醋、檸檬酸）變偏紅色，遇中性（食鹽、砂糖）維持紫色，遇鹼性（小蘇打粉）變偏藍綠色[cite: 54, 85]。"
    },
    {
        "unit": "單元4：廚房裡的科學",
        "type": "連連看",
        "q": "【做一做・連連看】下列廚房調味品的外觀、觸感與氣味描述，請配對連出正確答案：",
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
        "exp": "黃砂糖呈黃色粗顆粒且有甜味；黑胡椒粒呈深棕色粗顆粒且有辛辣味；食鹽呈白色粗顆粒且無氣味[cite: 47, 95]。"
    },

    # --- 填充與代號題 (Fill-in / Code Matching) ---
    {
        "unit": "單元1：多采多姿的植物",
        "type": "填充與代號題",
        "q": "【做一做・分類與代號】請回答下列植物根的形態與小花蔓澤蘭素養題：",
        "blanks": [
            {"label": "(1) 小白菜的根屬於哪一種形態？", "options": ["軸根（打√）", "鬚根（畫○）"], "ans": "軸根（打√）"},
            {"label": "(2) 蒜的根屬於哪一種形態？", "options": ["軸根（打√）", "鬚根（畫○）"], "ans": "鬚根（畫○）"},
            {"label": "(3) 蔥的根屬於哪一種形態？", "options": ["軸根（打√）", "鬚根（畫○）"], "ans": "鬚根（畫○）"},
            {"label": "(4) 莧菜的根屬於哪一種形態？", "options": ["軸根（打√）", "鬚根（畫○）"], "ans": "軸根（打√）"},
            {"label": "(5) 小花蔓澤蘭的莖能沿著樹幹向上攀爬生長，屬於什麼莖？（可直接輸入或選擇）", "options": None, "ans": "藤本"}
        ],
        "exp": "小白菜、菠菜、莧菜為軸根；蔥、蒜、稻子為鬚根。小花蔓澤蘭會攀爬樹木，屬於藤本莖[cite: 5, 13, 14]。"
    },
    {
        "unit": "單元1：多采多姿的植物",
        "type": "填充與代號題",
        "q": "【做一做・花朵構造與植物用途配對】請選出對應的花構造代號與植物生活產品：",
        "blanks": [
            {"label": "(1) 位在花朵最外圍，通常是綠色，具有保護和支撐花瓣的功能：", "options": ["甲. 花瓣", "乙. 花粉", "丙. 花萼", "丁. 雌蕊"], "ans": "丙. 花萼"},
            {"label": "(2) 有些有鮮豔的顏色，能吸引動物前來：", "options": ["甲. 花瓣", "乙. 花粉", "丙. 花萼", "丁. 雌蕊"], "ans": "甲. 花瓣"},
            {"label": "(3) 通常位於花朵中央，被雄蕊圍在中心：", "options": ["甲. 花瓣", "乙. 花粉", "丙. 花萼", "丁. 雌蕊"], "ans": "丁. 雌蕊"},
            {"label": "(4) 位於雄蕊的頂端，傳到雌蕊上能繁殖後代：", "options": ["甲. 花瓣", "乙. 花粉", "丙. 花萼", "丁. 雌蕊"], "ans": "乙. 花粉"},
            {"label": "(5) 家具（木桌、木椅）主要是由哪種植物的莖製成？", "options": ["A. 稻子", "B. 棉花", "C. 柳橙", "D. 樟樹"], "ans": "D. 樟樹"},
            {"label": "(6) 衣服主要是由哪種植物製成？", "options": ["A. 稻子", "B. 棉花", "C. 柳橙", "D. 樟樹"], "ans": "B. 棉花"}
        ],
        "exp": "花萼在最外圍保護花瓣；雄蕊頂端有花粉；雌蕊在中央。樟樹可做家具，棉花可做衣服[cite: 7, 9, 81, 99]。"
    },
    {
        "unit": "單元2：生活中的力",
        "type": "填充與代號題",
        "q": "【做一做・火車磁鐵與力的三要素填空】B車廂左端(②號)為 N 極，右端(③號)為 S 極，請回答下列問題：",
        "blanks": [
            {"label": "(1) 火車頭和車廂彼此要能成功連接，是利用磁鐵的哪一種特性？", "options": ["① 同極相斥", "② 異極相吸"], "ans": "② 異極相吸"},
            {"label": "(2) 想要讓 A 火車頭(①號)與 B 車廂左端(②號 N 極)連接，①號磁鐵右側應為什麼極？（輸入 S 或 N）", "options": ["S", "N"], "ans": "S"},
            {"label": "(3) 想要讓 B 車廂右端(③號 S 極)與 C 車廂左端(④號)連接，④號磁鐵左側應為什麼極？（輸入 S 或 N）", "options": ["S", "N"], "ans": "N"},
            {"label": "(4) 踢足球時，腳碰觸到球的位置，稱為什麼？（輸入：力的作用點 / 力的方向 / 力的大小）", "options": ["力的作用點", "力的方向", "力的大小"], "ans": "力的作用點"},
            {"label": "(5) 在力的圖示中，我們可以用「線段的長短」來表示什麼？", "options": ["力的作用點", "力的方向", "力的大小"], "ans": "力的大小"}
        ],
        "exp": "異極相吸：②號是 N 極，故①號右側為 S 極；③號是 S 極，故④號左側為 N 極。線段長短代表力的大小，箭頭代表力的方向，接觸位置代表力的作用點[cite: 16, 27, 28, 67, 87]。"
    },
    {
        "unit": "單元4：廚房裡的科學",
        "type": "填充與代號題",
        "q": "【做一做・食鹽溶解實驗與未知粉末辨識】請根據實驗結果填入正確答案：",
        "blanks": [
            {"label": "(1) 在 50 毫升常溫水中，加入第 4 平匙食鹽攪拌後開始有溶不掉的顆粒出現，代表最多可完全溶解幾平匙食鹽？（輸入數字）", "options": None, "ans": "3"},
            {"label": "(2) 當在水中加入第幾平匙攪拌後，杯底開始有溶不掉的食鹽顆粒出現？（輸入數字）", "options": None, "ans": "4"},
            {"label": "(3) 白色、摸起來粗粗的、搓聞無氣味的廚房材料代號是？（甲.檸檬酸 / 乙.小蘇打粉 / 丙.黑胡椒粒）", "options": ["甲. 檸檬酸", "乙. 小蘇打粉", "丙. 黑胡椒粒"], "ans": "甲. 檸檬酸"},
            {"label": "(4) 深棕色、摸起來粗粗的、搓聞有氣味的廚房材料代號是？", "options": ["甲. 檸檬酸", "乙. 小蘇打粉", "丙. 黑胡椒粒"], "ans": "丙. 黑胡椒粒"},
            {"label": "(5) 白色、摸起來細細的、搓聞無氣味的廚房材料代號是？", "options": ["甲. 檸檬酸", "乙. 小蘇打粉", "丙. 黑胡椒粒"], "ans": "乙. 小蘇打粉"}
        ],
        "exp": "50毫升常溫水加到第 4 平匙出現沉澱，故最多完全溶解 3 平匙。檸檬酸為白色粗顆粒無氣味，黑胡椒粒為深棕色粗顆粒有氣味，小蘇打粉為白色細粉末無氣味[cite: 51, 58, 59]。"
    },

    # --- 勾選題 / 複選題 (Checkboxes 打√) ---
    {
        "unit": "單元2：生活中的力",
        "type": "勾選題",
        "q": "【做一做・打√勾選題】下列生活中的物品，哪些可以被磁鐵吸住？請將正確的全部打 √：",
        "options": ["(1) 長尾夾", "(2) 橡皮擦", "(3) 迴紋針", "(4) 膠帶", "(5) 剪刀刀刃", "(6) 釘書針", "(7) 十元硬幣", "(8) 鋁罐"],
        "ans": ["(1) 長尾夾", "(3) 迴紋針", "(5) 剪刀刀刃", "(6) 釘書針"],
        "exp": "長尾夾、迴紋針、剪刀刀刃、釘書針、鐵釘、美工刀片為鐵製品，可被磁鐵吸住；橡皮擦、膠帶、十元硬幣、鋁罐不含鐵，無法被吸住[cite: 17, 83, 91, 93]。"
    },
    {
        "unit": "單元2：生活中的力",
        "type": "勾選題",
        "q": "【期中評量・打√勾選題】下列哪些情境屬於「水傳送動力」的應用？請在 □ 中打 √：",
        "options": ["① 馬桶水箱內的浮球", "② 用強力水柱清洗地面", "③ 水車轉動", "④ 郵輪在海上航行"],
        "ans": ["② 用強力水柱清洗地面", "③ 水車轉動"],
        "exp": "強力水柱沖洗地面與水車轉動（以及水槍射倒空罐）屬於水傳送動力；浮球與郵輪屬於水的浮力應用[cite: 23, 31]。"
    },
    {
        "unit": "單元3：奇妙的空氣",
        "type": "勾選題",
        "q": "【期末評量・打√勾選題】兩個人利用打氣筒幫輪胎打氣：「輪胎打氣後就不再是扁扁的」、「氣打滿之後可以再多打一些氣進去，輪胎才有彈性」。從對話中可以發現空氣的哪些特性？",
        "options": ["① 空氣占有空間", "② 空氣沒有固定形狀", "③ 空氣流動形成風", "④ 空氣可以被壓縮"],
        "ans": ["① 空氣占有空間", "② 空氣沒有固定形狀", "④ 空氣可以被壓縮"],
        "exp": "輪胎打氣後不再扁扁的證明空氣占有空間且沒有固定形狀；打滿後還能再多打氣進去證明空氣可以被壓縮[cite: 62]。"
    },
    {
        "unit": "單元3：奇妙的空氣",
        "type": "勾選題",
        "q": "【做一做・打√勾選題】下列哪些是可以幫助維護空氣品質的作法？請打 √：",
        "options": ["① 燃燒稻草與垃圾", "② 大量砍伐森林", "③ 騎腳踏車上學", "④ 利用風力發電（環保低汙染發電）", "⑤ 多搭乘大眾運輸工具"],
        "ans": ["③ 騎腳踏車上學", "④ 利用風力發電（環保低汙染發電）", "⑤ 多搭乘大眾運輸工具"],
        "exp": "騎腳踏車、使用風力發電、搭乘大眾運輸工具與多種樹皆有助於維護空氣品質[cite: 40, 45, 72]。"
    },
    {
        "unit": "單元4：廚房裡的科學",
        "type": "勾選題",
        "q": "【做一做・打√勾選題】下列哪些物質可以完全溶解在水中成為水溶液？請打 √：",
        "options": ["(1) 沙子", "(2) 檸檬酸", "(3) 砂糖", "(4) 食鹽", "(5) 黑胡椒粒", "(6) 小蘇打粉"],
        "ans": ["(2) 檸檬酸", "(3) 砂糖", "(4) 食鹽", "(6) 小蘇打粉"],
        "exp": "檸檬酸、砂糖、食鹽、小蘇打粉能完全溶解在水中；沙子與黑胡椒粒不能溶解在水中[cite: 48, 73]。"
    },

    # --- 是非題與選擇題 (True/False & Multiple Choice) ---
    {"unit": "單元1：多采多姿的植物", "type": "是非題", "q": "葉子是從莖上的節長出來，每種植物都會從一個節長出 2 片葉子。", "options": ["○", "×"], "ans": "×", "exp": "不同植物在節上生長葉子的方式不同，分為互生（1片）、對生（2片）和輪生（3片以上）[cite: 2, 12]。"},
    {"unit": "單元1：多采多姿的植物", "type": "是非題", "q": "大花咸豐草是草本莖，莖較細小但能直立，會向四面八方生長，幫助獲取陽光。", "options": ["○", "×"], "ans": "○", "exp": "大花咸豐草與長春花皆屬於草本莖，莖較細但能直立[cite: 4, 12]。"},
    {"unit": "單元1：多采多姿的植物", "type": "是非題", "q": "莧菜和蒜的根長得不一樣，莧菜有一條比較粗的主根；蒜的根像鬍鬚一樣，細細小小的。", "options": ["○", "×"], "ans": "○", "exp": "小白菜、菠菜、莧菜為軸根；蔥、蒜、稻子為鬚根[cite: 5, 12, 89]。"},
    {"unit": "單元1：多采多姿的植物", "type": "是非題", "q": "植物缺水時，葉子會下垂枯萎，只要將水澆在葉子上，植物就能恢復生氣。", "options": ["○", "×"], "ans": "×", "exp": "植物生長所需的水是由「根部」吸收，澆水時應澆在土壤上由根吸收[cite: 6, 12, 29]。"},
    {"unit": "單元1：多采多姿的植物", "type": "選擇題", "q": "黑板樹的葉子在枝條同一個節上長出多片葉子，請問它屬於哪一種生長方式？", "options": ["①互生", "②對生", "③輪生", "④單生"], "ans": "③輪生", "exp": "黑板樹與軟枝黃蟬的葉序屬於輪生[cite: 2, 13, 87]。"},
    {"unit": "單元1：多采多姿的植物", "type": "選擇題", "q": "為什麼小花蔓澤蘭將樹木包覆起來後，會導致樹木死亡？", "options": ["①會使樹木照不到陽光", "②會吃掉樹木的花", "③會搶走樹木的水分", "④會引來啃食樹木的動物"], "ans": "①會使樹木照不到陽光", "exp": "小花蔓澤蘭包覆樹木後，樹葉照不到陽光無法製造足夠養分而死亡，可切斷它的莖或連根拔除[cite: 14]。"},
    {"unit": "單元2：生活中的力", "type": "是非題", "q": "U形磁鐵中間和兩端的磁力強弱都相同。", "options": ["○", "×"], "ans": "×", "exp": "同一個磁鐵不同部位磁力強弱不同，兩端（磁極 N、S）磁力最強[cite: 18, 26, 68]。"},
    {"unit": "單元2：生活中的力", "type": "是非題", "q": "磁鐵可以吸在黑板上，表示黑板裡面一定有磁鐵的構造。", "options": ["○", "×"], "ans": "×", "exp": "磁鐵能吸在黑板上，是因為黑板含有「鐵」的成分，不一定有磁鐵[cite: 17, 26]。"},
    {"unit": "單元2：生活中的力", "type": "是非題", "q": "將皮球和硬幣放入水中後，發現硬幣會沉入水中，是因為它沒有受到水的浮力。", "options": ["○", "×"], "ans": "×", "exp": "放入水中的物體都會受到向上的浮力，硬幣沉下去是因為浮力不夠支撐其重量[cite: 21, 29, 69]。"},
    {"unit": "單元2：生活中的力", "type": "選擇題", "q": "整塊膠泥放入水中會沉到水底，如果將膠泥捏成下列形狀再放入水中，哪一種形狀還是會沉到水底？", "options": ["①船形", "②碗形", "③長條形", "④杯子形"], "ans": "③長條形", "exp": "船形、碗形、杯子形皆為容器形狀可浮於水面，長條形與球形會沉到水底[cite: 22, 27]。"},
    {"unit": "單元3：奇妙的空氣", "type": "是非題", "q": "游泳圈利用空氣會流動、可以被壓縮等特性，幫助我們浮在水面上。", "options": ["○", "×"], "ans": "×", "exp": "游泳圈是利用空氣「占有空間、沒有固定形狀」以及「水的浮力」幫助我們浮在水面上[cite: 34, 41, 43, 90]。"},
    {"unit": "單元3：奇妙的空氣", "type": "選擇題", "q": "將裝滿空氣的注射筒筒口抵住橡皮擦後，用力向下壓活塞再放手，活塞會出現什麼現象？", "options": ["①往上移動（回到原來位置）", "②繼續往下移動", "③停在壓下的位置", "④先往下再往上移動"], "ans": "①往上移動（回到原來位置）", "exp": "空氣可被壓縮，放手後被壓縮的空氣會推著活塞往上回到原來位置[cite: 38, 60]。"},
    {"unit": "單元4：廚房裡的科學", "type": "是非題", "q": "有一種白色的粉末材料，聞起來沒有特別的氣味，摸起來細細的，加入水中能完全溶解，這種材料可能是食鹽。", "options": ["○", "×"], "ans": "×", "exp": "摸起來「細細的」白色粉末是「小蘇打粉」；食鹽與檸檬酸摸起來是「粗粗的」顆粒狀[cite: 47, 57]。"},
    {"unit": "單元4：廚房裡的科學", "type": "選擇題", "q": "下列哪一種方法可以較準確且安全的分辨「食鹽」和「檸檬酸」？", "options": ["①嘗味道", "②觀察顏色", "③測試能不能完全溶於水", "④製作成水溶液後，加入紫色高麗菜汁觀察顏色變化"], "ans": "④製作成水溶液後，加入紫色高麗菜汁觀察顏色變化", "exp": "食鹽水加入紫色高麗菜汁維持紫色（中性），檸檬酸水則變成偏紅色（酸性）[cite: 47, 54, 58]。"}
]


# ==========================================
# 6. 智慧型 PDF 全題型解析與擦除紅藍解答工具
# ==========================================
def clean_pdf_text(raw_text: str) -> str:
    """清除康軒 PDF 內嵌注音符號與浮水印雜訊"""
    cleaned = re.sub(r'(?<=[\u4e00-\u9fff])[\u3105-\u3129\u02CA\u02C7\u02CB\u02D9ˊˇˋ˙]+', '', raw_text)
    cleaned = re.sub(r'xyz88\.\w+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'有著作權.*?必究\s*\d*', '', cleaned)
    return cleaned


def parse_all_questions_from_pdf(pdf_bytes: bytes, source_name: str, target_page: int = None) -> list:
    """
    從上傳的 PDF 自動擷取：
    1. 是非題 (○ / ×)
    2. 選擇題 (① / ② / ③ / ④)
    3. 填充與代號題 (括號內紅/藍字解答或代號)
    4. 勾選題 (□ 打 √)
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parsed_list = []

    pages_to_scan = [target_page] if target_page is not None else range(len(doc))

    for page_idx in pages_to_scan:
        if page_idx < 0 or page_idx >= len(doc):
            continue
        page = doc.load_page(page_idx)
        text = clean_pdf_text(page.get_text("text"))
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        joined_text = " ".join(lines)
        unit_label = f"考卷：{source_name} (第{page_idx+1}頁)"

        # 1. 解析是非題
        tf_pattern = re.compile(r'[\(（]\s*([○◯O×X✕✓√]?)\s*[\)）]\s*(\d+)\s*[\.、]\s*([^()（）①②③④]+?[。？\?])')
        for match in tf_pattern.finditer(joined_text):
            raw_ans, q_num, q_body = match.groups()
            q_body = re.sub(r'\s+', '', q_body)
            if len(q_body) < 6 or "選擇題" in q_body or "是非題" in q_body:
                continue
            ans = "○" if raw_ans in ["○", "◯", "O", "✓", "√"] else ("×" if raw_ans in ["×", "X", "✕"] else "○")
            parsed_list.append({
                "unit": unit_label,
                "pdf_name": source_name,
                "page_num": page_idx + 1,
                "type": "是非題",
                "q": f"是非第 {q_num} 題：{q_body}",
                "options": ["○", "×"],
                "ans": ans,
                "exp": f"來源：{source_name} 第 {page_idx+1} 頁"
            })

        # 2. 解析選擇題
        mc_pattern = re.compile(
            r'[\(（]\s*([①②③④1234]?)\s*[\)）]\s*(\d+)\s*[\.、]\s*(.+?)\s*[①1]\s*([^②③④]+?)\s*[②2]\s*([^③④]+?)\s*[③3]\s*([^④]+?)\s*[④4]\s*([^①②③④\(（]+?[。？\?]?)'
        )
        for match in mc_pattern.finditer(joined_text):
            raw_ans, q_num, q_stem, opt1, opt2, opt3, opt4 = match.groups()
            q_stem = re.sub(r'\s+', '', q_stem)
            opts = [
                f"①{re.sub(r'\\s+', '', opt1)}",
                f"②{re.sub(r'\\s+', '', opt2)}",
                f"③{re.sub(r'\\s+', '', opt3)}",
                f"④{re.sub(r'\\s+', '', opt4).rstrip('。')}"
            ]
            ans_map = {"①": opts[0], "1": opts[0], "②": opts[1], "2": opts[1], "③": opts[2], "3": opts[2], "④": opts[3], "4": opts[3]}
            ans_val = ans_map.get(raw_ans, opts[0])
            parsed_list.append({
                "unit": unit_label,
                "pdf_name": source_name,
                "page_num": page_idx + 1,
                "type": "選擇題",
                "q": f"選擇第 {q_num} 題：{q_stem}",
                "options": opts,
                "ans": ans_val,
                "exp": f"來源：{source_name} 第 {page_idx+1} 頁"
            })

        # 3. 解析做一做／素養題中的括號填充題 (如：屬於(藤本)莖、應為(S)極、溶解(3)平匙)
        fill_pattern = re.compile(r'([^。；！？\n]{4,35}?[\(（]\s*([A-DSNsN甲乙丙丁藤本磁極快慢34813不可以]+)\s*[\)）][^。；！？\n]{0,20}?[。？])')
        blanks_found = []
        for match in fill_pattern.finditer(joined_text):
            full_sent, ans_token = match.groups()
            full_sent_clean = re.sub(r'\s+', '', full_sent)
            ans_token = ans_token.strip()
            # 排除已抓過的是非選擇題號
            if re.match(r'^[\(（][○×①②③④1234][\)）]\d+\.', full_sent_clean):
                continue
            masked_sent = re.sub(r'[\(（]\s*' + re.escape(ans_token) + r'\s*[\)）]', '【 ＿＿ 】', full_sent_clean, count=1)
            blanks_found.append({
                "label": masked_sent,
                "options": None,
                "ans": ans_token
            })
        if blanks_found:
            parsed_list.append({
                "unit": unit_label,
                "pdf_name": source_name,
                "page_num": page_idx + 1,
                "type": "填充與代號題",
                "q": f"【本頁做一做／填充與代號題】（共 {len(blanks_found)} 格）",
                "blanks": blanks_found,
                "exp": f"來源：{source_name} 第 {page_idx+1} 頁填充與代號解析"
            })

    return parsed_list


def render_pdf_page_image(pdf_bytes: bytes, page_num: int, hide_answers: bool = False, zoom: float = 2.2) -> Image.Image:
    """將 PDF 轉為高解析圖片，並可一鍵濾除教師版紅/藍解答字體與連線"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(page_num)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    if hide_answers:
        arr = np.array(img, dtype=np.int16)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        # 濾除教師用解答常見的紅色/桃紅與藍色筆跡與連線
        red_mask = (r > 140) & ((r - g) > 25) & ((r - b) > 15)
        blue_mask = (b > 130) & ((b - r) > 25) & ((b - g) > 10)
        arr[red_mask | blue_mask] = [255, 255, 255]
        img = Image.fromarray(arr.astype(np.uint8))

    return img


# ==========================================
# 7. 互動題目渲染與自動批改核心函數（支援 5 大題型）
# ==========================================
def render_interactive_question(idx: int, item: dict, form_prefix: str):
    """在畫面上渲染單一題目（支援是非、選擇、勾選、填充代號、連連看）"""
    q_type = item.get("type", "選擇題")
    st.markdown(
        f'<div class="q-box">第 {idx+1} 題 <span style="font-size:0.85em; color:#2563eb;">[{item.get("unit","")}] [{q_type}]</span><br>{clean_cite(item["q"])}</div>',
        unsafe_allow_html=True
    )

    if q_type in ["是非題", "選擇題"]:
        st.radio(
            f"作答_{form_prefix}_{idx}",
            item["options"],
            index=None,
            key=f"{form_prefix}_q_{idx}",
            label_visibility="collapsed",
            horizontal=(q_type == "是非題")
        )

    elif q_type == "勾選題":
        st.caption("👇 請在符合的項目打 √（可複選）：")
        cols = st.columns(2)
        for opt_i, opt_label in enumerate(item["options"]):
            with cols[opt_i % 2]:
                st.checkbox(
                    clean_cite(opt_label),
                    key=f"{form_prefix}_q_{idx}_chk_{opt_i}"
                )

    elif q_type == "填充與代號題":
        st.caption("👇 請在下方各小題填入正確文字或選擇正確代號：")
        for b_i, b_item in enumerate(item.get("blanks", [])):
            b_label = clean_cite(b_item["label"])
            if b_item.get("options"):
                st.selectbox(
                    b_label,
                    ["-- 請選擇答案 --"] + b_item["options"],
                    key=f"{form_prefix}_q_{idx}_blank_{b_i}"
                )
            else:
                st.text_input(
                    b_label,
                    placeholder="請直接輸入答案...",
                    key=f"{form_prefix}_q_{idx}_blank_{b_i}"
                )

    elif q_type == "連連看":
        st.caption("👇 請為左邊的每一個項目選擇右邊對應的連線目標（選完後下方會即時畫出連線！）：")
        left_items = item.get("left_items", [])
        right_items = item.get("right_items", [])
        current_pairs = {}
        cols = st.columns(2)
        for l_i, l_text in enumerate(left_items):
            with cols[l_i % 2]:
                sel = st.selectbox(
                    f"🔗 {clean_cite(l_text)} 連到 👉",
                    ["-- 請選擇連線對象 --"] + right_items,
                    key=f"{form_prefix}_q_{idx}_match_{l_i}"
                )
                if sel != "-- 請選擇連線對象 --":
                    current_pairs[l_text] = sel
        render_svg_matching(left_items, right_items, current_pairs, show_grading=False)


def grade_interactive_question(idx: int, item: dict, form_prefix: str) -> tuple:
    """
    自動批改單一題目，回傳 (is_all_correct: bool, user_ans_summary: str, correct_ans_summary: str)
    """
    q_type = item.get("type", "選擇題")

    if q_type in ["是非題", "選擇題"]:
        u_ans = st.session_state.get(f"{form_prefix}_q_{idx}")
        is_ok = (u_ans == item["ans"])
        return is_ok, (u_ans if u_ans else "未作答"), item["ans"]

    elif q_type == "勾選題":
        chosen = []
        for opt_i, opt_label in enumerate(item["options"]):
            if st.session_state.get(f"{form_prefix}_q_{idx}_chk_{opt_i}", False):
                chosen.append(opt_label)
        expected = item["ans"]
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
            u_val = "" if raw_val == "-- 請選擇答案 --" else str(raw_val).strip()
            ans_val = str(b_item["ans"]).strip()
            # 寬鬆比對：完全相同，或標準答案包含於選項代號中
            sub_ok = (u_val.upper() == ans_val.upper()) or (u_val != "" and (ans_val in u_val or u_val in ans_val))
            if not sub_ok:
                all_ok = False
            u_parts.append(f"({b_i+1}) {u_val if u_val else '未填'}{'✔️' if sub_ok else '❌'}")
            c_parts.append(f"({b_i+1}) {ans_val}")
        return all_ok, " ｜ ".join(u_parts), " ｜ ".join(c_parts)

    elif q_type == "連連看":
        left_items = item.get("left_items", [])
        right_items = item.get("right_items", [])
        expected_map = item.get("ans", {})
        user_map = {}
        all_ok = True
        u_parts = []
        c_parts = []
        for l_i, l_text in enumerate(left_items):
            sel = st.session_state.get(f"{form_prefix}_q_{idx}_match_{l_i}", "-- 請選擇連線對象 --")
            u_val = "" if sel == "-- 請選擇連線對象 --" else sel
            if u_val:
                user_map[l_text] = u_val
            exp_val = expected_map.get(l_text, "")
            sub_ok = (u_val == exp_val)
            if not sub_ok:
                all_ok = False
            u_parts.append(f"{l_text}➔{u_val if u_val else '未連'}{'✔️' if sub_ok else '❌'}")
            c_parts.append(f"{l_text}➔{exp_val}")
        # 顯示批改後的連線圖（綠線正確，紅虛線錯誤）
        render_svg_matching(left_items, right_items, user_map, correct_pairs=expected_map, show_grading=True)
        return all_ok, " ； ".join(u_parts), " ； ".join(c_parts)

    return False, "未知題型", ""


# ==========================================
# 8. 側邊欄導覽、字體放大控制與永久儲存狀態
# ==========================================
with st.sidebar:
    st.title("🌿 小三自然複習神器")
    st.caption("115上 康軒版｜全題型作答＋自動存檔版")

    font_scale = st.slider(
        "🔍 畫面字體大小（手機/兒童推薦 125%~145%）",
        min_value=100,
        max_value=160,
        value=125,
        step=5,
        help="向右拉動可將題目、選項按鈕、表格與重點卡片字體全面放大！"
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

    # 讀取資料庫最新統計
    saved_pdfs_dict = db_load_all_pdfs()
    custom_qs_list = db_load_custom_questions()
    wrong_qs_list = db_load_wrong_questions()
    combined_quiz_bank = list(DEFAULT_QUIZ_BANK) + custom_qs_list

    st.divider()
    st.markdown(f"**📚 總題庫數：** `{len(combined_quiz_bank)}` 題")
    st.markdown(f"**📕 錯題本數量：** `{len(wrong_qs_list)}` 題 *(已永久存檔)*")
    st.markdown(f"**📂 已存 PDF 考卷：** `{len(saved_pdfs_dict)}` 份 *(已永久存檔)*")


# ==========================================
# 模式 1：完整重點複習（第一份重點複習 100% 完整呈現）
# ==========================================
if mode == "📖 完整重點複習（含遮罩背誦）":
    st.header("📖 康軒國小自然 3 上｜第一份《重點複習》完整知識庫")
    st.info("💡 100% 完整收錄第一份《重點複習》第 1～4 單元所有學習探索、重點補給站、分類比較表與實驗流程。開啟「🙈 遮罩關鍵字自測」即可進行填空背誦！")

    col_u, col_m, col_s = st.columns([2, 1.5, 2])
    with col_u:
        selected_unit = st.selectbox("📂 選擇複習單元", ["🌟 全部單元完整總覽"] + list(COMPLETE_REVIEW_DATA.keys()))
    with col_m:
        hide_kw = st.toggle("🙈 遮罩【關鍵字】填空自測", value=False)
    with col_s:
        search_kw = st.text_input("🔍 關鍵字搜尋（如：軸根、磁極、紫色高麗菜）", "")

    units_to_show = list(COMPLETE_REVIEW_DATA.keys()) if selected_unit == "🌟 全部單元完整總覽" else [selected_unit]

    for u_name in units_to_show:
        u_data = COMPLETE_REVIEW_DATA[u_name]
        st.subheader(f"{u_data['icon']} {u_name}")

        for act_name, act_content in u_data["activities"].items():
            full_act_str = json.dumps(act_content, ensure_ascii=False)
            if search_kw and (search_kw not in full_act_str and search_kw not in act_name):
                continue

            with st.expander(f"📌 {act_name}", expanded=True):
                st.markdown("#### 📝 學習探索＆重點補給站")
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
                        exp_html = f"""
                        <div class="exp-card">
                            <strong>{clean_cite(exp['title'])}</strong><br>
                            🎯 <b>實驗目的：</b>{render_masked_text(exp['purpose'], hide_kw)}<br>
                            🧪 <b>實驗流程：</b><br>{steps_html}<br>
                            📊 <b>實驗結果：</b>{render_masked_text(exp['result'], hide_kw)}<br>
                            💡 <b>實驗結論：</b>{render_masked_text(exp['conclusion'], hide_kw)}
                        </div>
                        """
                        st.markdown(exp_html, unsafe_allow_html=True)


# ==========================================
# 模式 2：全題型互動題庫闖關（含連連看、填充、勾選、是非、選擇）
# ==========================================
elif mode == "🎮 全題型題庫闖關（含連連看/填空）":
    st.header("🎮 全題型互動題庫闖關（支援連連看、填充、打勾、是非、選擇）")

    all_units = sorted(list(set(q["unit"] for q in combined_quiz_bank)))
    all_types = ["全部題型", "連連看", "填充與代號題", "勾選題", "是非題", "選擇題"]

    c1, c2, c3 = st.columns([2.2, 1.3, 1])
    with c1:
        selected_quiz_units = st.multiselect("📂 選擇測驗單元範圍", all_units, default=all_units)
    with c2:
        q_type_filter = st.selectbox("🎯 題型篩選（可單獨練連連看或填充！）", all_types)
    with c3:
        num_q = st.number_input("抽題數量", min_value=1, max_value=100, value=10, step=5)

    filtered_pool = [
        q for q in combined_quiz_bank
        if q["unit"] in selected_quiz_units and (q_type_filter == "全部題型" or q["type"] == q_type_filter)
    ]

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        if st.button("🎲 隨機抽題開始新測驗", use_container_width=True, type="primary"):
            sample_size = min(int(num_q), len(filtered_pool))
            st.session_state.active_quiz = random.sample(filtered_pool, sample_size) if filtered_pool else []
    with col_b2:
        if st.button("📋 載入範圍內全部題目（依序作答）", use_container_width=True):
            st.session_state.active_quiz = list(filtered_pool)

    if "active_quiz" not in st.session_state or not st.session_state.active_quiz:
        sample_size = min(int(num_q), len(filtered_pool))
        st.session_state.active_quiz = random.sample(filtered_pool, sample_size) if filtered_pool else []

    exam_questions = st.session_state.active_quiz

    if not exam_questions:
        st.warning("⚠️ 此篩選條件下沒有題目，請調整單元或題型篩選！")
    else:
        st.caption(f"👇 本次測驗共 **{len(exam_questions)}** 題。連連看與選單皆可直接點選作答，完成後請按最下方「✅ 交卷並自動批改」。")

        for idx, item in enumerate(exam_questions):
            render_interactive_question(idx, item, form_prefix="main_quiz")
            st.divider()

        if st.button("✅ 交卷並自動批改錯誤", type="primary", use_container_width=True):
            correct_count = 0
            total_count = len(exam_questions)
            st.subheader("📊 自動批改結果與詳解")

            for idx, item in enumerate(exam_questions):
                is_ok, u_ans_str, c_ans_str = grade_interactive_question(idx, item, form_prefix="main_quiz")
                if is_ok:
                    correct_count += 1
                    st.success(
                        f"✔️ **第 {idx+1} 題【答對】！** ({item['type']})\n\n"
                        f"- 題目：{clean_cite(item['q'])}\n"
                        f"- 你的作答：`{u_ans_str}`\n"
                        f"- 💡 解析：{clean_cite(item.get('exp', ''))}"
                    )
                else:
                    st.error(
                        f"❌ **第 {idx+1} 題【有誤，已自動加入錯題本】！** ({item['type']})\n\n"
                        f"- 題目：{clean_cite(item['q'])}\n"
                        f"- 你的作答：`{u_ans_str}`\n"
                        f"- 🎯 **正確答案：`{c_ans_str}`**\n"
                        f"- 💡 解析：{clean_cite(item.get('exp', ''))}"
                    )
                    db_add_wrong_question(item, u_ans_str)

            final_score = round((correct_count / total_count) * 100)
            db_save_exam_history(f"題庫闖關 ({q_type_filter})", final_score, correct_count, total_count)
            st.metric("🏆 本次測驗得分", f"{final_score} 分", f"答對 {correct_count} / {total_count} 題")
            if final_score == 100:
                st.balloons()


# ==========================================
# 模式 3：考卷 PDF 直接作答 ＆ 自動批改
# ==========================================
elif mode == "📄 考卷 PDF 直接作答＆自動批改":
    st.header("📄 考卷 PDF 匯入｜原卷消去解答 ＋ 電腦/手機直接作答批改")
    st.info("💡 上傳的 PDF 考卷會**自動永久儲存於資料庫**，重新整理網頁不會消失！系統會將考卷的紅/藍解答隱藏變成空白考卷，並在旁生成**互動式作答卡（是非、選擇、填充代號、勾選、連連看）**讓你直接作答與自動批改。")

    uploaded_files = st.file_uploader(
        "📥 上傳一份或多份 PDF 題庫/考卷（上傳後自動永久存檔並解析題目）",
        type=["pdf"],
        accept_multiple_files=True
    )

    if uploaded_files:
        new_count = 0
        for uf in uploaded_files:
            if uf.name not in saved_pdfs_dict:
                raw_bytes = uf.read()
                db_save_pdf(uf.name, raw_bytes)
                extracted_qs = parse_all_questions_from_pdf(raw_bytes, uf.name)
                if extracted_qs:
                    db_save_custom_questions(extracted_qs, pdf_name=uf.name)
                new_count += 1
        if new_count > 0:
            st.success(f"🎉 成功永久儲存 {new_count} 份 PDF 並自動匯入題目！")
            st.rerun()

    saved_pdfs_dict = db_load_all_pdfs()

    if not saved_pdfs_dict:
        st.warning("目前尚未上傳 PDF 考卷，請從上方上傳檔案（例如：校用卷、月考卷、重點複習）。")
    else:
        c_sel, c_pg, c_mask, c_del = st.columns([2.2, 1, 1.5, 1])
        with c_sel:
            chosen_pdf_name = st.selectbox("📂 選擇已存檔的 PDF 考卷", list(saved_pdfs_dict.keys()))
        pdf_bytes = saved_pdfs_dict[chosen_pdf_name]
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)

        with c_pg:
            page_num = st.number_input(f"選擇頁碼 (1~{total_pages})", min_value=1, max_value=total_pages, value=1)
        with c_mask:
            hide_ans_img = st.toggle("🪄 擦除考卷紅/藍解答（空白卷）", value=True)
        with c_del:
            if st.button("🗑️ 刪除此份 PDF"):
                db_delete_pdf(chosen_pdf_name)
                st.rerun()

        # 左右分欄（電腦上左邊看空白考卷圖、右邊直接作答；手機上自動上下排列）
        col_img, col_ans_sheet = st.columns([1.15, 1])

        with col_img:
            st.subheader(f"📄 考卷畫面（第 {page_num} 頁）")
            img_rendered = render_pdf_page_image(pdf_bytes, page_num - 1, hide_answers=hide_ans_img, zoom=2.2)
            st.image(img_rendered, use_container_width=True)

        with col_ans_sheet:
            st.subheader(f"✍️ 第 {page_num} 頁｜直接作答與自動批改區")

            # 取得該頁自動解析出的題目 + 使用者為該頁新增的互動題
            all_saved_custom = db_load_custom_questions()
            page_questions = [
                q for q in all_saved_custom
                if q.get("pdf_name") == chosen_pdf_name and int(q.get("page_num", 0)) == page_num
            ]
            # 若資料庫中尚未有該頁解析，即時解析本頁
            if not page_questions:
                page_questions = parse_all_questions_from_pdf(pdf_bytes, chosen_pdf_name, target_page=page_num - 1)
                if page_questions:
                    db_save_custom_questions(page_questions, pdf_name=chosen_pdf_name, page_num=page_num)

            # 提供「快速新增本頁看圖連連看 / 填代號 / 勾選題」工具（針對純圖片無文字的圖表題）
            with st.expander("➕ 新增／補充本頁看圖題（連連看、填代號、打勾題）並永久存檔"):
                add_type = st.selectbox("選擇要補充的本頁題型", ["填充與代號題", "連連看", "勾選題", "是非題", "選擇題"], key="add_t")
                add_q_title = st.text_input("題目說明", value=f"第 {page_num} 頁・做一做看圖題", key="add_title")

                if add_type == "填充與代號題":
                    st.caption("每行輸入一格：`小題標籤:正確答案`（例如 `(1)葉片部位:C` 或 `①號磁鐵極性:S`）")
                    raw_blanks = st.text_area("輸入各小格設定", value="(1) 第1格答案:A\n(2) 第2格答案:B", key="add_blanks")
                    if st.button("💾 儲存此題到本頁作答區", key="save_fill"):
                        blanks_list = []
                        for line in raw_blanks.strip().split("\n"):
                            if ":" in line or "：" in line:
                                parts = re.split(r'[:：]', line, maxsplit=1)
                                blanks_list.append({"label": parts[0].strip(), "options": None, "ans": parts[1].strip()})
                        if blanks_list:
                            new_q = {
                                "unit": f"考卷：{chosen_pdf_name} (第{page_num}頁)",
                                "pdf_name": chosen_pdf_name,
                                "page_num": page_num,
                                "type": "填充與代號題",
                                "q": add_q_title,
                                "blanks": blanks_list,
                                "exp": f"{chosen_pdf_name} 第 {page_num} 頁自訂看圖填充題"
                            }
                            db_save_custom_questions([new_q], pdf_name=chosen_pdf_name, page_num=page_num)
                            st.success("已儲存！")
                            st.rerun()

                elif add_type == "連連看":
                    st.caption("每行輸入一組正確配對：`左邊項目=右邊項目`（例如 `(1)朱槿=互生`）")
                    raw_pairs = st.text_area("輸入連連看配對", value="(1) 項目1=目標A\n(2) 項目2=目標B", key="add_match")
                    if st.button("💾 儲存此連連看到本頁作答區", key="save_match"):
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
                                "unit": f"考卷：{chosen_pdf_name} (第{page_num}頁)",
                                "pdf_name": chosen_pdf_name,
                                "page_num": page_num,
                                "type": "連連看",
                                "q": add_q_title,
                                "left_items": l_list,
                                "right_items": r_list,
                                "ans": ans_map,
                                "exp": f"{chosen_pdf_name} 第 {page_num} 頁看圖連連看"
                            }
                            db_save_custom_questions([new_q], pdf_name=chosen_pdf_name, page_num=page_num)
                            st.success("已儲存！")
                            st.rerun()

                elif add_type == "勾選題":
                    raw_opts = st.text_input("所有選項（用逗號分隔）", value="(1)選項1, (2)選項2, (3)選項3, (4)選項4", key="add_chk_opts")
                    raw_ans = st.text_input("應打 √ 的正確選項（用逗號分隔）", value="(1)選項1, (3)選項3", key="add_chk_ans")
                    if st.button("💾 儲存此勾選題到本頁作答區", key="save_chk"):
                        opts_l = [x.strip() for x in re.split(r'[,，]', raw_opts) if x.strip()]
                        ans_l = [x.strip() for x in re.split(r'[,，]', raw_ans) if x.strip()]
                        new_q = {
                            "unit": f"考卷：{chosen_pdf_name} (第{page_num}頁)",
                            "pdf_name": chosen_pdf_name,
                            "page_num": page_num,
                            "type": "勾選題",
                            "q": add_q_title,
                            "options": opts_l,
                            "ans": ans_l,
                            "exp": f"{chosen_pdf_name} 第 {page_num} 頁打√勾選題"
                        }
                        db_save_custom_questions([new_q], pdf_name=chosen_pdf_name, page_num=page_num)
                        st.success("已儲存！")
                        st.rerun()

            if not page_questions:
                st.info("📌 本頁為純課文或純圖片頁。若本頁有看圖連連看或填代號題，可點開上方「➕ 新增／補充本頁看圖題」加入作答框！")
            else:
                form_prefix = f"pdf_{hashlib.md5(chosen_pdf_name.encode()).hexdigest()[:6]}_p{page_num}"
                for idx, q_item in enumerate(page_questions):
                    render_interactive_question(idx, q_item, form_prefix=form_prefix)
                    st.divider()

                if st.button("🎯 送出本頁答案並自動批改", type="primary", use_container_width=True, key=f"grade_btn_{form_prefix}"):
                    c_cnt = 0
                    t_cnt = len(page_questions)
                    for idx, q_item in enumerate(page_questions):
                        is_ok, u_str, c_str = grade_interactive_question(idx, q_item, form_prefix=form_prefix)
                        if is_ok:
                            c_cnt += 1
                            st.success(f"✔️ **第 {idx+1} 題答對！** 你的答案：`{u_str}`")
                        else:
                            st.error(f"❌ **第 {idx+1} 題答錯（已存入錯題本）！**\n- 你的答案：`{u_str}`\n- 🎯 **正確答案：`{c_str}`**")
                            db_add_wrong_question(q_item, u_str)

                    page_score = round((c_cnt / t_cnt) * 100)
                    db_save_exam_history(f"{chosen_pdf_name} 第{page_num}頁", page_score, c_cnt, t_cnt)
                    st.metric("🏆 本頁得分", f"{page_score} 分", f"答對 {c_cnt} / {t_cnt} 題")
                    if page_score == 100:
                        st.balloons()


# ==========================================
# 模式 4：錯題複習本（可直接重測消除錯題）＆ 永久備份管理
# ==========================================
elif mode == "❌ 錯題複習本＆永久備份管理":
    st.header("❌ 專屬錯題複習本 ＆ 永久資料庫管理")

    tab_wrong, tab_history, tab_backup = st.tabs([
        "📕 錯題本直接重測（答對自動移除）",
        "📈 歷史測驗成績紀錄",
        "💾 題庫與錯題永久備份檔 (匯出/匯入)"
    ])

    with tab_wrong:
        wrong_qs = db_load_wrong_questions()
        if not wrong_qs:
            st.success("🎉 太棒了！目前錯題本是空的，所有題目都已經精通囉！")
        else:
            st.warning(f"📌 目前共有 **{len(wrong_qs)}** 題錯題（已自動儲存在 SQLite 資料庫中，關閉網頁不會消失）。你可以直接在下方重新作答，答對即可消除錯題！")

            if st.button("🗑️ 一鍵清空全部錯題紀錄"):
                db_clear_all_wrong_questions()
                st.rerun()

            st.divider()
            for idx, wq in enumerate(wrong_qs):
                st.caption(f"📕 累計答錯 `{wq.get('_wrong_count', 1)}` 次 ｜ 上次錯誤作答：`{wq.get('_last_ans', '')}` ｜ 紀錄時間：{wq.get('_updated_at', '')}")
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
                            st.error(f"❌ 還是有誤喔！你的作答：`{u_str}` ｜ 🎯 正確答案：`{c_str}`\n\n💡 解析：{clean_cite(wq.get('exp',''))}")
                with c_del:
                    if st.button(f"🗑️ 手動移除此題", key=f"del_wq_{idx}", use_container_width=True):
                        db_remove_wrong_question(wq["_qid"])
                        st.rerun()
                st.divider()

    with tab_history:
        history_list = db_load_exam_history()
        if not history_list:
            st.info("尚無測驗紀錄，快去題庫闖關或考卷作答試試看吧！")
        else:
            render_html_table(
                ["測驗名稱", "分數", "答對題數", "測驗時間"],
                [[h["測驗名稱"], h["分數"], h["答對題數"], h["測驗時間"]] for h in history_list]
            )

    with tab_backup:
        st.subheader("📦 完整學習資料一鍵備份／還原")
        st.markdown("雖然系統已內建 **SQLite 硬碟資料庫**（重整網頁或關機重開都不會消失），但若將來重新部署 GitHub / Streamlit Cloud 伺服器，可透過下方按鈕下載完整備份檔隨時還原：")

        backup_payload = {
            "custom_questions": db_load_custom_questions(),
            "wrong_questions": db_load_wrong_questions(),
            "exam_history": db_load_exam_history()
        }
        backup_json_str = json.dumps(backup_payload, ensure_ascii=False, indent=2)

        st.download_button(
            label="⬇️ 下載完整題庫＋錯題本＋成績備份檔 (.json)",
            data=backup_json_str.encode("utf-8"),
            file_name="science_3a_full_backup.json",
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
