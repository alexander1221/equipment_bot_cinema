#!/usr/bin/env python3
"""Согласованная копия SQLite и конфигурации. Запускать root на целевом сервере."""
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import sqlite3
import subprocess
import tarfile


def main():
    os.umask(0o077)
    project = Path('/opt/equipment_bot_cinema')
    db = project / 'data/equipment.db'
    if not db.is_file():
        raise SystemExit('База не найдена: проверьте каталог и режим')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    base = Path('/var/backups/equipment-bot')
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = base / stamp
    dest.mkdir(mode=0o700)
    src = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
    out = sqlite3.connect(dest / 'equipment.db')
    try:
        src.backup(out)
        result = out.execute('PRAGMA integrity_check').fetchone()[0]
        if result != 'ok':
            raise SystemExit('Копия не прошла проверку; её нельзя использовать')
    finally:
        out.close()
        src.close()
    sources = [
        (project / '.env', 'app.env'),
        (project / 'docker-compose.yml', 'docker-compose.yml'),
        (project / '.dockerignore', 'dockerignore'),
        (Path('/opt/equipment_bot_cinema.web.yml'), 'web.yml'),
        (Path('/opt/equipment_bot_cinema.full.yml'), 'full.yml'),
        (Path('/etc/nginx/sites-available/equipment-bot'), 'nginx-equipment-bot.conf'),
    ]
    for source, name in sources:
        if source.is_file():
            shutil.copyfile(source, dest / name)
    tls = Path('/etc/nginx/ssl/equipment-bot')
    if tls.is_dir():
        shutil.copytree(tls, dest / 'tls')
    commit = subprocess.check_output(
        ['git', '-C', str(project), 'rev-parse', 'HEAD'], text=True).strip()
    (dest / 'commit.txt').write_text(commit + '\n')
    (dest / 'README.txt').write_text(
        'UTC snapshot. Contains secrets. Record the active mode (web/full) separately.\n')
    archive = base / (stamp + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(dest, arcname=stamp)
    print('База: integrity_check=ok')
    print('Каталог:', dest)
    print('Архив:', archive)


if __name__ == '__main__':
    main()
