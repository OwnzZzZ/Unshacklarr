#!/bin/sh
# A release reuses the image main built for its commit: the same manifest put under the version's tag,
# so latest, the commit and the version are one image, the one main tested. Waits for main's build
# when both were pushed together; fails (exit 1) when that image never comes, and the caller builds.
#   sh tools/ci/retag.sh <registry> <owner/name> <from tag> <to tag>   (REGISTRY_USER, REGISTRY_PASSWORD)
set -eu
registry=$1 repo=$2 from=$3 to=$4
tries=${RETAG_TRIES:-45}  # 15 min, every 20 s: main's run builds meanwhile
accept="application/vnd.oci.image.index.v1+json,application/vnd.oci.image.manifest.v1+json"
accept="$accept,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# A token for the repository, from the registry's own challenge (ghcr.io and Forgejo answer alike)
challenge=$(curl -s -o /dev/null -D - "https://$registry/v2/" | tr -d '\r' | grep -i '^www-authenticate:' || true)
realm=$(echo "$challenge" | sed -n 's/.*realm="\([^"]*\)".*/\1/p')
service=$(echo "$challenge" | sed -n 's/.*service="\([^"]*\)".*/\1/p')
token=$(curl -sf -u "$REGISTRY_USER:$REGISTRY_PASSWORD" \
  "$realm?service=$service&scope=repository:$repo:pull,push" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
[ -n "$token" ] || { echo "No registry token: the image is built instead"; exit 1; }

i=0
until curl -sf -o "$work/manifest" -D "$work/headers" -H "Authorization: Bearer $token" -H "Accept: $accept" \
    "https://$registry/v2/$repo/manifests/$from"; do
  i=$((i + 1))
  [ "$i" -lt "$tries" ] || { echo "No image $from after $((tries / 3)) min: the image is built instead"; exit 1; }
  echo "Waiting for $from, built by main's run…"
  sleep 20
done
type=$(tr -d '\r' < "$work/headers" | sed -n 's/^[Cc]ontent-[Tt]ype: *//p')
curl -sf -X PUT -H "Authorization: Bearer $token" -H "Content-Type: $type" --data-binary @"$work/manifest" \
  "https://$registry/v2/$repo/manifests/$to" >/dev/null
echo "$repo:$to is $repo:$from, not built again"
