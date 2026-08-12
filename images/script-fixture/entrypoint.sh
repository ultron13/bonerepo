#!/bin/sh
# Builds two identical repositories -- one anonymous, one behind Basic auth --
# each with a `main` branch that verifies clean and a `broken` branch whose
# manifest declares a data file that is not there.
set -eu

WORK=/tmp/work
git config --global user.email "fixture@plimsoll.dev"
git config --global user.name "Plimsoll fixture"
git config --global init.defaultBranch main

rm -rf "$WORK"
mkdir -p "$WORK"
cp -r /seed/repo/. "$WORK"
cd "$WORK"
git init --quiet
git add -A
git commit --quiet -m "Checkout plan against the demo target"

git checkout --quiet -b broken
sed -i 's|path: data/users.csv|path: data/missing.csv|' perf/plimsoll.yaml
git commit --quiet -am "Declare a data file that is not present"
git checkout --quiet main

for VISIBILITY in public private; do
    rm -rf "/srv/git/$VISIBILITY"
    mkdir -p "/srv/git/$VISIBILITY"
    git init --quiet --bare "/srv/git/$VISIBILITY/plans.git"
    git push --quiet "/srv/git/$VISIBILITY/plans.git" main broken
    # Read-only: nothing in Plimsoll pushes to a script repository.
    git --git-dir="/srv/git/$VISIBILITY/plans.git" config http.receivepack false
    git --git-dir="/srv/git/$VISIBILITY/plans.git" config http.uploadpack true
done

htpasswd -bc /etc/nginx/htpasswd plimsoll plimsoll-fixture-token

spawn-fcgi -s /var/run/fcgiwrap.sock -M 666 /usr/sbin/fcgiwrap
exec nginx -g 'daemon off;'
