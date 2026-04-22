Source demos/_settings.tape

Output demos/multi-platform.gif
Output demos/multi-platform.mp4

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
Type "rm -rf /tmp/multi-platform-demo && mkdir -p /tmp/multi-platform-demo && cd /tmp/multi-platform-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/multi-platform.toml" conda.toml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Workspace targets linux-64 + osx-arm64; a windows feature adds win-64"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type "# 'info' surfaces the reachable platform set across all envs"
Enter
Sleep 500ms

Type@80ms "conda workspace info"
Enter
Wait /\$/
Sleep 3s

Type "# Default lock covers every (env, platform) pair"
Enter
Sleep 500ms

Type@80ms "conda workspace lock"
Enter
Wait /\$/
Sleep 2s

Type@80ms `rg '^      (linux-64|osx-arm64|win-64):$' conda.lock`
Enter
Wait /\$/
Sleep 2s

Type "# Narrow to a subset when iterating — typos fail fast"
Enter
Sleep 500ms

Type@80ms "conda workspace lock --platform linux-64"
Enter
Wait /\$/
Sleep 2s

Type@80ms `rg '^      (linux-64|osx-arm64|win-64):$' conda.lock`
Enter
Wait /\$/
Sleep 3s
