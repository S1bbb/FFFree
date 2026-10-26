#!/usr/bin/env python3
"""Скрейпер расписания физфака МГУ (ras.phys.msu.ru) -> schedule.json.

Запуск:  python3 scraper.py [-o data/schedule.json] [--cache DIR]

Каждая запись:
  {"group", "course", "day", "pair", "week", "subject", "kind", "room", "teacher"}
  + "raw" — исходный текст ячейки, если её не удалось однозначно разобрать.

day: 1–6 (Пн–Сб), pair: 1–6, week: "num" | "den" | "both".
Если пара в таблице разделена на две строки, верхняя считается числителем,
нижняя — знаменателем.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

BASE = "http://ras.phys.msu.ru/table"
PAGES = (
    [(c, f"{c}/{p}.htm") for c in range(1, 7) for p in (1, 2)]
    + [(5, f"5/2/{i}.htm") for i in range(1, 12)]
    + [(6, f"6/1/{i}.htm") for i in range(1, 14)]
)
PAIR_BY_START = {"9:00": 1, "10:50": 2, "13:30": 3, "15:20": 4, "17:05": 5, "18:55": 6}
CELL_CLASS = re.compile(r"^td(item|small)\d+$")
# латинские буквы, похожие на кириллические, в номерах аудиторий и групп
LAT2CYR = str.maketrans("aAbBcCeEHkKMmoOpPTxXy", "аАвВсСеЕНкКМмоОрРТхХу")


# ---------- загрузка ----------

def decode(content: bytes, http_charset: str | None) -> str:
    """Кодировка «по странице»: сервер объявляет UTF-8 в заголовке и windows-1251
    в <meta>, а реальные байты сейчас UTF-8. Проверяем, что байты действительно
    валидны в объявленной кодировке, иначе пробуем следующую."""
    head = content[:4096].decode("ascii", "ignore")
    m = re.search(r"charset\s*=\s*['\"]?([\w-]+)", head, re.I)
    candidates = [c for c in ("utf-8", http_charset, m and m.group(1), "windows-1251") if c]
    for enc in candidates:
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("windows-1251", "replace")


def fetch(session: requests.Session, path: str, cache: Path | None) -> str | None:
    url = f"{BASE}/{path}"
    r = session.get(url, timeout=30, allow_redirects=False)
    if r.status_code in (301, 302, 303, 307, 308):
        # кафедральные страницы, которых нет, редиректят на страницу потока
        print(f"  {path}: {r.status_code} -> {r.headers.get('Location')}, пропуск", file=sys.stderr)
        return None
    r.raise_for_status()
    text = decode(r.content, r.encoding if "charset" in r.headers.get("content-type", "") else None)
    if cache:
        out = cache / path.replace("/", "_")
        out.write_text(text, encoding="utf-8")
    return text


# ---------- нормализация ----------

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def norm_room(room: str) -> str:
    r = clean(room)
    if re.search(r"Хохлов|^ЦФА$", r, re.I):
        return "Хохлова"
    if re.fullmatch(r"[СсЮюCc][Фф][АаAa]", r):
        return r[0].translate(LAT2CYR).upper() + "ФА"
    # 5-33а, Н-9, Ц-39а: префикс корпуса заглавной, литера строчной, дефис
    m = re.fullmatch(r"([НнЦцHh]|\d)\s*[-–—]?\s*(\d+)\s*([а-яa-z]?)", r, re.I)
    if m:
        pre = m.group(1).translate(LAT2CYR).upper()
        return f"{pre}-{m.group(2)}{m.group(3).translate(LAT2CYR).lower()}"
    return r  # Каф., Л.каб., ГАИШ ..., кр.корп, КНО, ЛКВЭ, КВЭ и прочее — как есть


GROUP_RE = r"\d{3}\s?(?:[МмM][АаБбaAбБ]?|[абвАБВaA])?"


def norm_group(g: str) -> str:
    g = clean(g).replace(" ", "").translate(LAT2CYR)
    m = re.fullmatch(r"(\d{3})([Мм]?)(.?)", g)
    return f"{m.group(1)}{m.group(2).upper()}{m.group(3).lower()}" if m else g


def header_groups(text: str) -> list[str]:
    """'302-каф. математики/304-каф....' -> ['302', '304']; '527, 527 б -каф.' -> ['527', '527б']."""
    t = clean(text).translate(LAT2CYR)
    out = []
    for m in re.finditer(r"(?<!\d)([1-6]\d\d)(?:\s?([абв]))?(?![\dа-яА-Я])", t):
        g = m.group(1) + (m.group(2) or "")
        if g not in out:
            out.append(g)
    return out


PREFIX_RE = re.compile(rf"^((?:{GROUP_RE})(?:\s*(?:[+,]|и)\s*(?:{GROUP_RE}))*)\s*[-–]\s+(.+)$")
LOOSE_GROUP_RE = re.compile(rf"^(?:{GROUP_RE})\b")
TITLE_RE = re.compile(
    r"(?:^|\s)(?=(?:проф|доц|асс|ст\. ?преп|преп|асп|акад|чл\.-корр|"
    r"(?:гл\.|вед\.|ст\.|мл\.)? ?н\. ?с|науч\. сотр)\.)"
)


def kind_of(subject: str) -> str | None:
    s = subject.lower()
    if "военн" in s:
        return "military"
    if "физическая культура" in s:
        return "pe"
    if "межфакультетск" in s:
        return None
    if re.search(r"практикум|лаборатория специализации|спецпрак", s):
        return "practicum"
    if re.search(r"\bс/к\b", s):
        return "special_course"
    if re.search(r"\bд/с\b", s):
        return "specialization"
    if re.search(r"\bфтд\b", s):
        return "optional"
    if "семинар" in s:
        return "seminar"
    letters = re.sub(r"[^А-Яа-яЁё]", "", re.sub(r"\(.*?\)", "", subject))
    if len(letters) >= 4:
        upper = sum(ch.isupper() for ch in letters) / len(letters)
        # на сайте лекции набраны заглавными, семинары — обычным регистром
        if upper > 0.6:
            return "lecture"
        first_word = re.match(r"[А-ЯЁа-яё]+", subject)
        if first_word and len(first_word.group()) > 2 and first_word.group().isupper():
            return "lecture"  # «ТЕОРИЯ колебаний ...», «МЕХАНИКА сплошных сред»
        return "seminar"
    return None


# ---------- разбор ячейки ----------

def parse_entry(el: Tag, col_groups: list[str]) -> list[dict]:
    """Одна запись расписания (td без вложенной таблицы) -> список записей по группам."""
    text = clean(el.get_text(" "))
    if not text:
        return []
    nobrs = el.find_all("nobr")
    raw = None
    if len(nobrs) == 1:
        nb = nobrs[0]
        before, after = [], []
        target = before
        for node in el.contents:
            if node is nb:
                target = after
                continue
            target.append(node.get_text(" ") if isinstance(node, Tag) else str(node))
        subject = clean("".join(before))
        room = norm_room(nb.get_text(" "))
        teacher = clean("".join(after)) or None
    elif not nobrs:
        room = None
        m = TITLE_RE.search(text)
        subject, teacher = (clean(text[: m.start()]), clean(text[m.start():])) if m else (text, None)
    else:
        return [dict(group=g, subject=None, kind=None, room=None, teacher=None, raw=text) for g in col_groups]

    groups = col_groups
    m = PREFIX_RE.match(subject)
    if m:
        groups = [norm_group(g) for g in re.split(r"\s*(?:[+,]|\sи\s)\s*", m.group(1)) if g.strip()]
        subject = clean(m.group(2))
    if LOOSE_GROUP_RE.match(subject) or re.search(rf"(?:^|[\s,]){GROUP_RE}\s", subject):
        # «102М Д/С, 135М,142М С/К по выбору», «101М, 143М Д/С, 107Мб С/К …»:
        # разные виды занятий для разных групп в одной ячейке — однозначно не разложить
        raw = text
        if not m:
            named = [norm_group(g) for g in re.findall(rf"(?<!\d)({GROUP_RE})(?![\dа-яА-Я])", subject)]
            groups = list(dict.fromkeys(named)) or col_groups
    return [
        dict(group=g, subject=subject or None, kind=None if raw else kind_of(subject),
             room=room, teacher=teacher, **({"raw": raw} if raw else {}))
        for g in groups
    ]


def parse_cell(td: Tag, col_groups: list[str]) -> list[dict]:
    inner = td.find("table")
    if inner is None:
        return parse_entry(td, col_groups)
    out = []
    # вложенная таблица — параллельные подгруппы в одно и то же время
    for sub in inner.find_all("td"):
        if sub.find("table") is None:
            out += parse_entry(sub, col_groups)
    return out


# ---------- разбор страницы ----------

def build_grid(table: Tag):
    """Раскладывает строки таблицы в сетку с учётом rowspan/colspan.
    Возвращает list[list[(td, row0, col0) | None]]."""
    # на кафедральных страницах <tr> заголовка не закрыт, и html.parser вкладывает
    # следующие строки внутрь него — поэтому берём все tr, чья ближайшая таблица — эта
    rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    grid: dict[tuple[int, int], tuple] = {}
    for r, tr in enumerate(rows):
        c = 0
        for td in tr.find_all("td", recursive=False):
            while (r, c) in grid:
                c += 1
            rs, cs = int(td.get("rowspan", 1)), int(td.get("colspan", 1))
            for dr in range(rs):
                for dc in range(cs):
                    grid[(r + dr, c + dc)] = (td, r, c)
            c += cs
    return rows, grid


def parse_page(html: str, course: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    header = soup.find("tr", class_="tdheader")
    table = header.find_parent("table")
    rows, grid = build_grid(table)
    hdr_r = rows.index(header)

    col_groups: dict[int, list[str]] = {}
    for (r, c), (td, r0, c0) in grid.items():
        if r == hdr_r and r0 == r and c0 == c:
            g = header_groups(td.get_text(" "))
            if g and "ГРУППЫ" not in td.get_text():
                col_groups[c] = g

    # строки -> (день, пара, номер подстроки, число подстрок)
    slot: dict[int, tuple[int, int, int, int]] = {}
    day = 0
    pair_order = 0
    for r in range(len(rows)):
        first = grid.get((r, 0))
        if first:
            img = first[0].find("img")
            m = img and re.search(r"day(\d)", img.get("src", ""))
            if m and first[1] == r:
                day, pair_order = int(m.group(1)), 0
        t = grid.get((r, 1))
        if not t or "tdtime" not in (t[0].get("class") or []):
            continue
        td, r0, _ = t
        if r0 == r:
            pair_order += 1
        start = clean(td.get_text(" ")).split(" ")[0]
        pair = PAIR_BY_START.get(start, pair_order)
        slot[r] = (day, pair, r - r0, int(td.get("rowspan", 1)))

    records = []
    seen = set()
    for (r, c), (td, r0, c0) in sorted(grid.items(), key=lambda kv: kv[0]):
        if c not in col_groups or not CELL_CLASS.match(" ".join(td.get("class") or [])):
            continue
        key = (id(td), r0)
        if key in seen:
            continue
        seen.add(key)
        # все колонки и строки, которые покрывает ячейка
        cols = sorted({cc for (rr, cc), v in grid.items() if v[0] is td and cc in col_groups})
        cover = sorted({rr for (rr, cc), v in grid.items() if v[0] is td and rr in slot})
        groups = [g for cc in cols for g in col_groups[cc]]
        groups = list(dict.fromkeys(groups))
        by_pair: dict[tuple[int, int], set[int]] = {}
        n_sub: dict[tuple[int, int], int] = {}
        for rr in cover:
            d, p, sub, n = slot[rr]
            by_pair.setdefault((d, p), set()).add(sub)
            n_sub[(d, p)] = n
        entries = parse_cell(td, groups)
        for (d, p), subs in sorted(by_pair.items()):
            if n_sub[(d, p)] == 1 or len(subs) == n_sub[(d, p)]:
                week = "both"
            else:
                week = "num" if subs == {0} else "den"
            for e in entries:
                rec = {"group": e["group"], "course": course, "day": d, "pair": p, "week": week,
                       "subject": e["subject"], "kind": e["kind"], "room": e["room"], "teacher": e["teacher"]}
                if "raw" in e:
                    rec["raw"] = e["raw"]
                records.append(rec)
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default="data/schedule.json")
    ap.add_argument("--cache", help="папка для сохранения скачанных страниц (UTF-8)")
    args = ap.parse_args()
    cache = Path(args.cache) if args.cache else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    all_records, seen = [], set()
    for course, path in PAGES:
        html = fetch(session, path, cache)
        if html is None:
            continue
        recs = parse_page(html, course)
        new = 0
        for rec in recs:
            k = json.dumps(rec, ensure_ascii=False, sort_keys=True)
            if k not in seen:
                seen.add(k)
                all_records.append(rec)
                new += 1
        print(f"  {path}: {new} записей", file=sys.stderr)

    if not all_records:
        sys.exit("Ни одной записи — страницы не разобрались, файл не перезаписан")
    all_records.sort(key=lambda x: (x["course"], x["group"], x["day"], x["pair"], x["week"]))
    # одна запись на строку: файл компактный, а изменения расписания читаются в git diff
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in all_records)
    Path(args.output).write_text(f"[\n{lines}\n]\n", encoding="utf-8")
    n_raw = sum("raw" in r for r in all_records)
    print(f"Сохранено {len(all_records)} записей в {args.output} (с raw: {n_raw})", file=sys.stderr)


if __name__ == "__main__":
    main()
