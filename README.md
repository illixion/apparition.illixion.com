# apparition.illixion.com

The website for [Apparition](https://github.com/illixion/Apparition), and where its releases are
listed. Cloudflare serves `Website/` as static assets (`wrangler.jsonc`: no Worker, no build step),
so a push to `main` deploys.

`Website/release.json` lists every release: version, build, date, notes and, for each file, its
kind (`app` or `installer`), platform, size, SHA-256 and any number of download URLs. Apparition
Installer and the app read it to find the latest release. `Website/source-<platform>.json` are
AltStore/SideStore sources generated from it, one per app platform.

## Why the files can be anywhere

Nothing trusts a URL. Each release has `Website/releases/<version>/SHA256SUMS`, signed with
`ssh-keygen -Y sign -n apparition-release` by a key in `release-signers` (the YubiKey, or the
offline backup). Clients verify that signature against the same keys compiled into them, check
that `release.json` agrees with the signed hashes, and check every download's hash before using
it. So:

- files can move from GitHub to any other host, or have mirrors, by editing URLs only
  (`publish.py urls`), with no new signature;
- a file can't change without a new signature, and a file the manifest doesn't list is refused;
- the version is covered too, because every signed file name carries it, so an old release
  can't be relabelled as new.

`release-signers` must match the copy in the Apparition repo; `publish.py` refuses to run when
they differ. The keys are the ones that sign Longwave releases; the namespace keeps either
project's signatures from passing as the other's.

## Publishing a release

In the Apparition repo, after committing the version bump (project.yml, installer/Cargo.toml and
desktop/Cargo.toml), build on GitHub Actions, then check and release what it built:

```sh
gh workflow run release.yml -R illixion/Apparition -f version=0.2.0   # or push a v0.2.0 tag
gh run download <run id> -R illixion/Apparition -n release-0.2.0 -D ../Apparition/build/release/0.2.0
for f in ../Apparition/build/release/0.2.0/Apparition-*; do gh attestation verify "$f" -R illixion/Apparition; done
gh release create v0.2.0 ../Apparition/build/release/0.2.0/Apparition-* -R illixion/Apparition
```

(`scripts/package-release.sh 0.2.0 --linux` in the Apparition repo builds the same files locally.)

Then here:

```sh
scripts/publish.py add 0.2.0 ../Apparition/build/release/0.2.0 \
  --url-base https://github.com/illixion/Apparition/releases/download/v0.2.0 --notes "What changed"
scripts/publish.py verify 0.2.0 --download     # every URL serves the signed file
git add -A && git commit -m "Release 0.2.0" && git push
```

`add` hashes the files, signs the manifest (`scripts/bless.sh`, which asks the YubiKey for a PIN
and touch, so run it in the foreground), checks the signature, and lists the release as latest.
`--key-file <backup>` signs with the offline key instead.

A fix that changes only the Apple apps can be an app-only release: run the workflow with
`-f parts=app` (only `project.yml`'s version needs bumping then), and `add` takes the folder of
IPAs alone. Apparition updates itself from it, since it needs only its own platform's app. The
desktop app's update check ignores a release without an installer for its platform, and the site
keeps offering the installers of the newest release that has them, with a note.

Add a mirror later with `scripts/publish.py urls 0.2.0 <file> <url> --append`.
