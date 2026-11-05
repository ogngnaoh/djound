#!/usr/bin/env bash
# Source this file from Bash or zsh to select project-local tools when installed.
if [[ -n "${ZSH_VERSION:-}" ]]; then
  DJOUND_ROOT="$(cd "$(dirname "${(%):-%x}")/.." && pwd)"
else
  DJOUND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
export DJOUND_ROOT
if [[ -d "$DJOUND_ROOT/.tools/jdk/Contents/Home" ]]; then
  export JAVA_HOME="$DJOUND_ROOT/.tools/jdk/Contents/Home"
  export PATH="$JAVA_HOME/bin:$PATH"
fi
if [[ -d "$DJOUND_ROOT/.tools/node/bin" ]]; then
  export PATH="$DJOUND_ROOT/.tools/node/bin:$PATH"
fi
if [[ -d "$DJOUND_ROOT/.tools/maven/bin" ]]; then
  export PATH="$DJOUND_ROOT/.tools/maven/bin:$PATH"
fi
export npm_config_cache="$DJOUND_ROOT/.cache/npm"
export MAVEN_OPTS="${MAVEN_OPTS:-} -Dmaven.repo.local=$DJOUND_ROOT/.cache/m2"
export MAVEN_USER_HOME="$DJOUND_ROOT/.cache/maven-user"
export DJOUND_DATA_DIR="${DJOUND_DATA_DIR:-$DJOUND_ROOT/.runtime/data}"
