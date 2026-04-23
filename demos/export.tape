Source demos/_settings.tape

Output demos/export.gif
Output demos/export.mp4

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
Type "rm -rf /tmp/export-demo && mkdir -p /tmp/export-demo && cd /tmp/export-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/lockfile.toml" conda.toml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# conda workspace export plugs into conda's exporter plugin system"
Enter
Sleep 500ms

Type@80ms "conda workspace export --file environment.yml --platform linux-64"
Enter
Wait /\$/
Sleep 2s

Type@80ms "cat environment.yml"
Enter
Wait /\$/
Sleep 3s

Type "# Cross-format: emit conda.toml for the workspace across two platforms"
Enter
Sleep 500ms

Type@80ms "conda workspace export --format conda-toml --platform linux-64 --platform osx-arm64"
Enter
Wait /\$/
Sleep 4s

Type "# Same content, nested under [tool.conda] — drops straight into pyproject.toml"
Enter
Sleep 500ms

Type@80ms "conda workspace export --format pyproject-toml --platform linux-64 --platform osx-arm64"
Enter
Wait /\$/
Sleep 4s

Type "# Lock once, then re-emit in any format without re-solving"
Enter
Sleep 500ms

Type@80ms "conda workspace lock"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda workspace export --from-lockfile --format conda-lock-v1 --file env.conda-lock.yml"
Enter
Wait /\$/
Sleep 2s

Type@80ms "head -8 env.conda-lock.yml"
Enter
Wait /\$/
Sleep 3s
