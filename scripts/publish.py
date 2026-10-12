#!/usr/bin/env python3
"""Publish Apparition releases on apparition.illixion.com.

The site lists releases in Website/release.json; the installer and the app read it. A
release's files can live anywhere (GitHub releases today, any host later): what makes them
trustworthy is Website/releases/<version>/SHA256SUMS, signed with a pinned release key
(scripts/bless.sh). Clients check that signature, then each file's hash against it, and
ignore which URL served it. So download URLs can be added or swapped at any time without
signing again, while a file's contents can't change without a new signature.

  scripts/publish.py add <version> <dir> --url-base <url> [--url-base <url>...]
          [--build N] [--notes TEXT] [--signer X | --key-file F] [--gpg-agent] [--dry-run]
      Hash every file in <dir>, sign the manifest, and list the release (as latest when it is
      the newest). Each file's URL is <url-base>/<name>, for each --url-base in order.
      File names say what they are, and must carry the version:
        Apparition-<version>-<visionOS|iOS|tvOS>.ipa
        Apparition-Installer-<version>-<platform>.<zip|tar.gz>   (macos-universal,
                                                                  windows-x86_64, linux-x86_64)
      Upload the files to those URLs before or after; `verify --download` checks them.
  scripts/publish.py urls <version> <file name> <url>... [--append]
      Replace (or add to) a file's download URLs. No signing needed.
  scripts/publish.py verify [<version>] [--download]
      Check every listed release (or one) the way clients do; --download also fetches every
      URL and checks what it serves.
  scripts/publish.py source
      Regenerate the AltStore sources (Website/source-<platform>.json, one per app platform,
      since a source has no per-platform downloads) from release.json.
"""

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://apparition.illixion.com"
WEBSITE = ROOT / "Website"
INDEX = WEBSITE / "release.json"
SIGNERS = ROOT / "release-signers"
APP_SIGNERS = ROOT.parent / "Apparition" / "release-signers"
NAMESPACE = "apparition-release"
IDENTITY = "releases@apparition.illixion.com"
BUNDLE_ID = "com.illixion.apparition"
# The app's platforms, as in its file name -> as in release.json and source-<platform>.json.
APP_PLATFORMS = {"visionOS": "visionos", "iOS": "ios", "tvOS": "tvos"}
PLATFORMS = {"macos-universal", "windows-x86_64", "windows-arm64", "linux-x86_64", "linux-arm64"}


def fail(message):
    sys.exit(f"error: {message}")


def version_key(version):
    return tuple(int(p) if p.isdigit() else 0 for p in version.split("-")[0].split("."))


def classify(name, version):
    """(kind, platform) for a release file name, or None when it doesn't fit the scheme."""
    v = re.escape(version)
    m = re.fullmatch(rf"Apparition-{v}-([A-Za-z]+)\.ipa", name)
    if m and m.group(1) in APP_PLATFORMS:
        return "app", APP_PLATFORMS[m.group(1)]
    m = re.fullmatch(rf"Apparition-Installer-{v}-([a-z0-9_-]+)\.(zip|tar\.gz)", name)
    if m and m.group(1) in PLATFORMS:
        return "installer", m.group(1)
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_index():
    if INDEX.exists():
        return json.loads(INDEX.read_text())
    return {"format": "apparition-releases", "version": 1, "latest": None, "releases": []}


def save_index(index):
    index["releases"].sort(key=lambda r: version_key(r["version"]), reverse=True)
    INDEX.write_text(json.dumps(index, indent=2) + "\n")


def check_signers():
    if APP_SIGNERS.exists() and APP_SIGNERS.read_bytes() != SIGNERS.read_bytes():
        fail(f"{SIGNERS} differs from {APP_SIGNERS}; the app and the site must pin the same keys")


def ipa_info(path):
    """CFBundleIdentifier, CFBundleShortVersionString, CFBundleVersion, MinimumOSVersion."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
        if len(names) != 1:
            fail(f"{path.name}: expected one Payload/*.app/Info.plist")
        info = plistlib.loads(z.read(names[0]))
    return info


def verify_signature(sums, sig):
    result = subprocess.run(
        ["ssh-keygen", "-Y", "verify", "-f", str(SIGNERS), "-I", IDENTITY, "-n", NAMESPACE, "-s", str(sig)],
        stdin=open(sums, "rb"), capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"{sums.relative_to(ROOT)}: signature doesn't verify: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def parse_sums(path):
    sums = {}
    for line in path.read_text().splitlines():
        if line.strip():
            digest, name = line.split(None, 1)
            sums[name.lstrip("*")] = digest.lower()
    return sums


def cmd_add(args):
    check_signers()
    version = args.version
    if not re.fullmatch(r"\d+(\.\d+){1,3}", version):
        fail(f"version {version} isn't dotted numbers (1.2 or 1.2.3)")
    folder = Path(args.dir)
    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    if not files:
        fail(f"nothing in {folder}")
    assets = []
    apps = []
    for path in files:
        kind = classify(path.name, version)
        if not kind:
            fail(f"{path.name} isn't named like an Apparition {version} release file (see --help)")
        assets.append({
            "name": path.name,
            "kind": kind[0],
            "platform": kind[1],
            "size": path.stat().st_size,
            "sha256": sha256(path),
            "urls": [f"{base.rstrip('/')}/{path.name}" for base in args.url_base],
        })
        if kind[0] == "app":
            apps.append((kind[1], path))
    if not apps:
        fail(f"no Apparition-{version}-<platform>.ipa in {folder}")
    infos = {}
    for platform, app in apps:
        info = infos[platform] = ipa_info(app)
        if info.get("CFBundleIdentifier") != BUNDLE_ID:
            fail(f"{app.name} is {info.get('CFBundleIdentifier')}, not {BUNDLE_ID}")
        if info.get("CFBundleShortVersionString") != version:
            fail(f"{app.name} says it is version {info.get('CFBundleShortVersionString')}, not {version}")
    builds = {int(i.get("CFBundleVersion", "0") or 0) for i in infos.values()}
    if len(builds) > 1:
        fail(f"the IPAs disagree on the build number: {sorted(builds)}")

    index = load_index()
    newer = index["latest"] is None or version_key(version) > version_key(index["latest"])
    existing = next((r for r in index["releases"] if r["version"] == version), None)
    if not newer and not existing and not args.allow_older:
        fail(f"{version} is older than the latest, {index['latest']} (--allow-older to list it anyway)")

    stage = Path(tempfile.mkdtemp(prefix="apparition-publish-"))
    try:
        sums = stage / "SHA256SUMS"
        sums.write_text("".join(f"{a['sha256']}  {a['name']}\n" for a in assets))
        print("==> manifest")
        print("".join(f"    {line}\n" for line in sums.read_text().splitlines()), end="")
        bless = [str(ROOT / "scripts" / "bless.sh"), str(sums)]
        if args.signer:
            bless += ["--signer", args.signer]
        if args.key_file:
            bless += ["--key-file", args.key_file]
        if args.gpg_agent:
            bless.append("--gpg-agent")
        subprocess.run(bless, check=True)
        if args.dry_run:
            print(f"==> --dry-run: signed in {stage}, nothing listed")
            stage = None
            return
        out = WEBSITE / "releases" / version
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy(sums, out / "SHA256SUMS")
        shutil.copy(stage / "SHA256SUMS.sig", out / "SHA256SUMS.sig")
    finally:
        if stage:
            shutil.rmtree(stage, ignore_errors=True)

    release = {
        "version": version,
        "build": args.build or builds.pop() or None,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": args.notes or (existing or {}).get("notes"),
        "minOS": {platform: info.get("MinimumOSVersion") for platform, info in infos.items()},
        "sums": f"releases/{version}/SHA256SUMS",
        "signature": f"releases/{version}/SHA256SUMS.sig",
        "assets": assets,
    }
    index["releases"] = [r for r in index["releases"] if r["version"] != version] + [release]
    if newer:
        index["latest"] = version
    save_index(index)
    write_source(index)
    verify(index, [version], download=False)
    print(f"==> listed {version}{' as latest' if newer else ''}. Commit Website/ and push to deploy.")


def cmd_urls(args):
    index = load_index()
    release = next((r for r in index["releases"] if r["version"] == args.version), None) or fail(f"no release {args.version}")
    asset = next((a for a in release["assets"] if a["name"] == args.name), None) or fail(f"{args.version} has no {args.name}")
    asset["urls"] = (asset["urls"] if args.append else []) + args.url
    save_index(index)
    write_source(index)
    print(f"==> {args.name}: " + ", ".join(asset["urls"]))


def verify(index, versions, download):
    check_signers()
    ok = True
    for release in index["releases"]:
        if versions and release["version"] not in versions:
            continue
        version = release["version"]
        sums_path = WEBSITE / release["sums"]
        signer = verify_signature(sums_path, WEBSITE / release["signature"])
        sums = parse_sums(sums_path)
        print(f"==> {version}: {signer}")
        for name in sums:
            if not classify(name, version):
                fail(f"{version}: the signed manifest lists {name}, which doesn't carry the version")
        for asset in release["assets"]:
            if sums.get(asset["name"]) != asset["sha256"].lower():
                fail(f"{version}: {asset['name']} isn't in the signed manifest with this hash")
            if not asset["urls"]:
                print(f"    {asset['name']}: no URL")
                ok = False
            if download:
                for url in asset["urls"]:
                    h = hashlib.sha256()
                    try:
                        with urllib.request.urlopen(url) as response:
                            for chunk in iter(lambda: response.read(1 << 20), b""):
                                h.update(chunk)
                        good = h.hexdigest() == asset["sha256"].lower()
                        ok &= good
                        print(f"    {'ok  ' if good else 'BAD '} {url}")
                    except Exception as error:  # noqa: BLE001 - report and carry on
                        ok = False
                        print(f"    FAIL {url}: {error}")
    if not ok:
        sys.exit(1)


def cmd_verify(args):
    index = load_index()
    if not index["releases"]:
        fail("no releases listed yet")
    verify(index, [args.version] if args.version else [], args.download)


def write_source(index):
    """The AltStore/SideStore sources: Apparition itself, one file per app platform."""
    names = {v: k for k, v in APP_PLATFORMS.items()}
    platforms = sorted({a["platform"] for r in index["releases"] for a in r["assets"] if a["kind"] == "app"})
    for platform in platforms:
        versions = []
        for release in index["releases"]:
            app = next((a for a in release["assets"] if a["kind"] == "app" and a["platform"] == platform), None)
            if not app or not app["urls"]:
                continue
            versions.append({
                "version": release["version"],
                "buildVersion": str(release.get("build") or ""),
                "date": release["date"],
                "localizedDescription": release.get("notes") or f"Apparition {release['version']}.",
                "downloadURL": app["urls"][0],
                "size": app["size"],
                "sha256": app["sha256"],
                "minOSVersion": (release.get("minOS") or {}).get(platform) or "26.0",
            })
        source = {
            "name": f"Apparition for {names[platform]}",
            "identifier": f"com.illixion.apparition.source.{platform}",
            "subtitle": "The on-device sideloader",
            "description": "Official releases of Apparition. Every release is signed; see apparition.illixion.com.",
            "iconURL": f"{SITE}/icon.png",
            "website": SITE,
            "tintColor": "#8B7CF6",
            "apps": [{
                "name": "Apparition",
                "bundleIdentifier": BUNDLE_ID,
                "developerName": "Illixion",
                "subtitle": "Sideload, sign and refresh apps on the device itself",
                "localizedDescription": (
                    "Install apps from AltStore-format sources, sign them on the device with a free "
                    "Apple ID or your own certificate, keep them refreshed, and launch them with JIT."),
                "iconURL": f"{SITE}/icon.png",
                "tintColor": "#8B7CF6",
                "versions": versions,
                "appPermissions": {"entitlements": [], "privacy": {}},
            }],
            "news": [],
        }
        (WEBSITE / f"source-{platform}.json").write_text(json.dumps(source, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("version")
    add.add_argument("dir")
    add.add_argument("--url-base", action="append", required=True)
    add.add_argument("--build", type=int)
    add.add_argument("--notes")
    add.add_argument("--signer")
    add.add_argument("--key-file")
    add.add_argument("--gpg-agent", action="store_true")
    add.add_argument("--allow-older", action="store_true")
    add.add_argument("--dry-run", action="store_true")
    urls = sub.add_parser("urls")
    urls.add_argument("version")
    urls.add_argument("name")
    urls.add_argument("url", nargs="+")
    urls.add_argument("--append", action="store_true")
    ver = sub.add_parser("verify")
    ver.add_argument("version", nargs="?")
    ver.add_argument("--download", action="store_true")
    sub.add_parser("source")
    args = parser.parse_args()
    if args.command == "add":
        cmd_add(args)
    elif args.command == "urls":
        cmd_urls(args)
    elif args.command == "verify":
        cmd_verify(args)
    else:
        write_source(load_index())


if __name__ == "__main__":
    main()
