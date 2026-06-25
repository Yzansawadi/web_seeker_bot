#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import re
import time
from bs4 import BeautifulSoup
import pandas as pd

URL = "https://educate.iust.edu.sy/faces/ui/pages/guest/scheduleCourses/index.xhtml"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "X-Requested-With": "XMLHttpRequest",
    "Faces-Request": "partial/ajax"
}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": HEADERS["User-Agent"]})

# خريطة الأيام
DAY_MAP = {
    "س": "السبت",
    "ح": "الأحد",
    "ن": "الاثنين",
    "ث": "الثلاثاء",
    "ر": "الأربعاء"
}

# الأعمدة الأساسية
KEYS = ["index","code","name","activity","time","room","section","status","teacher","building"]

# تحويل 24h إلى 12h
def to_12h(t):
    if ":" not in t:
        return ""
    h, m = t.split(":")
    h = int(h)
    suffix = "AM" if h < 12 else "PM"
    h = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
    return f"{h}:{m} {suffix}"

# دالة الوقت الجديدة الصحيحة 100%
def parse_time_field(t):
    """
    يعالج الشكل الحقيقي للوقت كما يظهر في ملف IUST:
    [ 08:00_10:00 ] ن
    [ 12:00_14:00 ] ث س
    [ 14:00_16:00 ] ن[ 12:00_14:00 ] ن
    """

    t = t.strip()

    # استخراج كل المقاطع من الشكل [ HH:MM_HH:MM ]
    blocks = re.findall(r'\[\s*(\d{2}:\d{2})_(\d{2}:\d{2})\s*\]', t)

    # استخراج الأيام (كل شيء خارج الأقواس)
    days_part = re.sub(r'\[.*?\]', '', t).strip()
    days_list = days_part.split()
    full_days = [DAY_MAP.get(d, d) for d in days_list]
    full_days_str = " / ".join(full_days)

    # لو ما في وقت
    if not blocks:
        return full_days_str, "", "", t

    # نأخذ أول فترة فقط
    start24, end24 = blocks[0]
    start12 = to_12h(start24)
    end12 = to_12h(end24)

    return full_days_str, start12, end12, t

# استخراج ViewState
def get_viewstate(html):
    m = re.search(r'name="javax\.faces\.ViewState"\s+value="([^"]+)"', html)
    if m:
        return m.group(1)

    m = re.search(r'<update[^>]*id="javax\.faces\.ViewState"[^>]*>(.*?)</update>', html, re.S)
    if m:
        inner = m.group(1)
        s = inner.find("CDATA[")
        if s != -1:
            s += len("CDATA[")
            e = inner.find("]]>", s)
            if e != -1:
                return inner[s:e]
        return re.sub(r'<.*?>','',inner).strip()

    m = re.search(r'javax\.faces\.ViewState.*?CDATA\[(.*?)\]', html, re.S)
    if m:
        return m.group(1)

    return None

# استخراج الصفوف من fragment
def extract_rows_from_fragment(fragment):
    fragment = fragment.strip()
    if fragment.startswith("<tr") and "<table" not in fragment:
        fragment = "<table>" + fragment + "</table>"

    soup = BeautifulSoup(fragment, "html.parser")
    rows = []
    for tr in soup.find_all("tr", {"data-ri": True}):
        cells = []
        for td in tr.find_all("td", recursive=False):
            text = td.get_text(" ", strip=True)
            text = re.sub(r'\s+', ' ', text).strip()
            cells.append(text)
        if cells:
            rows.append(cells)
    return rows

# استخراج الصفوف من partial-response
def extract_rows_from_partial(text):
    rows = []
    for m in re.finditer(r'<update[^>]*id="([^"]*scheduleDtl[^"]*)"[^>]*>(.*?)</update>', text, re.S):
        inner = m.group(2)
        s = inner.find("CDATA[")
        if s != -1:
            s += len("CDATA[")
            e = inner.find("]]>", s)
            fragment = inner[s:e] if e != -1 else inner[s:]
        else:
            fragment = inner
        rows.extend(extract_rows_from_fragment(fragment))
    return rows

# POST مع retry
def safe_post(url, data, headers=None, retries=3, backoff=0.6):
    for attempt in range(retries):
        try:
            r = SESSION.post(url, data=data, headers=headers or HEADERS, timeout=20)
            r.raise_for_status()
            return r
        except:
            if attempt + 1 == retries:
                raise
            time.sleep(backoff * (attempt + 1))

# =========================
# بدء التنفيذ
# =========================

print("فتح الصفحة الرئيسية...")
r = SESSION.get(URL, headers=HEADERS, timeout=20)
viewstate = get_viewstate(r.text)
print("ViewState OK:", bool(viewstate))

# البحث الأولي
payload_search = {
    "javax.faces.partial.ajax":"true",
    "javax.faces.source":"serviceContents:scheduleDtl:j_idt68",
    "javax.faces.partial.execute":"@all",
    "javax.faces.partial.render":"serviceContents:scheduleDtl serviceContents:msgs",
    "serviceContents:scheduleDtl:j_idt68":"serviceContents:scheduleDtl:j_idt68",
    "serviceContents":"serviceContents",
    "serviceContents:j_idt62_input":"1",
    "serviceContents:depts_input":"",
    "javax.faces.ViewState": viewstate or ""
}

r = safe_post(URL, payload_search)
search_text = r.text
viewstate = get_viewstate(r.text) or viewstate
print("تم البحث — ViewState updated:", bool(viewstate))

m = re.search(r'rowCount:(\d+)', search_text)
if not m:
    raise RuntimeError("لم يتم العثور على rowCount")
total = int(m.group(1))
pages = (total + 49) // 50
print("عدد المواد:", total, "عدد الصفحات:", pages)

# تغيير الصفوف إلى 50
base_payload = {
    "javax.faces.partial.ajax":"true",
    "javax.faces.source":"serviceContents:scheduleDtl",
    "javax.faces.partial.execute":"serviceContents:scheduleDtl",
    "javax.faces.partial.render":"serviceContents:scheduleDtl",
    "serviceContents:scheduleDtl":"serviceContents:scheduleDtl",
    "serviceContents:scheduleDtl_pagination":"true",
    "serviceContents:scheduleDtl_first":"0",
    "serviceContents:scheduleDtl_rows":"50",
    "serviceContents:scheduleDtl_skipChildren":"true",
    "serviceContents:scheduleDtl_encodeFeature":"true",
    "serviceContents":"serviceContents",
    "serviceContents:scheduleDtl_rppDD":"50",
    "javax.faces.ViewState": viewstate or ""
}

print("تغيير عدد الصفوف إلى 50...")
r = safe_post(URL, base_payload)
viewstate = get_viewstate(r.text) or viewstate

# تحميل الصفحات
all_rows = []
for page in range(pages):
    first = page * 50
    print(f"جلب صفحة {page+1} من {pages} (first={first})")

    payload_page = base_payload.copy()
    payload_page["serviceContents:scheduleDtl_first"] = str(first)
    payload_page["javax.faces.ViewState"] = viewstate or ""

    r = safe_post(URL, payload_page)
    rows = extract_rows_from_partial(r.text)

    if not rows:
        r_full = SESSION.get(URL, headers=HEADERS, timeout=20)
        rows = extract_rows_from_fragment(r_full.text)

    print("  مستخرجة:", len(rows))
    all_rows.extend(rows)
    viewstate = get_viewstate(r.text) or viewstate
    time.sleep(0.35)

print("المجموع الكلي للصفوف:", len(all_rows))

# بناء القواميس
items = []
for r in all_rows:
    d = {KEYS[i]: r[i] if i < len(r) else "" for i in range(len(KEYS))}
    items.append(d)

# إضافة الأعمدة الجديدة
for item in items:
    days, start12, end12, _ = parse_time_field(item["time"])
    item["days"] = days
    item["start_time"] = start12
    item["end_time"] = end12

# إنشاء DataFrame
df = pd.DataFrame(items, columns=[
    "code","name","activity","days","start_time","end_time",
    "room","section","teacher"
])

# حفظ الملفات
df.to_excel("IUST_schedule_full.xlsx", index=False)
df.to_csv("IUST_schedule_full.csv", index=False, encoding="utf-8-sig")

print("تم حفظ IUST_schedule_full.xlsx و IUST_schedule_full.csv بنجاح")
