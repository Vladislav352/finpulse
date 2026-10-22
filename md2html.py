#!/usr/bin/env python3
"""md → простой html (заголовки, таблицы, цитаты, списки) для публикации отчётов."""
import sys,re,html
src,dst=sys.argv[1:3]; md=open(src).read().splitlines(); out=[]; i=0
def inline(t):
    t=html.escape(t); t=re.sub(r"\*\*(.+?)\*\*",r"<b>\1</b>",t); t=re.sub(r"`(.+?)`",r"<code>\1</code>",t); return t
while i<len(md):
    l=md[i]
    if l.startswith("|"):
        rows=[]
        while i<len(md) and md[i].startswith("|"): rows.append([c.strip() for c in md[i].strip("|").split("|")]); i+=1
        rows=[r for r in rows if not all(re.fullmatch(r"-+",c) for c in r)]
        out.append("<table><tr>"+"".join(f"<th>{inline(c)}</th>" for c in rows[0])+"</tr>"+"".join("<tr>"+"".join(f"<td>{inline(c)}</td>" for c in r)+"</tr>" for r in rows[1:])+"</table>"); continue
    if l.startswith("# "): out.append(f"<h1>{inline(l[2:])}</h1>")
    elif l.startswith("## "): out.append(f"<h2>{inline(l[3:])}</h2>")
    elif l.startswith("> "): out.append(f"<blockquote>{inline(l[2:])}</blockquote>")
    elif re.match(r"^\d+\. ",l): out.append(f"<p class=li>{inline(l)}</p>")
    elif l.startswith("- "): out.append(f"<p class=li>• {inline(l[2:])}</p>")
    elif l.strip(): out.append(f"<p>{inline(l)}</p>")
    i+=1
css="body{font-family:-apple-system,'Segoe UI',Arial,sans-serif;max-width:900px;margin:32px auto;padding:0 24px;color:#151b17;line-height:1.5;font-size:14px;background:#f5f6f2}h1{font-size:24px;margin:0 0 8px}h2{font-size:17px;margin:26px 0 8px;color:#1f5a3a;border-bottom:1px solid #d9ddd3;padding-bottom:4px}table{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0;background:#fff}th{background:#eef1ea;text-align:left;padding:6px 8px;border:1px solid #d9ddd3}td{padding:6px 8px;border:1px solid #d9ddd3;vertical-align:top}blockquote{margin:4px 0 4px 16px;color:#4f5a53;border-left:3px solid #1f7a48;padding-left:10px}code{background:#eef1ea;padding:1px 5px;border-radius:4px;font-size:12px}.li{margin:3px 0}nav{font-size:13px;margin-bottom:16px}nav a{color:#1f5a3a}"
title=next((l[2:] for l in md if l.startswith("# ")),"Отчёт")
open(dst,"w").write(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{css}</style><nav><a href='index.html'>← Фин.пульс</a> · <a href='otzyvy.pdf'>PDF</a></nav>"+"\n".join(out))
