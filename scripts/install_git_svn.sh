#!/usr/bin/env bash
# GitHub runners may ship a PPA Git newer than Ubuntu's git-svn package.
set -euo pipefail
export LC_ALL=C

sudo_command=()
if (( EUID != 0 )); then
  sudo_command=(sudo)
fi

"${sudo_command[@]}" apt-get update
version="$(apt-cache policy git-svn | awk '$1 == "Candidate:" { print $2; exit }')"
if [[ -z "$version" || "$version" == '(none)' ]]; then
  echo 'Nenhuma versão de git-svn disponível nos repositórios APT.' >&2
  exit 1
fi

# All three binaries are built from the same Git source package. Pin the
# candidate of git-svn, including its epoch, instead of keeping a newer PPA Git.
"${sudo_command[@]}" apt-get install --yes --allow-downgrades \
  "git=$version" "git-man=$version" "git-svn=$version" subversion

git --version
git svn --version
svn --version --quiet
