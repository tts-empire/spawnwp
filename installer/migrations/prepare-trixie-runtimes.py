#!/usr/bin/env python3
"""Copy verified managed files and retain selected images; never recreate a service."""
import json
import os
import sys
from pathlib import Path

root=Path(os.environ.get('SPAWNWP_RUNTIME_ROOT','/srv/wp-dev'))
sys.path.insert(0,str(root))
from runtime_migration import stage_site, restore_primary_overrides


def main():
    projects=Path(os.environ.get('SPAWNWP_PROJECTS_ROOT','/srv'))
    restore_primary_overrides(root,Path(os.environ.get('SPAWNWP_STATE_ROOT','/var/lib/spawnwp/updater'))/'backups')
    manifest=json.loads((root/'runtime-migration.json').read_text())
    for site in sorted(projects.iterdir()):
        if not (site/'compose.yaml').is_file() or not (site/'.env').is_file():continue
        try:
            result=stage_site(site,root,manifest)
            print(site.name+': '+json.dumps(result))
        except Exception as exc:
            print(site.name+': migration_required: '+str(exc),file=sys.stderr)
    return 0

if __name__=='__main__':raise SystemExit(main())
