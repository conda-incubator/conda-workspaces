Source demos/_settings.tape

Output demos/lockfile.gif
Output demos/lockfile.mp4

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
Type "rm -rf /tmp/lockfile-demo && mkdir -p /tmp/lockfile-demo && cd /tmp/lockfile-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/lockfile.toml" conda.toml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Install and solve for the current platform"
Enter
Sleep 500ms

Type@80ms "conda workspace install"
Enter
Wait /\$/
Sleep 2s

Type "# Lock every platform declared in conda.toml"
Enter
Sleep 500ms

Type@80ms "conda workspace lock"
Enter
Wait /\$/
Sleep 2s

Type "# Both platforms are captured in a single conda.lock"
Enter
Sleep 500ms

Type@80ms `rg '^      (linux-64|osx-arm64):$' conda.lock`
Enter
Wait /\$/
Sleep 2s

Type@80ms "head -15 conda.lock"
Enter
Wait /\$/
Sleep 3s

Type "# Clean and reinstall from lock — no solving needed"
Enter
Sleep 500ms

Type@80ms "conda workspace clean --yes"
Enter
Wait /\$/
Sleep 1s

Type@80ms "conda workspace install --locked"
Enter
Wait /\$/
Sleep 3s
