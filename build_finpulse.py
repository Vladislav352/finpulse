#!/usr/bin/env python3
"""Сборка дашборда «Фин.пульс» из truestats_last_pull.json + план из таблицы-канона.
Результат: finpulse.html рядом со скриптом (публикуется как артефакт Claude).
Запуск: python3 build_finpulse.py
"""
import json, os, datetime as dt, urllib.request, urllib.parse, subprocess, collections, calendar, html

HERE = os.path.dirname(os.path.abspath(__file__)); HOME = os.path.expanduser("~")
SHEET_ID = "PUT_YOUR_SHEET_ID_HERE"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
D = json.load(open(os.path.join(HERE, "truestats_last_pull.json")))
today = dt.date.today()
cur = today.strftime("%Y-%m"); mon_ru = ["", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MON = {m: mon_ru[int(m[5:])] + " " + m[:4] for m in [r[0] for r in D["months"][1:]]}
FAM = [("КЛ-МИКС", "Микс КП+ШП"), ("КЛ-ЖНШ", "Женьшень"), ("КЛ-ЖДП", "Концентрат ЖДП"), ("КЛ-КП", "Коленный"), ("КЛ-ШП", "Шейный"),
       ("КН-БЕЛ", "Детокс белый"), ("КН-ЖЕЛ", "Детокс жёлтый"), ("КН-ЛАВ", "Детокс лаванда"), ("КН-ВАС", "Детокс василёк"), ("ТГР", "Тигровый"), ("LH", "Бренд В")]
def fam(code):
    for k, n in FAM:
        if code.startswith(k): return n
    return "Прочее"

# ---------- план из таблицы-канона (лист «Сентябрь план» финдиректора) ----------
def plan_from_sheet():
    try:
        subprocess.run(["bash", os.path.join(HOME, "gdrive_refresh.sh")], check=True, capture_output=True)
        tok = json.load(open(os.path.join(HOME, ".config/gdrive-claude/token.json")))["access_token"]
        rng = urllib.parse.quote("Сентябрь план!A1:M14")
        req = urllib.request.Request(f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{rng}?valueRenderOption=UNFORMATTED_VALUE", headers={"Authorization": "Bearer " + tok})
        vals = json.load(urllib.request.urlopen(req, timeout=60)).get("values", [])
        retail = online = 0.0; margins = []; drr = None
        for r in vals:
            if len(r) > 6 and isinstance(r[6], (int, float)) and isinstance(r[4], (int, float)):
                if len(r) > 10 and isinstance(r[10], (int, float)):
                    online += r[6]; margins.append(r[12] if len(r) > 12 else None); drr = r[10] if isinstance(r[10], (int, float)) else drr
                else: retail += r[6]
        return {"online": online, "retail": retail, "drr": drr, "margin_lo": 13, "margin_hi": 15, "ok": True}
    except Exception as e:
        return {"online": 34710000, "retail": 8550000, "drr": 0.10, "margin_lo": 13, "margin_hi": 15, "ok": False, "err": str(e)}
PLAN = plan_from_sheet()

def transit_from_sheet():
    """Временный источник (по договорённости с финдиректором 14.09): листы «Что уже едет» и «Товарообеспечение»."""
    try:
        tok = json.load(open(os.path.join(HOME, ".config/gdrive-claude/token.json")))["access_token"]
        def vals(rng):
            req = urllib.request.Request(f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{urllib.parse.quote(rng)}?valueRenderOption=UNFORMATTED_VALUE", headers={"Authorization": "Bearer " + tok})
            return json.load(urllib.request.urlopen(req, timeout=60)).get("values", [])
        rows = {}
        for r in vals("Что уже едет!A2:D7"):
            if len(r) > 3 and isinstance(r[3], (int, float)) and r[2]: rows[r[2]] = {"sku": r[2], "cost": r[1], "arrived": r[3], "oct": None}
        for r in vals("Товарообеспечение!A2:E7"):
            if len(r) > 4 and r[2] in rows and isinstance(r[4], (int, float)): rows[r[2]]["oct"] = r[4]
        future = [r for r in vals("Сентябрь!A1:D60") if len(r) > 3 and isinstance(r[2], (int, float)) and isinstance(r[3], str) and "сент" in r[3].lower()]
        return {"rows": list(rows.values()), "future": future, "ok": True}
    except Exception as e:
        return {"rows": [], "future": [], "ok": False, "err": str(e)}
TRANSIT = transit_from_sheet()

def onec_load():
    p = os.path.join(HERE, "onec_last_pull.json")
    if not os.path.exists(p): return None
    d = json.load(open(p)); rows = d["rows"][1:]
    def is_goods(art): return bool(art) and art.split("-")[0] in ("КЛ", "КН", "ТГР", "LH") or (art or "").startswith("КЛ")
    goods = [r for r in rows if is_goods(r[2])]; other = [r for r in rows if not is_goods(r[2])]
    byfam = collections.defaultdict(lambda: [0.0, 0.0])
    for r in goods: byfam[fam(r[2])][0] += float(r[5] or 0); byfam[fam(r[2])][1] += float(r[6] or 0)
    d["goods"], d["other"], d["byfam"] = goods, other, byfam
    d["goods_total"] = sum(float(r[5] or 0) for r in goods); d["other_total"] = sum(float(r[5] or 0) for r in other)
    return d
ONEC = onec_load()

# ---------- агрегаты ----------
hdr = D["sku"][0]; ix = {h: i for i, h in enumerate(hdr)}
def f(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0
def agg(rows, key):
    out = collections.defaultdict(lambda: dict(rev=0, profit=0, cost=0, adv=0, units=0, paid=0, tax=0))
    for r in rows:
        a = out[key(r)]; a["rev"] += f(r[ix["Реализация до СПП"]]); a["profit"] += f(r[ix["Прибыль"]]); a["cost"] += f(r[ix["Себестоимость продаж"]])
        a["adv"] += f(r[ix["Реклама"]]); a["units"] += int(f(r[ix["Продано шт"]])); a["tax"] += f(r[ix["Налог"]])
        a["paid"] += f(r[ix["Выплата на р/с"]]) if "Выплата на р/с" in ix else f(r[ix["Прибыль"]]) + f(r[ix["Себестоимость продаж"]]) + f(r[ix["Налог"]])
    for a in out.values():
        a["margin"] = a["profit"] / a["rev"] * 100 if a["rev"] else 0; a["roc"] = a["profit"] / a["cost"] * 100 if a["cost"] else 0; a["drr"] = a["adv"] / a["rev"] * 100 if a["rev"] else 0
    return out
sku = D["sku"][1:]
mtd = [r for r in sku if r[0] == "MTD"]; prev_m = sorted({r[0] for r in sku if r[0] != "MTD"})[-1]; prev = [r for r in sku if r[0] == prev_m]
T_mtd = agg(mtd, lambda r: "all")["all"]; T_prev = agg(prev, lambda r: "all")["all"]
by_acc_mtd = agg(mtd, lambda r: r[ix["Кабинет"]]); by_acc_prev = agg(prev, lambda r: r[ix["Кабинет"]])
by_fam_prev = agg(prev, lambda r: fam(r[ix["Артикул"]])); by_fam_mtd = agg(mtd, lambda r: fam(r[ix["Артикул"]]))
mix = {h: i for i, h in enumerate(D["months"][0])}
months = [dict(m=r[0], days=r[1], rev=f(r[2]), profit=f(r[12]), margin=f(r[13]), drr=f(r[14]),
               paid=f(r[mix["Выплата на р/с"]]) if "Выплата на р/с" in mix else f(r[12]) + f(r[mix["Себестоимость продаж"]]) + f(r[mix["Налог"]])) for r in D["months"][1:]]
days_in_month = calendar.monthrange(today.year, today.month)[1]; mtd_days = today.day
plan_online = PLAN["online"]; plan_prorata = plan_online * mtd_days / days_in_month
runrate = T_mtd["rev"] / mtd_days * days_in_month if mtd_days else 0
stock = D["stock"][1:]; stock_total = sum(int(f(r[3])) for r in stock); transit_total = sum(int(f(r[4])) for r in stock); toclient = sum(int(f(r[5])) for r in stock)
# остатки по семействам
st_fam = collections.defaultdict(lambda: [0, 0, 0])
for r in stock:
    a = st_fam[fam(r[1] or "")]; a[0] += int(f(r[3])); a[1] += int(f(r[4])); a[2] += int(f(r[5]))
updated = D["sku"][1][-1] if len(D["sku"]) > 1 else ""

def N(x): return f"{int(round(x)):,}".replace(",", "\u202f")
def M(x): return f"{x/1e6:.2f}".replace(".", ",")
def P(x, d=1): return (f"{x:.{d}f}").replace(".", ",")
def status(kind, v):
    if kind == "margin": return ("good", "в цели") if v >= PLAN["margin_lo"] else (("warn", "ниже цели") if v >= 8 else ("crit", "ниже цели"))
    if kind == "drr": return ("good", "в плане") if v <= PLAN["drr"] * 100 + 0.5 else (("warn", "выше плана") if v <= 15 else ("crit", "выше плана"))
    if kind == "pct": return ("good", "по плану") if v >= 90 else (("warn", f"{v:.0f}% плана") if v >= 60 else ("crit", f"{v:.0f}% плана"))
def esc(s): return html.escape(str(s))

# ---------- SVG: месяцы ----------
def bars_svg(ms):
    W, H, pad, gap = 760, 200, 34, 6; n = len(ms); bw = (W - pad * 2) / n - gap; mx = max(m["rev"] for m in ms) or 1
    out = [f'<svg viewBox="0 0 {W} {H+38}" class="chart" role="img" aria-label="Реализация по месяцам">']
    for i in range(5):
        y = pad + (H - pad) * i / 4; val = mx * (1 - i / 4)
        out.append(f'<line x1="{pad}" x2="{W}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/><text x="{pad-6}" y="{y+4:.1f}" class="ax" text-anchor="end">{val/1e6:.0f}</text>')
    for i, m in enumerate(ms):
        x = pad + i * (bw + gap) + gap / 2; h = (H - pad) * m["rev"] / mx; y = H - h
        cls = "mbar cur" if m["m"] == cur else "mbar"
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="3" class="{cls}"><title>{MON.get(m["m"], m["m"])}: {M(m["rev"])} млн ₽, прибыль {M(m["profit"])} млн, маржа {P(m["margin"])}%, ДРР {P(m["drr"])}%</title></rect>')
        if i % 2 == 0 or m["m"] == cur: out.append(f'<text x="{x+bw/2:.1f}" y="{H+16}" class="ax" text-anchor="middle">{m["m"][5:]}.{m["m"][2:4]}</text>')
        if m["rev"] == mx or m["m"] == cur or i == n - 2: out.append(f'<text x="{x+bw/2:.1f}" y="{y-5:.1f}" class="lbl" text-anchor="middle">{m["rev"]/1e6:.1f}</text>')
    out.append(f'<text x="{pad}" y="14" class="ax">млн ₽, реализация до СПП</text></svg>')
    return "".join(out)
def lines_svg(ms):
    W, H, pad = 760, 170, 34; n = len(ms); mx = 30
    def pt(i, v): return pad + (W - pad - 70) * i / (n - 1), pad + (H - pad) * (1 - min(v, mx) / mx)
    out = [f'<svg viewBox="0 0 {W} {H+38}" class="chart" role="img" aria-label="Маржинальность и ДРР по месяцам">']
    for v in (0, 10, 15, 20, 30):
        _, y = pt(0, v); out.append(f'<line x1="{pad}" x2="{W}" y1="{y:.1f}" y2="{y:.1f}" class="grid{" tgt" if v==15 else ""}"/><text x="{pad-6}" y="{y+4:.1f}" class="ax" text-anchor="end">{v}%</text>')
    for key, cls in (("margin", "l1"), ("drr", "l2")):
        d = " ".join(f'{"M" if i==0 else "L"}{pt(i, m[key])[0]:.1f},{pt(i, m[key])[1]:.1f}' for i, m in enumerate(ms))
        out.append(f'<path d="{d}" class="{cls}"/>')
        for i, m in enumerate(ms):
            x, y = pt(i, m[key]); out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="{cls} dot"><title>{MON.get(m["m"], m["m"])}: {"маржа" if key=="margin" else "ДРР"} {P(m[key])}%</title></circle>')
        x, y = pt(n - 1, ms[-1][key]); dy = -6 if (key == "margin") == (ms[-1]["margin"] >= ms[-1]["drr"]) else 12
        out.append(f'<text x="{x+8:.1f}" y="{y+dy:.1f}" class="lbl">{"М " if key=="margin" else "ДРР "}{P(ms[-1][key])}%</text>')
    for i, m in enumerate(ms):
        if i % 2 == 0 or m["m"] == cur: x, _ = pt(i, 0); out.append(f'<text x="{x:.1f}" y="{H+16}" class="ax" text-anchor="middle">{m["m"][5:]}.{m["m"][2:4]}</text>')
    out.append(f'<text x="{pad}" y="14" class="ax">маржинальность и ДРР, %; пунктир — цель маржи 15%</text></svg>')
    return "".join(out)

def rows_html(d, maxrev, show_drr=True):
    out = []
    for name, a in sorted(d.items(), key=lambda kv: -kv[1]["rev"]):
        if a["rev"] < 10000: continue
        w = max(1, round(a["rev"] / maxrev * 100)); mc = "neg" if a["margin"] < 0 else ("low" if a["margin"] < PLAN["margin_lo"] else "ok")
        out.append(f'<div class="r" title="{esc(name)}: {M(a["rev"])} млн ₽, прибыль {M(a["profit"])} млн, маржа {P(a["margin"])}%, ДРР {P(a["drr"])}%">'
                   f'<div class="lbl">{esc(name)}</div><div class="track"><i class="{"neg" if a["profit"]<0 else ""}" style="width:{w}%"></i></div>'
                   f'<div class="vals"><b>{M(a["rev"])}</b><span class="m {mc}">{"+" if a["margin"]>0 else ""}{P(a["margin"])}%</span>' + (f'<span class="m d">ДРР {P(a["drr"],0)}%</span>' if show_drr else "") + '</div></div>')
    return "".join(out)

mtd_st = status("pct", T_mtd["rev"] / plan_prorata * 100 if plan_prorata else 0)
mar_st = status("margin", T_mtd["margin"]); roc_plan = 96; roc_st = ("good", "по плану") if T_mtd["roc"] >= roc_plan * .9 else (("warn", "ниже плана") if T_mtd["roc"] >= roc_plan * .5 else ("crit", "ниже плана"))
drr_st = status("drr", T_mtd["drr"])
pct_mtd = T_mtd["rev"] / plan_prorata * 100 if plan_prorata else 0
last_full = months[-2] if len(months) > 1 and months[-1]["m"] == cur else months[-1]
peak = max(months, key=lambda m: m["rev"])

page = f"""<meta charset="utf-8">
<title>Фин.пульс компании</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600&family=Manrope:wght@600;800&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
:root{{color-scheme:light;--bg:#f5f6f2;--surface:#fcfcfa;--surface-2:#eef1ea;--line:#d9ddd3;--line-strong:#b9c0b3;--ink:#151b17;--ink-2:#4f5a53;--ink-3:#7d877f;--accent:#1f7a48;--accent-ink:#155a35;--accent-soft:#dcefe3;--plan:#2a78d6;--plan-soft:#dbe8f8;--adv:#eb6834;--good:#0ca30c;--warn:#b8800a;--crit:#d03b3b;--crit-soft:#f9e1e1;--warn-soft:#fbefd0;--good-soft:#dff3df;--shadow:0 1px 2px rgba(20,30,24,.06),0 8px 24px -12px rgba(20,30,24,.18);--f-body:"Golos Text",system-ui,-apple-system,"Segoe UI",sans-serif;--f-display:"Manrope","Golos Text",system-ui,sans-serif;--f-mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#141715;--surface:#1c201d;--surface-2:#242925;--line:#333a35;--line-strong:#4a534d;--ink:#eef1ec;--ink-2:#b4bcb6;--ink-3:#7f8981;--accent:#2f9558;--accent-ink:#5fc487;--accent-soft:#1d3327;--plan:#3987e5;--plan-soft:#1c2c44;--adv:#d95926;--good:#3fb53f;--warn:#e0a52c;--crit:#e26262;--crit-soft:#3a2323;--warn-soft:#3a3120;--good-soft:#1f3520;--shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6)}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#141715;--surface:#1c201d;--surface-2:#242925;--line:#333a35;--line-strong:#4a534d;--ink:#eef1ec;--ink-2:#b4bcb6;--ink-3:#7f8981;--accent:#2f9558;--accent-ink:#5fc487;--accent-soft:#1d3327;--plan:#3987e5;--plan-soft:#1c2c44;--adv:#d95926;--good:#3fb53f;--warn:#e0a52c;--crit:#e26262;--crit-soft:#3a2323;--warn-soft:#3a3120;--good-soft:#1f3520;--shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6)}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:var(--f-body);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}}a{{color:var(--accent-ink)}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 24px 64px}}
header{{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px 32px;padding-bottom:20px;border-bottom:1px solid var(--line)}}
.brand{{display:flex;align-items:center;gap:14px}}.leaf{{width:40px;height:40px;border-radius:12px;background:var(--accent);display:grid;place-items:center;flex:none}}.leaf svg{{width:24px;height:24px}}
h1{{font-family:var(--f-display);font-weight:800;font-size:26px;letter-spacing:-.01em;margin:0;line-height:1.15}}.sub{{color:var(--ink-2);margin:2px 0 0;font-size:14px}}
.meta{{display:flex;flex-wrap:wrap;gap:8px}}.chip{{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:12.5px;font-weight:500;border:1px solid var(--line);background:var(--surface);color:var(--ink-2);white-space:nowrap;text-decoration:none}}.chip .dot{{width:8px;height:8px;border-radius:50%;background:var(--ink-3);flex:none}}.chip.ok .dot{{background:var(--good)}}.chip.warn .dot{{background:var(--warn)}}.chip.crit .dot{{background:var(--crit)}}.chip.ok{{color:var(--ink)}}
section{{margin-top:36px}}.sec-head{{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:6px 24px;margin-bottom:14px}}h2{{font-family:var(--f-display);font-weight:800;font-size:19px;margin:0}}.sec-head .note{{color:var(--ink-2);font-size:13.5px;max-width:62ch}}
.eyebrow{{font-size:11.5px;text-transform:uppercase;letter-spacing:.09em;color:var(--ink-3);font-weight:600}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}}.kpi{{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px 14px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:10px}}.kpi .eyebrow{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;flex-wrap:wrap}}
.kpi .num{{font-family:var(--f-display);font-weight:800;font-size:34px;line-height:1;letter-spacing:-.02em;font-variant-numeric:tabular-nums}}.kpi .num small{{font-size:16px;font-weight:600;color:var(--ink-2);margin-left:4px}}
.kpi .row{{display:flex;justify-content:space-between;gap:10px;font-size:13px;color:var(--ink-2);font-variant-numeric:tabular-nums}}.kpi .row b{{color:var(--ink);font-weight:600}}
.bar{{height:6px;border-radius:3px;background:var(--surface-2);position:relative;overflow:hidden}}.bar i{{position:absolute;left:0;top:0;bottom:0;border-radius:3px;background:var(--accent)}}.bar i.adv{{background:var(--adv)}}.bar .tgt{{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--ink)}}
.kpi .src{{font-size:12px;color:var(--ink-3);display:flex;gap:6px;align-items:center;flex-wrap:wrap}}.tag{{font-size:11px;padding:1px 7px;border-radius:6px;border:1px solid var(--line);color:var(--ink-2);background:var(--surface-2);font-weight:500}}.tag.miss{{border-color:transparent;background:var(--crit-soft);color:var(--crit)}}.tag.manual{{border-color:transparent;background:var(--warn-soft);color:var(--warn)}}.tag.auto{{border-color:transparent;background:var(--good-soft);color:var(--good)}}
.status{{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;padding:2px 8px;border-radius:999px;letter-spacing:0;text-transform:none;white-space:nowrap}}.status.crit{{background:var(--crit-soft);color:var(--crit)}}.status.warn{{background:var(--warn-soft);color:var(--warn)}}.status.good{{background:var(--good-soft);color:var(--good)}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px}}.panel{{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px;box-shadow:var(--shadow)}}.panel h3{{font-family:var(--f-display);font-weight:800;font-size:15.5px;margin:0 0 4px}}.panel .desc{{color:var(--ink-2);font-size:13px;margin:0 0 14px}}
.rows{{display:flex;flex-direction:column;gap:9px}}.r{{display:grid;grid-template-columns:minmax(120px,1.1fr) 3fr minmax(190px,auto);gap:12px;align-items:center;font-size:13.5px}}.r .lbl{{color:var(--ink);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.r .track{{height:18px;background:var(--surface-2);border-radius:4px;position:relative;overflow:hidden}}.r .track i{{position:absolute;left:0;top:0;bottom:0;background:var(--accent);border-radius:0 4px 4px 0;min-width:2px}}.r .track i.neg{{background:var(--crit)}}
.r .vals{{display:flex;gap:10px;justify-content:flex-end;font-variant-numeric:tabular-nums;font-family:var(--f-mono);font-size:12.5px;color:var(--ink-2);white-space:nowrap}}.r .vals b{{color:var(--ink);font-weight:500}}.r .vals .m{{min-width:52px;text-align:right}}.r .vals .m.neg{{color:var(--crit);font-weight:500}}.r .vals .m.low{{color:var(--warn);font-weight:500}}.r .vals .m.ok{{color:var(--good);font-weight:500}}.r .vals .m.d{{min-width:64px;color:var(--ink-3)}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;color:var(--ink-2);margin-top:12px}}.legend i{{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-2px;margin-right:6px}}
.chart{{width:100%;height:auto;display:block;overflow:visible}}.chart .grid{{stroke:var(--line);stroke-width:1}}.chart .grid.tgt{{stroke:var(--ink-3);stroke-dasharray:4 4}}.chart .ax{{fill:var(--ink-3);font-size:11px;font-family:var(--f-body)}}.chart .lbl{{fill:var(--ink);font-size:11.5px;font-family:var(--f-mono);font-weight:500}}
.chart .mbar{{fill:var(--accent);opacity:.75}}.chart .mbar.cur{{opacity:1;stroke:var(--ink);stroke-width:1.5;stroke-dasharray:3 3}}.chart .mbar:hover{{opacity:1}}
.chart path.l1{{fill:none;stroke:var(--accent);stroke-width:2}}.chart path.l2{{fill:none;stroke:var(--adv);stroke-width:2}}.chart circle.l1{{fill:var(--accent);stroke:var(--surface);stroke-width:2}}.chart circle.l2{{fill:var(--adv);stroke:var(--surface);stroke-width:2}}
.callout{{margin-top:14px;padding:12px 14px;border-radius:10px;background:var(--warn-soft);color:var(--ink);font-size:13.5px;display:flex;gap:10px;align-items:flex-start}}.callout.info{{background:var(--plan-soft)}}.callout svg{{flex:none;width:18px;height:18px;margin-top:2px}}
.tbl{{width:100%;overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13.5px}}th{{font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--ink-3);font-weight:600;text-align:left;padding:8px 10px;border-bottom:1px solid var(--line-strong);white-space:nowrap}}td{{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}}td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--f-mono);font-size:12.5px}}tr:last-child td{{border-bottom:none}}
.srcs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}.src-card{{border:1px solid var(--line);border-radius:12px;padding:14px 16px;background:var(--surface);display:flex;flex-direction:column;gap:6px}}.src-card .name{{font-weight:600;display:flex;justify-content:space-between;gap:8px;align-items:center}}.src-card .what{{font-size:13px;color:var(--ink-2)}}.src-card .next{{font-size:12.5px;color:var(--ink);padding-top:6px;border-top:1px dashed var(--line)}}
.defs{{columns:2;column-gap:32px;font-size:13.5px;color:var(--ink-2)}}.defs p{{margin:0 0 10px;break-inside:avoid}}.defs code{{font-family:var(--f-mono);font-size:12.5px;color:var(--ink);background:var(--surface-2);padding:1px 6px;border-radius:5px}}
footer{{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);color:var(--ink-3);font-size:12.5px;display:flex;flex-wrap:wrap;gap:8px 24px;justify-content:space-between}}
@media (max-width:640px){{.defs{{columns:1}}.r{{grid-template-columns:1fr}}.r .vals{{justify-content:flex-start}}.kpi .num{{font-size:30px}}}}
</style>
<div class="wrap">
<header><div class="brand"><div class="leaf" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M5 19c0-8 6-13 14-14-1 8-6 14-14 14z" fill="#fff" opacity=".95"/><path d="M5 19c3-4 6-7 10-9" stroke="#1f7a48" stroke-width="1.6" stroke-linecap="round"/></svg></div>
<div><h1>Фин.пульс компании</h1><p class="sub">Выручка, маржинальность, рентабельность себестоимости, ДРР. Факт из TrueStats API по пяти кабинетам компании, план из таблицы-канона. Данные на {esc(updated)}.</p></div></div>
<div class="meta"><a class="chip ok" href="{SHEET_URL}" target="_blank"><span class="dot"></span>План: таблица-канон ↗</a><span class="chip ok"><span class="dot"></span>Факт: TrueStats API, автообновление 2×/день</span><span class="chip {'ok' if PLAN['ok'] else 'warn'}"><span class="dot"></span>{'План сентября: лист финдиректора, подтверждён 14.09' if PLAN['ok'] else 'План сентября: резервные значения'}</span><a class="chip" href="otzyvy.html"><span class="dot" style="background:var(--adv)"></span>Отзывы WB за 30 дней ↗</a><a class="chip" href="finpulse.pdf"><span class="dot"></span>PDF</a></div></header>

<section><div class="sec-head"><h2>{esc(mon_ru[today.month].capitalize())}, факт на {today.day}-е число</h2><p class="note">Маркетплейсы, пять кабинетов, без кабинета с другой товарной группой. Полоса выручки сравнивает факт с планом онлайн, пересчитанным на прошедшие дни месяца. Засечки: цели.</p></div>
<div class="kpis">
<div class="kpi"><div class="eyebrow"><span>Выручка, реализация до СПП</span><span class="status {mtd_st[0]}">◆ {mtd_st[1]}</span></div><div class="num">{M(T_mtd['rev'])}<small>млн ₽</small></div><div class="bar"><i style="width:{min(100,pct_mtd):.0f}%"></i><span class="tgt" style="left:99%"></span></div>
<div class="row"><span>План онлайн на месяц</span><b>{M(plan_online)} млн</b></div><div class="row"><span>План на {mtd_days} дн. / темп на месяц</span><b>{M(plan_prorata)} / {M(runrate)} млн</b></div><div class="src"><span class="tag auto">TrueStats API</span><span class="tag auto">план из канона</span></div></div>
<div class="kpi"><div class="eyebrow"><span>Маржинальность</span><span class="status {mar_st[0]}">◆ {mar_st[1]}</span></div><div class="num">{P(T_mtd['margin'])}<small>%</small></div><div class="bar"><i style="width:{min(100,T_mtd['margin']/20*100):.0f}%"></i><span class="tgt" style="left:65%"></span><span class="tgt" style="left:75%"></span></div>
<div class="row"><span>Цель</span><b>{PLAN['margin_lo']}–{PLAN['margin_hi']}%</b></div><div class="row"><span>Прибыль с начала месяца</span><b>{M(T_mtd['profit'])} млн ₽</b></div><div class="src">Прибыль TrueStats после комиссий, логистики, рекламы, налога · <span class="tag auto">API</span></div></div>
<div class="kpi"><div class="eyebrow"><span>Рентабельность себестоимости</span><span class="status {roc_st[0]}">◆ {roc_st[1]}</span></div><div class="num">{P(T_mtd['roc'],0)}<small>%</small></div><div class="bar"><i style="width:{min(100,T_mtd['roc']/120*100):.0f}%"></i><span class="tgt" style="left:{roc_plan/120*100:.0f}%"></span></div>
<div class="row"><span>План сентября, взвеш.</span><b>≈ {roc_plan}%</b></div><div class="row"><span>Себестоимость проданного</span><b>{M(T_mtd['cost'])} млн ₽</b></div><div class="src">Прибыль / себестоимость продаж · <span class="tag auto">API</span></div></div>
<div class="kpi"><div class="eyebrow"><span>ДРР</span><span class="status {drr_st[0]}">◆ {drr_st[1]}</span></div><div class="num">{P(T_mtd['drr'])}<small>%</small></div><div class="bar"><i class="adv" style="width:{min(100,T_mtd['drr']/25*100):.0f}%"></i><span class="tgt" style="left:{PLAN['drr']*100/25*100:.0f}%"></span></div>
<div class="row"><span>План</span><b>{P(PLAN['drr']*100,0)}% от выручки</b></div><div class="row"><span>Реклама с начала месяца</span><b>{M(T_mtd['adv'])} млн ₽</b></div><div class="src">Все типы кампаний WB и Ozon · <span class="tag auto">API</span></div></div>
<div class="kpi"><div class="eyebrow"><span>Оплата на р/с по факту</span><span class="tag auto">новое</span></div><div class="num">{M(T_mtd['paid'])}<small>млн ₽</small></div><div class="bar"><i style="width:{min(100,T_mtd['paid']/T_mtd['rev']*100 if T_mtd['rev'] else 0):.0f}%"></i></div>
<div class="row"><span>Доля от реализации до СПП</span><b>{P(T_mtd['paid']/T_mtd['rev']*100 if T_mtd['rev'] else 0,0)}%</b></div><div class="row"><span>{MON.get(prev_m, prev_m)}, весь месяц</span><b>{M(T_prev['paid'])} млн ₽</b></div><div class="src">Выплаты площадок = прибыль + себестоимость + налог · <span class="tag auto">API</span></div></div>
</div></section>

<section><div class="sec-head"><h2>Двенадцать месяцев</h2><p class="note">Помесячная сводка TrueStats. Пик — {esc(MON.get(peak['m'],peak['m']))}: {M(peak['rev'])} млн. {esc(MON.get(last_full['m'],last_full['m']).capitalize())}: {M(last_full['rev'])} млн при марже {P(last_full['margin'])}% и ДРР {P(last_full['drr'])}%. Текущий месяц обведён пунктиром, он неполный.</p></div>
<div class="grid2"><div class="panel"><h3>Реализация по месяцам</h3><p class="desc">млн ₽ до СПП</p>{bars_svg(months)}</div>
<div class="panel"><h3>Маржинальность и ДРР</h3><p class="desc">процент от реализации</p>{lines_svg(months)}<div class="legend"><span><i style="background:var(--accent)"></i>маржинальность</span><span><i style="background:var(--adv)"></i>ДРР</span></div></div></div>
<div class="callout"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3l9 16H3L12 3z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M12 10v4M12 17h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div>Выручка падает пятый месяц: с {M(peak['rev'])} млн в марте до {M(last_full['rev'])} млн в августе, это минус {100-last_full['rev']/peak['rev']*100:.0f}%. В августе ДРР вырос до {P(last_full['drr'])}% при марже {P(last_full['margin'])}%. Цель 100 млн в месяц к концу года требует роста в {P(100e6/last_full['rev'])} раза от августа.</div></div></section>

<section><div class="sec-head"><h2>{esc(MON.get(prev_m,prev_m).capitalize())}, последний полный месяц</h2><p class="note">По кабинетам и семействам, выручка в млн ₽, справа маржа и ДРР. Наборы сведены к базовому SKU.</p></div>
<div class="grid2"><div class="panel"><h3>По кабинетам</h3><p class="desc">итого {M(T_prev['rev'])} млн, на р/с {M(T_prev['paid'])} млн, прибыль {M(T_prev['profit'])} млн, маржа {P(T_prev['margin'])}%, ДРР {P(T_prev['drr'])}%</p><div class="rows">{rows_html(by_acc_prev, max(a['rev'] for a in by_acc_prev.values()))}</div></div>
<div class="panel"><h3>По семействам</h3><p class="desc">красное — семейство в убытке</p><div class="rows">{rows_html(by_fam_prev, max(a['rev'] for a in by_fam_prev.values()))}</div></div></div></section>

<section><div class="sec-head"><h2>{esc(mon_ru[today.month].capitalize())} с начала месяца</h2><p class="note">Те же разрезы за текущий месяц. Сравнивать с прошлым месяцем можно только по марже и ДРР, выручка неполная.</p></div>
<div class="grid2"><div class="panel"><h3>По кабинетам</h3><p class="desc">итого {M(T_mtd['rev'])} млн за {mtd_days} дн.</p><div class="rows">{rows_html(by_acc_mtd, max(a['rev'] for a in by_acc_mtd.values()))}</div></div>
<div class="panel"><h3>По семействам</h3><p class="desc">маржа {P(T_mtd['margin'])}%, ДРР {P(T_mtd['drr'])}%</p><div class="rows">{rows_html(by_fam_mtd, max(a['rev'] for a in by_fam_mtd.values()))}</div></div></div></section>

<section><div class="sec-head"><h2>Остатки на складах маркетплейсов</h2><p class="note">Снимок TrueStats на {esc(stock[0][0] if stock else '')}: всего {N(stock_total)} шт на складах WB и Ozon, {N(transit_total)} шт в транзите между складами, {N(toclient)} шт в пути к клиенту. Собственный склад фулфилмента в Москве в TrueStats не заведён, поэтому «товар в пути из Китая» здесь нет — он остаётся в листе «Что уже едет».</p></div>
<div class="panel"><div class="tbl"><table><thead><tr><th>Семейство</th><th class="n">На складах МП, шт</th><th class="n">В транзите, шт</th><th class="n">К клиенту, шт</th><th class="n">Продано за {mtd_days} дн., шт</th><th class="n">Дней запаса</th></tr></thead><tbody>
{''.join(f'<tr><td>{esc(k)}</td><td class="n">{N(v[0])}</td><td class="n">{N(v[1])}</td><td class="n">{N(v[2])}</td><td class="n">{N(by_fam_mtd[k]["units"] if k in by_fam_mtd else 0)}</td><td class="n">{(v[0]/(by_fam_mtd[k]["units"]/mtd_days) if k in by_fam_mtd and by_fam_mtd[k]["units"] else 0):.0f}</td></tr>' for k,v in sorted(st_fam.items(), key=lambda kv:-kv[1][0]))}
</tbody></table></div><div class="callout info"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.6"/><path d="M12 8v5M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div>«Дней запаса» = остаток на складах МП, делённый на средние дневные продажи текущего месяца. Красный флаг РНП: остаток меньше 7 дней.</div></div></section>

{('<section><div class="sec-head"><h2>Склад фулфилмента в Москве, из 1С</h2><p class="note">Остатки регистра «Товары на складах» по складу «' + esc(ONEC["warehouse"]) + '» на ' + esc(ONEC["date"]) + ', чтение через OData дважды в день. Это то, что лежит у Татьяны и ещё не уехало на маркетплейсы. Упаковка, стикеры и шоубоксы вынесены отдельно.</p></div>'
 '<div class="grid2"><div class="panel"><h3>Товар по семействам</h3><p class="desc">всего ' + N(ONEC["goods_total"]) + ' шт пластырей на складе ФФ</p><div class="rows">'
 + "".join(f'<div class="r" title="{esc(k)}: {N(v[0])} шт"><div class="lbl">{esc(k)}</div><div class="track"><i style="width:{max(1, round(v[0] / max(x[0] for x in ONEC["byfam"].values()) * 100))}%"></i></div><div class="vals"><b>{N(v[0])}</b><span class="m d">шт</span></div></div>' for k, v in sorted(ONEC["byfam"].items(), key=lambda kv: -kv[1][0]))
 + '</div></div><div class="panel"><h3>Топ позиций</h3><p class="desc">артикул и остаток, шт</p><div class="tbl"><table><thead><tr><th>Артикул</th><th>Номенклатура</th><th class="n">В наличии</th></tr></thead><tbody>'
 + "".join(f'<tr><td>{esc(r[2])}</td><td>{esc(r[3][:52])}</td><td class="n">{N(r[5])}</td></tr>' for r in ONEC["goods"][:10])
 + f'</tbody></table></div><div class="callout info"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.6"/><path d="M12 8v5M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div>Расходники на складе: {N(ONEC["other_total"])} шт (пакеты, стикеры, шоубоксы, {len(ONEC["other"])} позиций). Полный список по всем {len(ONEC["rows"])-1} позициям — лист 1C_остатки_ФФ в таблице.</div></div></div></div></section>') if ONEC else ''}

<section><div class="sec-head"><h2>Товар из Китая: приехало и остаток к октябрю</h2><p class="note">Временный источник по договорённости с финдиректором: листы «Что уже едет» и «Товарообеспечение» таблицы-канона. Постоянный источник появится из Битрикс24 или 1С. Склад фулфилмента в Москве в TrueStats не заведён.</p></div>
<div class="panel"><div class="tbl"><table><thead><tr><th>SKU</th><th class="n">Себест., ₽</th><th class="n">Приехало, шт</th><th class="n">Остаток к октябрю, шт</th></tr></thead><tbody>
{''.join(f'<tr><td>{esc(r["sku"])}</td><td class="n">{esc(r["cost"])}</td><td class="n">{N(r["arrived"])}</td><td class="n">{N(r["oct"]) if r["oct"] is not None else "—"}</td></tr>' for r in TRANSIT["rows"]) or '<tr><td colspan="4">Лист «Что уже едет» не прочитан</td></tr>'}
</tbody></table></div>{('<div class="callout info"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.6"/><path d="M12 8v5M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div>Будущие поставки по листу «Сентябрь»: ' + '; '.join(f"{esc(r[0])[:40]} — {N(r[2])} шт, {esc(r[3])}" for r in TRANSIT["future"][:6]) + '.</div></div>') if TRANSIT["future"] else ''}</div></section>

<section><div class="sec-head"><h2>Откуда что берём</h2></div><div class="srcs">
<div class="src-card"><div class="name">TrueStats API <span class="tag auto">подключён</span></div><div class="what">Пять кабинетов компании: реализация, продажи, себестоимость, комиссия, логистика, хранение, реклама, налог, прибыль, остатки. Скрипт пишет в листы <code>TS_…</code> таблицы-канона дважды в день.</div><div class="next">Себестоимость в карточках TrueStats сверена финдиректором 14.09. Следующий шаг: 1–2 расчётных показателя из продаж по выбору финдиректора.</div></div>
<div class="src-card"><div class="name"><a href="{SHEET_URL}" target="_blank">Таблица-канон «План продаж и заказов»</a> <span class="tag auto">подключена</span></div><div class="what">План по SKU, розница, товар в пути из Китая, комментарии команды. Листы <code>TS_…</code> заполняются автоматически, остальные ведёт команда.</div><div class="next">План месяца = лист «Сентябрь план», подтверждено финдиректором 14.09. Следующий шаг: разделить план на Ozon и WB.</div></div>
<div class="src-card"><div class="name">Розница и сети <span class="tag miss">факта нет</span></div><div class="what">План розницы {M(PLAN['retail'])} млн в месяц есть, факта отгрузок в сети нет ни в TrueStats, ни в таблице.</div><div class="next">Источник факта розницы — Алексей Сухов или 1С. Обсудить на созвоне.</div></div>
<div class="src-card"><div class="name">1С Комплексная автоматизация <span class="tag auto">подключена, только чтение</span></div><div class="what">Стандартный OData, GET-запросы дважды в день: справочники, остатки склада ФФ Москва. В базу ничего не пишется.</div><div class="next">Следующий этап: факт ДДС по статьям (регистры денежных средств), затем заказы поставщикам как «товар в пути».</div></div></div></section>

<section><div class="sec-head"><h2>Как считаем</h2></div><div class="panel"><div class="defs">
<p><b>Выручка</b> = <code>realisation</code> TrueStats, реализация до СПП, сумма по кабинетам.</p><p><b>Маржинальность</b> = <code>profit / realisation</code>. Прибыль TrueStats: после комиссии, логистики, хранения, рекламы, налога и себестоимости.</p>
<p><b>Рентабельность себестоимости</b> = <code>profit / costOfSales</code>. Себестоимость в карточках TrueStats сверена финдиректором 14.09; себестоимость проданного может отличаться от текущей, пока продаются остатки, заведённые до изменения цены закупки.</p><p><b>ДРР</b> = <code>advertisingExpenseSum / realisation</code>, все типы кампаний.</p>
<p><b>Оплата на р/с по факту</b> = <code>totalPaid</code> TrueStats: сумма, которую площадки перечисляют на расчётный счёт за продажи периода. Равна прибыль + себестоимость + налог; формула «прибыль + себестоимость» даёт ту же величину без налога, потому что налог площадка не удерживает. Считается по отчётам реализации, поэтому за текущий месяц сумма растёт по мере закрытия недель.</p>
<p><b>Семейства</b>: по префиксу артикула, наборы сведены к базовому SKU.</p><p><b>Статусы</b>: красное — убыток или меньше 60% плана, жёлтое — ниже цели, зелёное — в цели.</p></div></div></section>

<footer><span>Фин.пульс компании · собран {dt.datetime.now():%d.%m.%Y %H:%M} · TrueStats API + <a href="{SHEET_URL}" target="_blank">таблица-канон</a></span><span>Владислав Арсентьев, AI-интегратор</span></footer>
</div>
"""
open(os.path.join(HERE, "finpulse.html"), "w").write(page)
print("built finpulse.html", len(page), "bytes; plan ok:", PLAN["ok"], PLAN.get("err", ""))
print(f"MTD rev {T_mtd['rev']:.0f} paid {T_mtd['paid']:.0f} margin {T_mtd['margin']:.1f} roc {T_mtd['roc']:.1f} drr {T_mtd['drr']:.1f}; prev {prev_m} rev {T_prev['rev']:.0f} margin {T_prev['margin']:.1f}")
