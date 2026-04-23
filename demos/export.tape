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

Type "# Export the manifest to environment.yml — no install required"
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

Type "# Same plugin system as 'conda export' — JSON works by filename alone"
Enter
Sleep 500ms

Type@80ms "conda workspace export --file environment.json --platform linux-64"
Enter
Wait /\$/
Sleep 2s

Type "# Re-emit the lockfile across every declared platform"
Enter
Sleep 500ms

Type@80ms "conda workspace lock"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda workspace export --from-lockfile --format conda-workspaces-lock-v1 --file conda.lock.copy"
Enter
Wait /\$/
Sleep 2s

Type@80ms "diff conda.lock conda.lock.copy && echo 'byte-identical'"
Enter
Wait /\$/
Sleep 3s
