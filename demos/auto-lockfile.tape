Source demos/_settings.tape

Output demos/auto-lockfile.gif
Output demos/auto-lockfile.mp4

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `export PS1="$DEMO_PS1" && mkdir -p /tmp/.demo-home && echo 'PS1="$ "' > /tmp/.demo-home/.bash_profile && export HOME=/tmp/.demo-home`
Enter
Wait /\$/
Type `FIXTURES=$(pwd)/demos/fixtures`
Enter
Wait /\$/
Type "rm -rf /tmp/auto-lock-demo && mkdir -p /tmp/auto-lock-demo && cd /tmp/auto-lock-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/auto-lockfile.toml" conda.toml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Install solves, creates the environment, and writes conda.lock"
Enter
Sleep 500ms

Type@80ms "conda workspace install"
Enter
Wait /\$/
Sleep 2s

Type "# Info confirms the lockfile is up to date"
Enter
Sleep 500ms

Type@80ms "conda workspace info"
Enter
Wait /\$/
Sleep 3s

Type "# Edit the manifest to add a dependency"
Enter
Sleep 500ms

Type@80ms `echo 'requests = ">=2.28"' >> conda.toml`
Enter
Wait /\$/
Sleep 1s

Type "# Install detects the stale lockfile and re-solves automatically"
Enter
Sleep 500ms

Type@80ms "conda workspace install"
Enter
Wait /\$/
Sleep 3s

Type "# Edit the manifest again"
Enter
Sleep 500ms

Type@80ms `echo 'flask = ">=3"' >> conda.toml`
Enter
Wait /\$/
Sleep 1s

Type "# --locked rejects a stale lockfile (useful for CI)"
Enter
Sleep 500ms

Type@80ms "conda workspace install --locked 2>&1 || true"
Enter
Wait /\$/
Sleep 3s

Type "# Fix: lock first, then install --locked"
Enter
Sleep 500ms

Type@80ms "conda workspace lock && conda workspace install --locked"
Enter
Wait /\$/
Sleep 3s
