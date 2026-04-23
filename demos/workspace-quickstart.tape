Source demos/_settings.tape

Output demos/workspace-quickstart.gif
Output demos/workspace-quickstart.mp4

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

Type "# One command: init + add + install + shell"
Enter
Sleep 500ms

Type@80ms `conda workspace quickstart --no-shell --name demo "python=3.12" "numpy>=2"`
Enter
Wait /\$/
Sleep 3s

Type "# The manifest quickstart wrote"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type "# --json gives a scriptable summary, skipping the shell"
Enter
Sleep 500ms

Type "rm -rf /tmp/ws-json && mkdir -p /tmp/ws-json && cd /tmp/ws-json"
Enter
Wait /\$/

Type@80ms `conda workspace quickstart --json --name scripted "python=3.12"`
Enter
Wait /\$/
Sleep 3s
