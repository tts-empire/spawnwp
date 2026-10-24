#!/usr/bin/env python3
"""Consistent, private migration backup; never sends site data off the host."""
from __future__ import annotations
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
import tarfile
from pathlib import Path


def backup(project: Path) -> Path:
    os.umask(0o077)
    destination = project / "backups" / "runtime" / (time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=False)
    php = subprocess.check_output(["docker","compose","ps","-q","php"],cwd=project,text=True).strip()
    paused = False
    try:
        for relative in (".env","compose.yaml"):
            shutil.copy2(project / relative, destination / relative)
        shutil.copytree(project / "docker", destination / "docker")
        if (project / ".spawnwp").exists():
            shutil.copytree(project / ".spawnwp", destination / "state")
        config = json.loads(subprocess.check_output(["docker","compose","config","--format","json"],cwd=project,text=True))
        def archive_volume(service_name, filename):
            volume_name = config["volumes"][service_name]["name"]
            info = json.loads(subprocess.check_output(["docker","volume","inspect",volume_name],text=True))[0]
            with tarfile.open(destination / filename,"w:gz") as archive:
                archive.add(info["Mountpoint"],arcname=".")
        if php:
            subprocess.run(["docker","pause",php],check=True,stdout=subprocess.DEVNULL)
            paused = True
        db = subprocess.check_output(["docker","compose","ps","-q","db"],cwd=project,text=True).strip()
        commands=[]
        if db:
            commands.append(("database.sql.gz", ["docker","compose","exec","-T","db","sh","-c",
               'exec mariadb-dump --single-transaction --skip-lock-tables -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"']))
        else:
            archive_volume("db_data","db_data.tar.gz")
        if php:
            commands.append(("wordpress.tar.gz", ["docker","cp",f"{php}:/var/www/html/.","-"]))
        else:
            archive_volume("wp_data","wordpress.tar.gz")
            shutil.copytree(project / "projects" / "primary" / "wp-content",destination / "wp-content")
        for name, command in commands:
            with gzip.open(destination / name,"wb") as output:
                process = subprocess.Popen(command,cwd=project,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                assert process.stdout is not None
                shutil.copyfileobj(process.stdout,output)
                _, error = process.communicate()
                if process.returncode:
                    raise RuntimeError(f"Backup failed for {name}: {error.decode(errors='replace')[:200]}")
        mailpit = subprocess.check_output(["docker","compose","ps","-aq","mailpit"],cwd=project,text=True).strip()
        if mailpit:
            info = json.loads(subprocess.check_output(["docker","inspect",mailpit],text=True))[0]
            mount = next((m for m in info["Mounts"] if m["Destination"]=="/data"),None)
            if mount and (Path(mount["Source"])/"mailpit.db").is_file():
                source = sqlite3.connect((Path(mount["Source"])/"mailpit.db").as_uri()+"?mode=ro",uri=True)
                target = sqlite3.connect(destination/"mailpit.db")
                try:
                    source.backup(target)
                finally:
                    target.close(); source.close()
        (destination/"complete.json").write_text(json.dumps({"created_at":time.time(),"php_container":php})+"\n")
        return destination
    finally:
        if paused:
            subprocess.run(["docker","unpause",php],check=True,stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    print("Migration backup: " + str(backup(Path.cwd())))
