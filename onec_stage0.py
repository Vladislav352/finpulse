#!/usr/bin/env python3
"""1С КА, этап 0: карта опубликованных объектов OData. ТОЛЬКО GET $metadata. Пароль не печатается.
Результат: 1c_metadata_summary.md + сырой 1c_metadata.xml (локально). Лог: 1c_pull.log (URL без пароля, код, размер).
"""
import os, sys, base64, urllib.request, datetime as dt, re, collections, xml.etree.ElementTree as ET
HERE = os.path.dirname(os.path.abspath(__file__))
def env():
    cfg = {}
    for line in open(os.path.expanduser("~/.config/finpulse/1c.env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); cfg[k.strip()] = v.strip()
    return cfg
def log(msg):
    with open(os.path.join(HERE, "1c_pull.log"), "a") as f: f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    print(msg, flush=True)
cfg = env(); base = cfg["ONEC_BASE"].rstrip("/")
auth = "Basic " + base64.b64encode(f"{cfg['ONEC_USER']}:{cfg['ONEC_PASS']}".encode("utf-8")).decode()
url = base + "/odata/standard.odata/$metadata"
req = urllib.request.Request(url, headers={"Authorization": auth, "Accept": "application/xml"})
try:
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read(); log(f"GET {url} -> {r.status} {len(data)} bytes")
except urllib.error.HTTPError as e:
    log(f"GET {url} -> {e.code} {e.read()[:200]!r}"); sys.exit(1)
open(os.path.join(HERE, "1c_metadata.xml"), "wb").write(data)
ns = {"edm": "http://schemas.microsoft.com/ado/2009/11/edm"}
root = ET.fromstring(data)
ets = [e.get("Name") for e in root.iter("{http://schemas.microsoft.com/ado/2009/11/edm}EntitySet")]
if not ets:  # другой namespace edm
    ets = [e.get("Name") for e in root.iter() if e.tag.endswith("EntitySet")]
groups = collections.Counter(n.split("_")[0] for n in ets)
keys = {"Номенклатур": [], "Склад": [], "Организац": [], "ТоварыНаСклад": [], "ТоварыОрганизац": [], "ДенежныеСредств": [], "СтатьиДвиженияДенежныхСредств": [], "ЗаказПоставщик": [], "ЗаказКлиент": [], "Контрагент": [], "Свободные": [], "Остатки": []}
for n in ets:
    for k in keys:
        if k.lower() in n.lower(): keys[k].append(n)
lines = [f"# 1С КА — карта OData (этап 0), {dt.date.today()}", "", f"Опубликовано сущностей: **{len(ets)}**. Размер metadata: {len(data)//1024} КБ.", "", "## По типам объектов", ""]
for g, c in groups.most_common(): lines.append(f"- {g}: {c}")
lines += ["", "## Что важно для Фин.пульса", ""]
for k, v in keys.items():
    if v: lines.append(f"**{k}** ({len(v)}): " + ", ".join(sorted(v)[:25]) + (" …" if len(v) > 25 else ""))
lines += ["", "## Все сущности", ""] + [f"- {n}" for n in sorted(ets)]
open(os.path.join(HERE, "1c_metadata_summary.md"), "w").write("\n".join(lines))
print("\n".join(lines[:40]))
