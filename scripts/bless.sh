#!/usr/bin/env bash
#
# Sign one release manifest (SHA256SUMS) with a pinned release key, writing SHA256SUMS.sig
# next to it, then check the result with ssh-keygen's own verifier against release-signers.
# scripts/publish.py runs this; it's separate so the signing is a few auditable lines.
#
#   scripts/bless.sh <SHA256SUMS>                          # the YubiKey (first signer), via an agent
#   scripts/bless.sh <SHA256SUMS> --signer ixion@SecureBackup
#   scripts/bless.sh <SHA256SUMS> --key-file ~/backup_ed25519   # the offline key, no agent
#   scripts/bless.sh <SHA256SUMS> --gpg-agent              # look for the key in gpg-agent
#
# SSHSIG under the namespace "apparition-release". The keys are the same two that sign
# Longwave releases (see release-signers for why both are pinned from the start); the
# namespace keeps their signatures from passing as each other's.
#
# The YubiKey asks for a PIN and a touch, so run this in the foreground: a backgrounded
# signer can't raise the prompt and fails as a timeout.
#
# Ported from Longwave's scripts/bless-release.sh (signing half).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIGNERS="$REPO_ROOT/release-signers"
NAMESPACE='apparition-release'
IDENTITY='releases@apparition.illixion.com'

MANIFEST="${1:?usage: bless.sh <SHA256SUMS> [--signer X | --key-file F] [--gpg-agent]}"
shift
SIGNER=""
KEY_FILE=""
USE_GPG_AGENT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --signer)    SIGNER="${2:?--signer needs a value}"; shift ;;
    --key-file)  KEY_FILE="${2:?--key-file needs a value}"; shift ;;
    --gpg-agent) USE_GPG_AGENT=1 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

[[ -f "$MANIFEST" ]] || { echo "error: no manifest at $MANIFEST" >&2; exit 1; }
[[ -f "$SIGNERS" ]] || { echo "error: signer list not found at $SIGNERS" >&2; exit 1; }

# A pinned signer line by comment substring or 1-based index; the first by default.
pick_signer() {
  local want="$1" n=0
  while read -r principal algo b64 comment; do
    [[ -z "${algo:-}" || "$principal" == \#* ]] && continue
    n=$((n + 1))
    if [[ -z "$want" && "$n" == 1 ]] || [[ "$want" == "$n" ]] \
       || { [[ -n "$want" ]] && [[ "$comment" == *"$want"* ]]; }; then
      printf '%s %s %s\n' "$algo" "$b64" "$comment"
      return 0
    fi
  done < "$SIGNERS"
  return 1
}

agent_has_key() { SSH_AUTH_SOCK="$1" ssh-add -L 2>/dev/null | grep -qF "$2"; }

# ssh-keygen -Y sign leaves an existing .sig alone and still exits 0, so a stale signature
# would survive silently. Remove it first.
SIGFILE="$MANIFEST.sig"
rm -f "$SIGFILE"

if [[ -n "$KEY_FILE" ]]; then
  [[ -f "$KEY_FILE" ]] || { echo "error: key file not found: $KEY_FILE" >&2; exit 1; }
  echo "==> signing with the key file $KEY_FILE"
  ssh-keygen -Y sign -n "$NAMESPACE" -f "$KEY_FILE" "$MANIFEST"
else
  SIGNER_LINE="$(pick_signer "$SIGNER")" \
    || { echo "error: no pinned signer matches '${SIGNER:-<first>}' in $SIGNERS" >&2; exit 1; }
  read -r SIGNER_ALGO SIGNER_BLOB SIGNER_NAME <<<"$SIGNER_LINE"
  echo "==> signing as $SIGNER_NAME (watch for a PIN/touch prompt)"

  # An agent that holds this key: --gpg-agent, else SSH_AUTH_SOCK, else gpg-agent's ssh
  # socket (where a YubiKey's OpenPGP key shows up). Checked, because signing through an
  # agent without the key fails with an error that reads like a bad key file.
  if [[ "$USE_GPG_AGENT" -eq 1 ]]; then
    SSH_AUTH_SOCK="$(gpgconf --list-dirs agent-ssh-socket)"; export SSH_AUTH_SOCK
    gpgconf --launch gpg-agent || true
  elif ! agent_has_key "${SSH_AUTH_SOCK:-}" "$SIGNER_BLOB"; then
    gpgsock="$(gpgconf --list-dirs agent-ssh-socket 2>/dev/null || true)"
    if [[ -n "$gpgsock" ]] && { gpgconf --launch gpg-agent 2>/dev/null; agent_has_key "$gpgsock" "$SIGNER_BLOB"; }; then
      SSH_AUTH_SOCK="$gpgsock"; export SSH_AUTH_SOCK
    fi
  fi
  agent_has_key "${SSH_AUTH_SOCK:-}" "$SIGNER_BLOB" || {
    echo "error: no reachable ssh-agent holds $SIGNER_NAME." >&2
    echo "       Plug in the YubiKey, try --gpg-agent, or sign with the offline key: --key-file." >&2
    exit 1
  }
  # -f takes the PUBLIC key; ssh-keygen asks the agent to sign with its private half.
  PUBTMP="$(mktemp -t apparition-signer)"
  trap 'rm -f "$PUBTMP"' EXIT
  printf '%s %s %s\n' "$SIGNER_ALGO" "$SIGNER_BLOB" "$SIGNER_NAME" > "$PUBTMP"
  ssh-keygen -Y sign -n "$NAMESPACE" -f "$PUBTMP" "$MANIFEST"
fi

[[ -f "$SIGFILE" ]] || { echo "error: ssh-keygen made no signature" >&2; exit 1; }

# Catches signing with a key that isn't pinned: valid, and refused by every client.
echo "==> checking it against release-signers"
ssh-keygen -Y verify -f "$SIGNERS" -I "$IDENTITY" -n "$NAMESPACE" -s "$SIGFILE" < "$MANIFEST" | sed 's/^/    /'
