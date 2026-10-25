"""Runtime provenance for PHP2Shell; EOL status is independent of these two fixes."""
from __future__ import annotations

import json
import re
import subprocess
import sys

PATCHED = {"8.2": (8, 2, 35), "8.3": (8, 3, 36), "8.4": (8, 4, 27), "8.5": (8, 5, 12)}
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
POLICY = "trixie-2026-10-24"
OLD_POLICY = "php2shell-2026-10-21"
SURY_VERSION = "1:7.4.33-31+0~20261021.121+debian13~1.gbpe9c428"
CVES = ("CVE-2026-109618", "CVE-2026-109619")
# These commands only read package metadata and checksums, even on a live container.
PROBE = r'''
$os = @parse_ini_file('/etc/os-release') ?: [];
$r = ['php_full_version'=>PHP_VERSION,'php_series'=>PHP_MAJOR_VERSION.'.'.PHP_MINOR_VERSION,
      'os_id'=>$os['ID']??'unknown','os_version'=>$os['VERSION_ID']??'unknown',
      'runtime_provider'=>'official','runtime_package_version'=>'','binary_integrity'=>false];
if (PHP_MAJOR_VERSION===7 && PHP_MINOR_VERSION===4) {
    $r['runtime_provider']='sury';
    $out=[]; $rc=1;
    exec('dpkg-query -W -f=\'${source:Package}\t${Package}\t${Version}\n\' \'php7.4-*\' 2>/dev/null',$out,$rc);
    $versions=[]; $packages=[];
    foreach($out as $line) {
        $p=explode("\t",$line);
        if(count($p)===3 && $p[0]==='php7.4') {$versions[$p[1]]=$p[2]; $packages[]=$p[1];}
    }
    $r['core_packages']=$versions;
    $r['runtime_package_version']=$versions['php7.4-cli']??'';
    // Debian slim deliberately excludes man/doc files. Verify executable PHP
    // and every core extension against the package's installed checksum manifest.
    $valid=true; $verified=[];
    foreach($packages as $package) {
        $files=glob('/var/lib/dpkg/info/'.$package.'.md5sums');
        if(!$files) $files=glob('/var/lib/dpkg/info/'.$package.':*.md5sums');
        if(count($files)!==1) {$valid=false; continue;}
        foreach(file($files[0],FILE_IGNORE_NEW_LINES|FILE_SKIP_EMPTY_LINES) as $line) {
            $parts=preg_split('/\s+/',trim($line),2);
            if(count($parts)!==2) {$valid=false; continue;}
            $path='/'.$parts[1];
            if($path==='/usr/bin/php7.4' || $path==='/usr/sbin/php-fpm7.4' ||
               preg_match('~^/usr/lib/php/[0-9]+/[^/]+\.so$~',$path)) {
                $valid=$valid && is_file($path) && hash_equals($parts[0],md5_file($path));
                $verified[]=$path;
            }
        }
    }
    $r['binary_integrity']=$valid && in_array('/usr/bin/php7.4',$verified,true) &&
        in_array('/usr/sbin/php-fpm7.4',$verified,true) && realpath(PHP_BINARY)==='/usr/bin/php7.4';
}
echo json_encode($r,JSON_THROW_ON_ERROR);
'''


def status(version: str | None) -> dict:
    if not version:
        return {"status":"unverified", "php_full_version":"unknown", "minimum_fixed":"unknown"}
    match = VERSION_RE.fullmatch(version.strip())
    if not match:
        return {"status":"unverified", "php_full_version":version, "minimum_fixed":"unknown"}
    parsed = tuple(map(int, match.groups()))
    minimum = PATCHED.get(f"{parsed[0]}.{parsed[1]}")
    if minimum is None:
        return {"status":"vulnerable", "php_full_version":version, "minimum_fixed":"vendor backport verification required"}
    return {"status":"patched" if parsed>=minimum else "vulnerable",
            "php_full_version":version, "minimum_fixed":".".join(map(str,minimum))}


def assess(probe: dict, policy: str, series: str) -> dict:
    report = {**probe, **status(probe.get("php_full_version"))}
    legacy = probe.get("php_series", series) == "7.4"
    report["legacy"] = legacy
    if legacy:
        packages = probe.get("core_packages", {})
        valid = (policy == POLICY and series == "7.4" and probe.get("os_id") == "debian"
                 and probe.get("os_version") == "13" and probe.get("binary_integrity") is True
                 and {"php7.4-cli", "php7.4-fpm", "php7.4-common"}.issubset(packages)
                 and all(v == SURY_VERSION for v in packages.values())
                 and re.match(r"^7\.4\.33(?:$|-)", probe.get("php_full_version", "")))
        report["status"] = "patched" if valid else "unverified"
        report["minimum_fixed"] = SURY_VERSION
    elif policy not in {POLICY, OLD_POLICY} or series != probe.get("php_series"):
        report["status"] = "unverified"
    report["cve_fixes"] = {cve: report["status"] for cve in CVES}
    return report


def image_report(reference: str, *, container: str = "") -> dict:
    identity = ""
    try:
        # Container inspection identifies the running image, never a moving tag.
        if container:
            identity = subprocess.check_output(["docker","inspect","-f","{{.Image}}",container],text=True).strip()
            reference = identity
        inspected = subprocess.run(["docker","image","inspect",reference],capture_output=True,text=True,timeout=15)
        if inspected.returncode:
            return {"image_id":"", **status(None)}
        info = json.loads(inspected.stdout)[0]
        identity = info["Id"]
        labels = info.get("Config",{}).get("Labels") or {}
        if container:
            command = ["docker","exec",container,"php","-d","disable_functions=","-r",PROBE]
        else:
            command = ["docker","run","--rm","--network","none","--read-only","--memory","128m",
                       "--cpus","0.5","--pids-limit","16","--user","65534:65534","--cap-drop","ALL",
                       "--security-opt","no-new-privileges","--entrypoint","php",identity,
                       "-d","disable_functions=","-r",PROBE]
        result = subprocess.run(command,capture_output=True,text=True,timeout=30)
        if result.returncode:
            return {"image_id":identity, **status(None)}
        report = assess(json.loads(result.stdout), labels.get("org.spawnwp.php-security-policy",""),
                        labels.get("org.spawnwp.php-series",""))
        return {"image_id":identity, **report}
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return {"image_id":identity, **status(None)}


def accepts_image(reference: str, acknowledged: bool, image_id: str = "") -> bool:
    report = image_report(reference)
    # Patched EOL runtimes still require explicit, image-specific LAB acceptance.
    if report["status"] == "patched" and not report.get("legacy"):
        return True
    return bool(acknowledged and image_id and image_id == report.get("image_id"))


if __name__ == "__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--check-version":
        result=status(sys.argv[2])
        print(f"{result['status']}: PHP {result['php_full_version']} (minimum fixed: {result['minimum_fixed']})")
        raise SystemExit(0 if result["status"]=="patched" else 2)
    print("Usage: php_security.py --check-version <full-php-version>",file=sys.stderr)
    raise SystemExit(2)
