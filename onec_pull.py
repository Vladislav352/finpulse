#!/usr/bin/env python3
"""1С КА → остатки склада фулфилмента (этап 1). ТОЛЬКО GET к стандартному OData.
Запросы за прогон: Catalog_Склады, Catalog_Организации, Catalog_Номенклатура (кэш на сутки), Balance регистра ТоварыНаСкладах по складу ФФ.
Результат: onec_last_pull.json рядом + лист `1C_остатки_ФФ` в таблице-каноне. Лог: 1c_pull.log (без пароля).
Запуск: python3 onec_pull.py [--dry-run]
"""
import os, sys, json, base64, urllib.request, urllib.parse, datetime as dt, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__)); HOME = os.path.expanduser("~")
SHEET_ID = "PUT_YOUR_SHEET_ID_HERE"
WAREHOUSE_HINT = "ФФ МСК"   # искомый склад по подстроке имени
DRY = "--dry-run" in sys.argv

def log(m):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}"; print(line, flush=True)
    open(os.path.join(HERE, "1c_pull.log"), "a").write(line + "\n")

def env():
    cfg = {}
    for line in open(os.path.join(HOME, ".config/finpulse/1c.env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line: k, v = line.split("=", 1); cfg[k.strip()] = v.strip()
    return cfg
CFG = env(); BASE = CFG["ONEC_BASE"].rstrip("/") + "/odata/standard.odata/"
AUTH = "Basic " + base64.b64encode(f"{CFG['ONEC_USER']}:{CFG['ONEC_PASS']}".encode()).decode()

def get(path, params):
    """GET к OData. Только чтение. $format=json всегда."""
    q = dict(params); q["$format"] = "json"
    url = BASE + urllib.parse.quote(path, safe="/(),='") + "?" + "&".join(f"{urllib.parse.quote(k, safe='$')}={urllib.parse.quote(str(v), safe='(),=\'')}" for k, v in q.items())
    req = urllib.request.Request(url, headers={"Authorization": AUTH, "Accept": "application/json"}, method="GET")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = r.read(); log(f"GET {path} {json.dumps({k:v for k,v in params.items()}, ensure_ascii=False)[:160]} -> {r.status} {len(data)} B {time.time()-t0:.1f}s")
            return json.loads(data).get("value", [])
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]; log(f"GET {path} -> {e.code} {body}"); raise SystemExit(f"1С {e.code}: {body}")

def main():
    today = dt.date.today(); now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    # 1. Склады
    wh = get("Catalog_Склады", {"$select": "Ref_Key,Description,IsFolder,DeletionMark,ТипСклада", "$filter": "IsFolder eq false"})
    wh = [w for w in wh if not w.get("DeletionMark")]
    ff = [w for w in wh if WAREHOUSE_HINT.lower() in (w.get("Description") or "").lower()]
    log("склады: " + "; ".join(w["Description"] for w in wh))
    if not ff: raise SystemExit(f"склад с подстрокой «{WAREHOUSE_HINT}» не найден")
    ff = ff[0]
    # 2. Организации
    orgs = get("Catalog_Организации", {"$select": "Ref_Key,Description,ИНН,НаименованиеПолное", "$filter": "DeletionMark eq false"})
    log("организации: " + "; ".join(f"{o['Description']} (ИНН {o.get('ИНН','')})" for o in orgs))
    # 3. Номенклатура (кэш на сутки)
    cache = os.path.join(HERE, "1c_nomenclature_cache.json"); nom = None
    if os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) < 20 * 3600:
        nom = json.load(open(cache))
    if nom is None:
        rows = get("Catalog_Номенклатура", {"$select": "Ref_Key,Description,Артикул,Code,ЕдиницаИзмерения_Key", "$filter": "IsFolder eq false and DeletionMark eq false"})
        nom = {r["Ref_Key"]: {"name": r.get("Description", ""), "art": r.get("Артикул", ""), "code": r.get("Code", "")} for r in rows}
        json.dump(nom, open(cache, "w"), ensure_ascii=False); log(f"номенклатура: {len(nom)} позиций (кэш обновлён)")
    # 4. Остатки по складу ФФ на конец сегодняшнего дня
    period = f"datetime'{today.isoformat()}T23:59:59'"
    cond = f"Склад_Key eq guid'{ff['Ref_Key']}'"
    bal = get(f"AccumulationRegister_ТоварыНаСкладах/Balance(Period={period},Condition='{cond}')", {"$select": "Номенклатура_Key,Характеристика_Key,ВНаличииBalance,КОтгрузкеBalance"})
    rows = [["Дата", "Склад", "Артикул", "Номенклатура", "Код 1С", "В наличии, шт", "К отгрузке, шт", "Доступно, шт", "Обновлено"]]
    tot_avail = tot_ship = 0
    for b in bal:
        n = nom.get(b.get("Номенклатура_Key"), {}); avail = float(b.get("ВНаличииBalance") or 0); ship = float(b.get("КОтгрузкеBalance") or 0)
        if avail == 0 and ship == 0: continue
        tot_avail += avail; tot_ship += ship
        rows.append([today.isoformat(), ff["Description"], n.get("art", ""), n.get("name", b.get("Номенклатура_Key")), n.get("code", ""), avail, ship, avail - ship, now])
    rows[1:] = sorted(rows[1:], key=lambda r: -r[5])
    log(f"остатки {ff['Description']}: позиций {len(rows)-1}, в наличии {tot_avail:.0f} шт, к отгрузке {tot_ship:.0f} шт")
    out = {"date": today.isoformat(), "updated": now, "warehouse": ff["Description"], "warehouses": [w["Description"] for w in wh],
           "orgs": [{"name": o["Description"], "inn": o.get("ИНН", "")} for o in orgs], "rows": rows, "total_avail": tot_avail, "total_ship": tot_ship}
    json.dump(out, open(os.path.join(HERE, "onec_last_pull.json"), "w"), ensure_ascii=False, indent=0)
    if DRY: return
    # 5. Лист в таблице-каноне
    subprocess.run(["bash", os.path.join(HOME, "gdrive_refresh.sh")], check=True, capture_output=True)
    tok = json.load(open(os.path.join(HOME, ".config/gdrive-claude/token.json")))["access_token"]
    def gs(method, path, body=None):
        req = urllib.request.Request(f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}{path}", data=json.dumps(body).encode() if body is not None else None, method=method, headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r: return json.load(r)
        except urllib.error.HTTPError as e: raise SystemExit(f"Sheets {e.code} {path}: {e.read().decode()[:300]}")
    meta = gs("GET", "?fields=sheets.properties(sheetId,title)"); have = {s["properties"]["title"] for s in meta["sheets"]}
    if "1C_остатки_ФФ" not in have:
        gs("POST", ":batchUpdate", {"requests": [{"addSheet": {"properties": {"title": "1C_остатки_ФФ", "sheetId": 930001, "gridProperties": {"frozenRowCount": 1}, "tabColor": {"red": 0.85, "green": 0.2, "blue": 0.2}}}},
                                                 {"addProtectedRange": {"protectedRange": {"range": {"sheetId": 930001}, "description": "Автоматически из 1С КА (OData, только чтение), руками не править", "warningOnly": True}}}]})
    gs("POST", f"/values/{urllib.parse.quote('1C_остатки_ФФ!A1:ZZ')}:clear", {})
    gs("POST", "/values:batchUpdate", {"valueInputOption": "RAW", "data": [{"range": "1C_остатки_ФФ!A1", "values": rows}]})
    log("лист 1C_остатки_ФФ записан")

if __name__ == "__main__":
    main()
