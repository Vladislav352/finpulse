#!/usr/bin/env python3
"""Выгрузка факта из TrueStats API в таблицу-канон «План продаж и заказов» (аккаунт компании).

Листы, которые скрипт ПОЛНОСТЬЮ перезаписывает при каждом запуске (руками не править):
  TS_факт_SKU      — период × кабинет × артикул: реализация, продажи, себестоимость, комиссия,
                     логистика, хранение, реклама, налог, прибыль, маржинальность, ДРР, остатки
  TS_кабинеты      — период × кабинет, итоги
  TS_месяцы        — помесячная сводка по всем кабинетам компании (последние 12 месяцев)
  TS_остатки       — снимок остатков по складам на дату запуска
  TS_лог           — когда и что обновлялось

Периоды: текущий месяц (с 1-го по сегодня) и три предыдущих полных месяца.
Секреты: ~/.config/truestats/secrets.env (TRUESTATS_TOKEN), Google — token.json.
Запуск: python3 truestats_pull.py [--dry-run]
"""
import json, os, sys, time, datetime as dt, urllib.request, urllib.parse, subprocess, calendar

HOME = os.path.expanduser("~")
SHEET_ID = "PUT_YOUR_SHEET_ID_HERE"
TS_BASE = "https://api.truestats.ru"
# Кабинеты компании в TrueStats: id -> (название, площадка)
ACCOUNTS = {1001: ("Бренд Б Ozon", "Ozon"), 1002: ("Бренд Б WB", "WB"), 1003: ("Бренд Б ЯндексМаркет", "YM"),
            1004: ("Бренд А Ozon", "Ozon"), 1005: ("Бренд А WB", "WB")}
DRY = "--dry-run" in sys.argv
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "truestats_pull.log")

def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f: f.write(line + "\n")

def ts_token():
    for line in open(os.path.join(HOME, ".config/truestats/secrets.env")):
        if line.startswith("TRUESTATS_TOKEN="): return line.split("=", 1)[1].strip()
    raise SystemExit("TRUESTATS_TOKEN не найден")

def ts(method, path, body=None, retries=3):
    for attempt in range(retries):
        req = urllib.request.Request(TS_BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers={"X-Api-Token": TOKEN, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            txt = e.read().decode()[:300]
            if e.code == 429 or e.code >= 500: time.sleep(5 * (attempt + 1)); continue
            raise SystemExit(f"TrueStats {e.code} {path}: {txt}")
        except Exception as e:
            if attempt == retries - 1: raise
            time.sleep(5)
    raise SystemExit(f"TrueStats: не удалось {path}")

def g_token():
    subprocess.run(["bash", os.path.join(HOME, "gdrive_refresh.sh")], check=True, capture_output=True)
    return json.load(open(os.path.join(HOME, ".config/gdrive-claude/token.json")))["access_token"]

def gs(method, path, body=None):
    req = urllib.request.Request(f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}{path}",
                                 data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": "Bearer " + GTOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r: return json.load(r)
    except urllib.error.HTTPError as e: raise SystemExit(f"Sheets {e.code} {path}: {e.read().decode()[:400]}")

def periods(today):
    out = [("MTD", today.replace(day=1), today)]
    y, m = today.year, today.month
    for _ in range(3):
        m -= 1
        if m == 0: m, y = 12, y - 1
        out.append((f"{y}-{m:02d}", dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])))
    return out

def num(x):
    try: return round(float(x), 2)
    except (TypeError, ValueError): return ""

SKU_COLS = ["Период", "С", "По", "Кабинет", "Площадка", "Артикул", "nmId", "Реализация до СПП", "Продажи (к оплате)", "Продано шт", "Заказов шт",
            "Себестоимость продаж", "Комиссия", "Логистика", "Хранение", "Реклама", "Налог", "Прибыль", "Выплата на р/с", "К перечислению", "Маржинальность %", "Рентабельность с/с %",
            "ДРР %", "Ср. цена до СПП", "Ср. цена после СПП", "Остаток МП шт", "В пути к клиенту шт", "Себест. ед.", "Обновлено"]

def sku_row(period, d1, d2, acc_id, r, now):
    name, mp = ACCOUNTS[acc_id]
    cost = num(r.get("costOfSales")); profit = num(r.get("profit"))
    roc = round(profit / cost * 100, 1) if isinstance(cost, float) and cost and isinstance(profit, float) else ""
    return [period, d1.isoformat(), d2.isoformat(), name, mp, r.get("vendorCode") or "", r.get("article"),
            num(r.get("realisation")), num(r.get("sales")), r.get("salesCount"), r.get("ordersCount"),
            cost, num(r.get("commission")), num(r.get("logistics")), num(r.get("storage")), num(r.get("advertisingExpenseSum")),
            num(r.get("tax")), profit, num(r.get("totalPaid")), num(r.get("toTransfer")), num(r.get("marginality")), roc, num(r.get("drr")),
            num(r.get("averagePriceBeforeSPP")), num(r.get("averagePriceAfterSPP")), r.get("stockBalance"), r.get("stockBalanceInWayToClient"),
            num(r.get("cost")), now]

def main():
    global TOKEN, GTOKEN
    TOKEN = ts_token()
    today = dt.date.today(); now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    sku_rows, acc_rows = [SKU_COLS], [SKU_COLS[:5] + SKU_COLS[7:]]
    for period, d1, d2 in periods(today):
        for acc_id in ACCOUNTS:
            body = {"dateFrom": d1.isoformat(), "dateTo": d2.isoformat(), "groupBy": "nm_id", "page": 1, "limit": 500,
                    "financialMod": True, "filters": {"accounts": [acc_id]}}
            r = ts("POST", "/reporting/main/products", body)
            rows = [x for x in r.get("byProduct", []) if (x.get("realisation") or x.get("ordersCount") or x.get("stockBalance"))]
            for x in rows: sku_rows.append(sku_row(period, d1, d2, acc_id, x, now))
            st = r.get("stats") or {}
            if st.get("realisation") or st.get("ordersCount"):
                row = sku_row(period, d1, d2, acc_id, st, now); acc_rows.append(row[:5] + row[7:])
            log(f"{period} {ACCOUNTS[acc_id][0]}: {len(rows)} SKU, реализация {st.get('realisation')}")
            time.sleep(1.1)  # лимит 60 req/min
    # помесячная сводка по всем кабинетам компании
    months = []
    y, m = today.year, today.month
    for _ in range(12):
        months.append(f"{y}-{m:02d}-01"); m -= 1
        if m == 0: m, y = 12, y - 1
    agg = ts("POST", "/reporting/aggregated-view/month", {"months": sorted(months), "filters": {"accounts": list(ACCOUNTS)}, "financialMod": True})
    res = agg.get("result", [])
    mkeys = ["realisation", "sales", "salesCount", "ordersCount", "costOfSales", "commission", "logistics", "storage", "advertisingExpenseSum", "tax", "profit", "marginality", "drr", "totalPaid", "toTransfer"]
    mhdr = ["Месяц", "Дней", "Реализация до СПП", "Продажи (к оплате)", "Продано шт", "Заказов шт", "Себестоимость продаж", "Комиссия", "Логистика", "Хранение", "Реклама", "Налог", "Прибыль", "Маржинальность %", "ДРР %", "Выплата на р/с", "К перечислению", "Обновлено"]
    month_rows = [mhdr]
    for item in sorted(res, key=lambda x: x.get("month") or ""):
        key = (item.get("month") or item.get("groupKey") or "")[:7]
        month_rows.append([key, item.get("daysCount")] + [num(item.get(k)) if k not in ("salesCount", "ordersCount") else item.get(k) for k in mkeys] + [now])
    log(f"месяцы: {len(res)} строк")
    # остатки
    stock_rows = [["Дата", "Артикул", "articleId", "Всего шт", "В транзите", "К клиенту", "От клиента", "Остатки по складам (json)", "Обновлено"]]
    page = 1
    while True:
        s = ts("POST", "/warehouse/stock-balance/v2", {"page": page, "limit": 100, "stockAvailability": "in_stock", "skipSettings": True, "accountIds": list(ACCOUNTS)})
        wh = {w["id"]: w["name"] for w in s.get("warehouses", [])}
        for it in s.get("items", []):
            q = {wh.get(int(k), k): v for k, v in (it.get("quantities") or {}).items() if v}
            stock_rows.append([today.isoformat(), it.get("vendorCode"), it.get("articleId"), it.get("totalQuantity"), it.get("totalInTransit"),
                               it.get("totalInWayToClient"), it.get("totalInWayFromClient"), json.dumps(q, ensure_ascii=False), now])
        pg = s.get("pagination") or {}
        if page * 100 >= (pg.get("total") or 0): break
        page += 1; time.sleep(1.1)
    log(f"остатки: {len(stock_rows)-1} артикулов")
    if DRY:
        print(json.dumps({"sku": len(sku_rows) - 1, "acc": len(acc_rows) - 1, "months": len(month_rows) - 1, "stock": len(stock_rows) - 1}, ensure_ascii=False)); return
    # запись в таблицу
    GTOKEN = g_token()
    meta = gs("GET", "?fields=sheets.properties(sheetId,title)")
    have = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    reqs = []
    for i, (title, sid) in enumerate([("TS_факт_SKU", 910001), ("TS_кабинеты", 910002), ("TS_месяцы", 910003), ("TS_остатки", 910004), ("TS_лог", 910005)]):
        if title not in have:
            reqs.append({"addSheet": {"properties": {"title": title, "sheetId": sid, "gridProperties": {"frozenRowCount": 1}, "tabColor": {"red": 0.55, "green": 0.35, "blue": 0.8}}}})
            reqs.append({"addProtectedRange": {"protectedRange": {"range": {"sheetId": sid}, "description": "Лист заполняется автоматически из TrueStats API, руками не править", "warningOnly": True}}})
    if reqs: gs("POST", ":batchUpdate", {"requests": reqs})
    for title in ["TS_факт_SKU", "TS_кабинеты", "TS_месяцы", "TS_остатки"]:
        gs("POST", f"/values/{urllib.parse.quote(title)}!A1:ZZ:clear", {})
    gs("POST", "/values:batchUpdate", {"valueInputOption": "RAW", "data": [
        {"range": "TS_факт_SKU!A1", "values": sku_rows}, {"range": "TS_кабинеты!A1", "values": acc_rows},
        {"range": "TS_месяцы!A1", "values": month_rows}, {"range": "TS_остатки!A1", "values": stock_rows}]})
    gs("POST", f"/values/{urllib.parse.quote('TS_лог!A1')}:append?valueInputOption=RAW", {"values": [[now, f"SKU {len(sku_rows)-1}, кабинеты {len(acc_rows)-1}, месяцы {len(month_rows)-1}, остатки {len(stock_rows)-1}", "ok"]]})
    log("записано в таблицу")
    json.dump({"sku": sku_rows, "acc": acc_rows, "months": month_rows, "stock": stock_rows},
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "truestats_last_pull.json"), "w"), ensure_ascii=False)

if __name__ == "__main__":
    try: main()
    except SystemExit as e:
        log(f"ОШИБКА: {e}"); raise
