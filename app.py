from __future__ import annotations

import csv
import io
import re

import pdfplumber
import streamlit as st

MONTH_MAP = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _parse_date(s: str) -> str | None:
    m = re.match(r"([A-Za-z]{3})\s+(\d{1,2}),\s+(\d{4})", s)
    if not m:
        return None
    mm = MONTH_MAP.get(m.group(1))
    if not mm:
        return None
    return f"{m.group(3)}/{mm}/{m.group(2).zfill(2)}"


def _parse_amount(s: str) -> int:
    return int(s.replace(",", ""))


def _extract_text(file) -> str:
    with pdfplumber.open(file) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _extract_data(text: str, filename: str) -> dict:
    errors = []
    is_cn = bool(re.search(r"Credit Note Number", text))

    # 日付
    pat = r"Credit Note\s*[\r\n]+\s*([A-Za-z]{3}\s+\d{1,2},\s+\d{4})" if is_cn \
        else r"Tax invoice\s*[\r\n]+\s*([A-Za-z]{3}\s+\d{1,2},\s+\d{4})"
    dm = re.search(pat, text)
    date = _parse_date(dm.group(1)) if dm else None
    if not date:
        errors.append("日付が見つかりません")

    # eBay user ID
    um = re.search(r"eBay user ID\s*[\r\n]+\s*([\w\-\.]+)", text)
    user_id = um.group(1).strip() if um else None
    if not user_id:
        errors.append("eBay user IDが見つかりません")

    # 金額
    taxable = jct = None
    YEN = r"[¥￥]"
    if is_cn:
        m = re.search(rf"-{YEN}\s*([\d,]+)Total credited amount at 10 % in JPY", text)
        taxable = -_parse_amount(m.group(1)) if m else None
        if taxable is None:
            errors.append("税抜金額 (Total credited amount at 10 % in JPY) が見つかりません")

        m = re.search(rf"-{YEN}\s*([\d,]+)Total JCT credited at 10 % in JPY", text)
        jct = -_parse_amount(m.group(1)) if m else None
        if jct is None:
            errors.append("消費税額 (Total JCT credited at 10 % in JPY) が見つかりません")
    else:
        m = re.search(rf"{YEN}\s*([\d,]+)Total taxable amount at 10 % in JPY", text)
        taxable = _parse_amount(m.group(1)) if m else None
        if taxable is None:
            errors.append("税抜金額 (Total taxable amount at 10 % in JPY) が見つかりません")

        m = re.search(rf"{YEN}\s*([\d,]+)JCT at 10 % in JPY", text)
        jct = _parse_amount(m.group(1)) if m else None
        if jct is None:
            errors.append("消費税額 (JCT at 10 % in JPY) が見つかりません")

    if errors:
        raise ValueError(f"**{filename}**\n" + "\n".join(f"- {e}" for e in errors))

    total = taxable + jct
    return {
        "date": date,
        "summary": f"{'eBay手数料返還' if is_cn else 'eBay手数料'} {user_id}",
        "taxable": taxable,
        "jct": jct,
        "total": total,
        "accountName": f"{user_id} 返還" if is_cn else user_id,
        "isCreditNote": is_cn,
        "userId": user_id,
    }


def _build_csv(rows: list[dict]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerow(["日付", "摘要", "税抜金額 (A)", "消費税額 (B)", "税込合計 (A+B)", "アカウント名"])
    for r in rows:
        w.writerow([r["date"], r["summary"], r["taxable"], r["jct"], r["total"], r["accountName"]])
    return ("﻿" + out.getvalue()).encode("utf-8")


def _build_yayoi(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_NONNUMERIC, lineterminator="\r\n")
    for r in rows:
        amount = abs(r["total"])
        jct = abs(r["jct"])
        if r["isCreditNote"]:
            row = [
                "2000", "", "", r["date"],
                "輸出売上", "輸出売上", "", "輸出売上", amount, "",
                "支払手数料", r["userId"], "", "課対仕入内10%", amount, jct,
                r["summary"], "", "", 0, "", "", "", "", "",
            ]
        else:
            row = [
                "2000", "", "", r["date"],
                "支払手数料", r["userId"], "", "課対仕入内10%", amount, jct,
                "輸出売上", "輸出売上", "", "輸出売上", amount, "",
                r["summary"], "", "", 0, "", "", "", "", "",
            ]
        safe = [c.encode("cp932", errors="replace").decode("cp932") if isinstance(c, str) else c for c in row]
        w.writerow(safe)
    return buf.getvalue().encode("cp932", errors="replace")


# ── UI ──────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="eBay手数料CSV作成ツール",
    page_icon="🛒",
    layout="centered",
)

st.markdown(
    """
    <style>
    .header-bar {
        background: #DC1E1E;
        color: white;
        padding: 14px 20px;
        border-radius: 8px;
        margin-bottom: 28px;
        font-size: 20px;
        font-weight: bold;
        font-family: sans-serif;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown('<div class="header-bar">eBay 手数料 CSV 作成ツール</div>', unsafe_allow_html=True)

uploaded = st.file_uploader(
    "eBay税務インボイスPDF（Tax Invoice / Credit Note）",
    type="pdf",
    accept_multiple_files=True,
)

if not uploaded:
    st.stop()

st.caption(f"読み込みファイル: {len(uploaded)} 件")

if not st.button("CSV を作成する", type="primary", use_container_width=True):
    st.stop()

rows: list[dict] = []
error_msgs: list[str] = []

progress = st.progress(0, text="処理中...")
for i, f in enumerate(uploaded):
    try:
        text = _extract_text(f)
        rows.append(_extract_data(text, f.name))
    except Exception as e:
        error_msgs.append(str(e))
    progress.progress((i + 1) / len(uploaded), text=f"処理中: {f.name}")
progress.empty()

for msg in error_msgs:
    st.error(msg)

if not rows:
    st.stop()

rows.sort(key=lambda r: r["date"])
st.success(f"処理完了：{len(rows)} 件")

st.dataframe(
    [
        {
            "日付": r["date"],
            "摘要": r["summary"],
            "税抜金額": f"¥{r['taxable']:,}",
            "消費税額": f"¥{r['jct']:,}",
            "税込合計": f"¥{r['total']:,}",
        }
        for r in rows
    ],
    use_container_width=True,
)

col1, col2 = st.columns(2)
with col1:
    st.download_button(
        "📥 CSV をダウンロード",
        _build_csv(rows),
        "ebay_fees_summary.csv",
        "text/csv",
        use_container_width=True,
    )
with col2:
    st.download_button(
        "📥 弥生TXT をダウンロード",
        _build_yayoi(rows),
        "ebay_fees_yayoi.txt",
        "text/plain; charset=cp932",
        use_container_width=True,
    )
