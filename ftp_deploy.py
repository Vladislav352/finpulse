#!/usr/bin/env python3
"""Зеркалирование локального каталога на хостинг по FTPS (шифрованный FTP).
Обходной транспорт вместо rsync-по-SSH: хостинг банит SSH (порт 22) с выходных IP VPN,
а FTP (порт 21) работает. Публикует готовые файлы сайта, командной строки сервера не требует.

Использование:
    python3 ftp_deploy.py <local_dir> <remote_dir> [--delete] [--exclude NAME ...]

Реквизиты — из env-файла (FTP_ENV или ~/.config/finpulse/ftp.env), формат KEY=VALUE:
    FTP_HOST=ftp.example.com
    FTP_USER=user
    FTP_PASS=...            # пароль кладёт владелец, в код/лог не попадает
    FTP_TLS=1               # 1 = FTPS (по умолчанию), 0 = обычный FTP

remote_dir — относительно домашнего каталога FTP (напр. finpulse/public_html).
--delete — удалять на сервере то, чего нет локально (кроме --exclude); имена из --exclude
никогда не заливаются и не удаляются (напр. .htpasswd, cgi-bin, finpulse).
Код возврата: 0 — успех, ненулевой — ошибка (для честного rc в вызывающих скриптах).
"""
import os, sys, ftplib, ssl

class ReuseFTP_TLS(ftplib.FTP_TLS):
    """FTPS с переиспользованием TLS-сессии на канале данных.
    хостинг (vsftpd) требует require_ssl_reuse — иначе '522 SSL connection failed: session reuse required'.
    """
    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host,
                session=getattr(self.sock, "session", None))
        return conn, size

def load_env():
    path = os.environ.get("FTP_ENV") or os.path.expanduser("~/.config/finpulse/ftp.env")
    cfg = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); cfg[k.strip()] = v.strip()
    for k in ("FTP_HOST", "FTP_USER", "FTP_PASS"):
        if not cfg.get(k):
            sys.stderr.write(f"ftp_deploy: нет {k} в {path}\n"); sys.exit(2)
    cfg.setdefault("FTP_TLS", "1")
    return cfg

def connect(cfg):
    if cfg["FTP_TLS"] not in ("0", "false", "no"):
        ftp = ReuseFTP_TLS(context=ssl.create_default_context())
        # хостинг отдаёт валидный сертификат; если понадобится — можно ослабить проверку через контекст.
        ftp.connect(cfg["FTP_HOST"], 21, timeout=30)
        ftp.login(cfg["FTP_USER"], cfg["FTP_PASS"])
        ftp.prot_p()  # шифровать канал данных
    else:
        ftp = ftplib.FTP()
        ftp.connect(cfg["FTP_HOST"], 21, timeout=30)
        ftp.login(cfg["FTP_USER"], cfg["FTP_PASS"])
    ftp.set_pasv(True)
    return ftp

def ensure_cwd(ftp, remote_dir):
    ftp.cwd("/")
    for part in [p for p in remote_dir.strip("/").split("/") if p]:
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part); ftp.cwd(part)

def remote_entries(ftp):
    """{имя: is_dir} в текущем удалённом каталоге, через MLSD с фолбэком на NLST."""
    items = {}
    try:
        for name, facts in ftp.mlsd():
            if name in (".", ".."): continue
            items[name] = (facts.get("type") == "dir")
        return items
    except Exception:
        for name in ftp.nlst():
            base = name.rsplit("/", 1)[-1]
            if base in (".", ".."): continue
            is_dir = True
            try:
                cur = ftp.pwd(); ftp.cwd(base); ftp.cwd(cur)
            except ftplib.error_perm:
                is_dir = False
            items[base] = is_dir
        return items

def rmtree(ftp, name):
    try:
        cur = ftp.pwd(); ftp.cwd(name)
    except ftplib.error_perm:
        ftp.delete(name); return
    for child, is_dir in list(remote_entries(ftp).items()):
        rmtree(ftp, child) if is_dir else ftp.delete(child)
    ftp.cwd(cur); ftp.rmd(name)

def mirror(ftp, local_dir, excludes, delete):
    local_dir = os.path.abspath(local_dir)
    local_files = set(); local_dirs = set()
    for e in os.listdir(local_dir):
        if e in excludes: continue
        (local_dirs if os.path.isdir(os.path.join(local_dir, e)) else local_files).add(e)

    if delete:
        for name, is_dir in remote_entries(ftp).items():
            if name in excludes: continue
            if is_dir and name not in local_dirs:
                rmtree(ftp, name)
            elif not is_dir and name not in local_files:
                ftp.delete(name)

    for f in sorted(local_files):
        with open(os.path.join(local_dir, f), "rb") as fh:
            ftp.storbinary(f"STOR {f}", fh)

    for d in sorted(local_dirs):
        cur = ftp.pwd()
        try:
            ftp.cwd(d)
        except ftplib.error_perm:
            ftp.mkd(d); ftp.cwd(d)
        mirror(ftp, os.path.join(local_dir, d), excludes, delete)
        ftp.cwd(cur)

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.stderr.write(__doc__); sys.exit(2)
    local_dir, remote_dir = args[0], args[1]
    delete = "--delete" in args
    excludes = set()
    if "--exclude" in args:
        i = args.index("--exclude") + 1
        while i < len(args) and not args[i].startswith("--"):
            excludes.add(args[i].rstrip("/")); i += 1
    if not os.path.isdir(local_dir):
        sys.stderr.write(f"ftp_deploy: нет локального каталога {local_dir}\n"); sys.exit(2)
    cfg = load_env()
    try:
        ftp = connect(cfg)
    except Exception as e:
        sys.stderr.write(f"ftp_deploy: подключение не удалось: {type(e).__name__}: {e}\n"); sys.exit(1)
    try:
        ensure_cwd(ftp, remote_dir)
        mirror(ftp, local_dir, excludes, delete)
        sys.stderr.write(f"ftp_deploy: ok → {cfg['FTP_HOST']}:/{remote_dir.strip('/')}\n")
    except Exception as e:
        sys.stderr.write(f"ftp_deploy: ошибка публикации: {type(e).__name__}: {e}\n"); sys.exit(1)
    finally:
        try: ftp.quit()
        except Exception:
            try: ftp.close()
            except Exception: pass

if __name__ == "__main__":
    main()
