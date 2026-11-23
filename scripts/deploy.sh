#!/usr/bin/env bash
# Deploy a tested main-branch commit on the existing Ubuntu host. No DB restore.
set -euo pipefail

target=${1:-}
[[ "$target" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full commit SHA'; exit 1; }
[[ $(id -u) == 0 ]] || { echo 'This deployment expects the existing root-managed installation'; exit 1; }
project=/opt/equipment_bot_cinema
cd "$project"
exec 9>/run/lock/equipment-bot-deploy.lock
flock -n 9 || { echo 'Another deployment is running'; exit 1; }
[[ -f .env && -f /opt/equipment_bot_cinema.full.yml ]]
[[ -f /usr/local/sbin/equipment-backup.py ]]
git diff --quiet
git diff --cached --quiet
old_sha=$(git rev-parse HEAD)
old_image=$(docker inspect --format '{{.Image}}' equipment_bot_cinema-equipment-bot-1)
git fetch origin main
git cat-file -e "$target^{commit}"
git merge-base --is-ancestor "$target" origin/main
if [[ -n $(git diff --name-only "$old_sha" "$target" -- app/models.py app/db.py) ]]; then
  echo 'Database model/init changed: use a separately reviewed migration and deployment plan.'
  exit 1
fi

# Record the old version before touching the checkout or running container.
python3 /usr/local/sbin/equipment-backup.py
rollback_tag="equipment-bot-release:rollback-$old_sha"
docker image tag "$old_image" "$rollback_tag"
override=$(mktemp /var/tmp/equipment-bot-deploy.XXXXXX.yml)
compose=(docker compose --project-name equipment_bot_cinema -f docker-compose.yml -f /opt/equipment_bot_cinema.full.yml -f "$override")
started=0
changed=0
finish() {
  result=$?
  trap - EXIT
  set +e
  if (( result != 0 && changed )); then
    echo 'Deployment failed; restoring the previous code/image. Database is not restored.'
    git checkout --detach "$old_sha"
    if (( started )); then
      printf 'services:\n  equipment-bot:\n    image: %s\n' "$rollback_tag" > "$override"
      if ! "${compose[@]}" up -d --no-build --pull never; then
        echo 'Rollback could not restart the previous image; operator intervention required.'
      fi
    fi
  fi
  rm -f "$override"
  exit "$result"
}
trap finish EXIT

git checkout --detach "$target"
changed=1
printf 'services:\n  equipment-bot:\n    image: equipment-bot-release:%s\n' "$target" > "$override"
"${compose[@]}" build
started=1
"${compose[@]}" up -d --no-build --pull never
for attempt in {1..30}; do
  running=$(docker inspect --format '{{.State.Running}}' equipment_bot_cinema-equipment-bot-1)
  if [[ "$running" == true ]] && curl -fsS --max-time 3 http://127.0.0.1:8080/login > /dev/null; then
    sleep 3
    [[ $(docker inspect --format '{{.State.Running}}' equipment_bot_cinema-equipment-bot-1) == true ]] || continue
    # Keep the normal full.yml maintenance commands on the same current image.
    docker image tag "equipment-bot-release:$target" equipment_bot_cinema-equipment-bot:latest
    echo "Deployment ready: $target. Check /start and the issue-time form manually."
    exit 0
  fi
  sleep 2
done
echo 'Application health check failed.'
exit 1
