#!/usr/bin/env bash
# Copy out/ to the hist15-research-data branch (runs/<date>/), retrying on push races.
set -u
[ -n "$(ls -A out 2>/dev/null)" ] || exit 0
git config user.name "weather-bot"
git config user.email "weather-bot@users.noreply.github.com"
stamp="$(date -u +%Y%m%d)"
rm -rf /tmp/keep && mkdir -p /tmp/keep && cp -r out /tmp/keep/out
here="$(git rev-parse HEAD)"
for i in 1 2 3 4 5 6 7 8 9 10; do
  if git fetch -q origin hist15-research-data 2>/dev/null; then
    git checkout -q -f -B data origin/hist15-research-data
  else
    git checkout -q -f --orphan data && git rm -rq --cached . || true
  fi
  git clean -fdxq -e out
  mkdir -p "runs/$stamp" && cp -r /tmp/keep/out/. "runs/$stamp/"
  git add runs
  if ! git commit -qm "hist15 output $stamp $1 [skip ci]"; then break; fi
  if git push -q origin HEAD:hist15-research-data; then break; fi
  sleep $((i * 7))
done
git checkout -q -f "$here"
