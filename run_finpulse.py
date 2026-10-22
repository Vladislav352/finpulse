#!/usr/bin/env python3
"""Полный цикл Фин.пульса: выгрузка TrueStats → сборка finpulse.html → публикация на хостинг.
Запускается launchd (com.company.truestats-pull) и вручную: python3 run_finpulse.py [--no-pull] [--no-deploy]
Публикация: rsync по SSH-ключу ~/.ssh/deploy_key в example.com/public_html/<FINPULSE_DIR>/ (см. deploy.env).
"""
import os, sys, subprocess, datetime as dt, shutil

HERE = os.path.dirname(os.path.abspath(__file__)); HOME = os.path.expanduser("~")
PY = sys.executable
LOG = os.path.join(HERE, "run_finpulse.log")
def log(m):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}"; print(line, flush=True)
    open(LOG, "a").write(line + "\n")

def env():
    cfg = {"DEPLOY_MODE": "rsync", "SSH_KEY": f"{HOME}/.ssh/deploy_key", "TARGET": "user@example.com:example.com/public_html/finpulse/"}
    p = os.path.join(HERE, "deploy.env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); cfg[k.strip()] = v.strip()
    return cfg

def main():
    args = sys.argv[1:]
    if "--no-pull" not in args:
        r = subprocess.run([PY, os.path.join(HERE, "truestats_pull.py")], capture_output=True, text=True)
        log(f"pull rc={r.returncode} {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''} {r.stderr.strip()[-200:]}")
        if r.returncode != 0: log("pull failed — собираю из прошлых данных")
    if "--no-pull" not in args:
        r1 = subprocess.run([PY, os.path.join(HERE, "onec_pull.py")], capture_output=True, text=True)
        log(f"1c rc={r1.returncode} {(r1.stdout.strip().splitlines() or [''])[-1][:160]} {r1.stderr.strip()[-160:]}")
    r = subprocess.run([PY, os.path.join(HERE, "build_finpulse.py")], capture_output=True, text=True)
    log(f"build rc={r.returncode} {r.stdout.strip().splitlines()[0] if r.stdout.strip() else r.stderr.strip()[-200:]}")
    if r.returncode != 0: sys.exit(1)
    # каталог публикации
    site = os.path.join(HERE, "site"); os.makedirs(site, exist_ok=True)
    shutil.copy(os.path.join(HERE, "finpulse.html"), os.path.join(site, "index.html"))
    rep_dir = os.path.join(os.path.dirname(HERE), "Отзывы")
    reps = sorted(f for f in os.listdir(rep_dir) if f.startswith("Отчёт_отзывы_WB_") and f.endswith(".md")) if os.path.isdir(rep_dir) else []
    if reps:
        subprocess.run([PY, os.path.join(HERE, "md2html.py"), os.path.join(rep_dir, reps[-1]), os.path.join(site, "otzyvy.html")], capture_output=True)
    pkg = os.path.join(os.path.dirname(HERE), "Для_команды_2026-09-13")
    for f, dst in (("finpulse_2026-09-13.pdf", "finpulse.pdf"), ("Отзывы_WB_30дней_2026-09-13.pdf", "otzyvy.pdf")):
        if os.path.exists(os.path.join(pkg, f)): shutil.copy(os.path.join(pkg, f), os.path.join(site, dst))
    open(os.path.join(site, ".htaccess"), "w").write(
        "AuthType Basic\nAuthName \"Fin.pulse\"\nAuthUserFile /home/u/user/finpulse/public_html/.htpasswd\nRequire valid-user\n"
        "<Files \".htpasswd\">\n  Require all denied\n</Files>\nDirectoryIndex index.html\nAddDefaultCharset UTF-8\n"
        "<FilesMatch \"\\.(html|json)$\">\n  Header set Cache-Control \"no-cache\"\n</FilesMatch>\n")
    if "--no-deploy" in args: log("deploy пропущен"); return
    cfg = env()
    mode = cfg.get("DEPLOY_MODE", "rsync")
    if mode == "ftp":
        # FTPS вместо rsync-по-SSH: хостинг банит SSH с IP VPN, FTP работает. Реквизиты в ~/.config/finpulse/ftp.env.
        remote = cfg.get("FTP_REMOTE", "finpulse/public_html")
        r = subprocess.run([PY, os.path.join(HERE, "ftp_deploy.py"), site, remote,
                            "--delete", "--exclude", ".htpasswd", "cgi-bin"], capture_output=True, text=True)
        log(f"deploy(ftp) rc={r.returncode} → {remote} {(r.stderr.strip()[-200:])}")
        if r.returncode != 0: sys.exit(1)
        return
    if mode != "rsync": log("deploy выключен"); return
    r = subprocess.run(["rsync", "-az", "--delete", "--exclude=.htpasswd", "--exclude=cgi-bin/", "-e", f"ssh -i {cfg['SSH_KEY']} -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new", site + "/", cfg["TARGET"]], capture_output=True, text=True)
    log(f"deploy rc={r.returncode} → {cfg['TARGET']} {r.stderr.strip()[-200:]}")

if __name__ == "__main__": main()
