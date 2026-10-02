Source demos/_settings.tape

Output demos/export.gif

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

Type "# conda workspace export uses conda's exporter plugins"
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

Type "# Export the workspace to conda.toml for both platforms"
Enter
Sleep 500ms

Type@80ms "conda workspace export --format conda-toml --platform linux-64 --platform osx-arm64"
Enter
Wait /\$/
Sleep 4s

Type "# Export the same content under [tool.conda] for pyproject.toml"
Enter
Sleep 500ms

Type@80ms "conda workspace export --format pyproject-toml --platform linux-64 --platform osx-arm64"
Enter
Wait /\$/
Sleep 4s

Type "# Export conda-lock-v1 from the lockfile without solving again"
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
