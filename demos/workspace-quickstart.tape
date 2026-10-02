Source demos/_settings.tape

Output demos/workspace-quickstart.gif

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `export PS1="$DEMO_PS1" && mkdir -p /tmp/.demo-home && echo 'PS1="$ "' > /tmp/.demo-home/.bash_profile && export HOME=/tmp/.demo-home`
Enter
Wait /\$/
Type "rm -rf /tmp/ws-quickstart && mkdir -p /tmp/ws-quickstart && cd /tmp/ws-quickstart"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Initialize a workspace, add dependencies, and install without opening a shell"
Enter
Sleep 500ms

Type@80ms `conda workspace quickstart --no-shell --name demo -c conda-forge "python=3.12" "numpy>=2"`
Enter
Wait /\$/
Sleep 3s

Type "# Inspect the manifest written by quickstart"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type "# --json returns a summary for scripts without opening a shell"
Enter
Sleep 500ms

Type "rm -rf /tmp/ws-json && mkdir -p /tmp/ws-json && cd /tmp/ws-json"
Enter
Wait /\$/

Type@80ms `conda workspace quickstart --json --name scripted -c conda-forge "python=3.12"`
Enter
Wait /\$/
Sleep 3s
