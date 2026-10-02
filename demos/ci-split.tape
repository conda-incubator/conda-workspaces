Source demos/_settings.tape

Output demos/ci-split.gif

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
Type "rm -rf /tmp/ci-split-demo && mkdir -p /tmp/ci-split-demo && cd /tmp/ci-split-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/lockfile.toml" conda.toml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Each CI runner locks one platform into its own fragment file"
Enter
Sleep 500ms

Type@80ms "conda workspace lock --platform linux-64 --output conda.lock.linux-64"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda workspace lock --platform osx-arm64 --output conda.lock.osx-arm64"
Enter
Wait /\$/
Sleep 2s

Type@80ms "ls conda.lock.*"
Enter
Wait /\$/
Sleep 2s

Type "# The coordinator merges fragments into one conda.lock without solving"
Enter
Sleep 500ms

Type@80ms "conda workspace lock --merge 'conda.lock.*'"
Enter
Wait /\$/
Sleep 2s

Type@80ms "head -15 conda.lock"
Enter
Wait /\$/
Sleep 3s
