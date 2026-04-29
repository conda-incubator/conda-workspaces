Source demos/_settings.tape

Output demos/shell.gif
Output demos/shell.mp4

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
Type "rm -rf /tmp/shell-demo && mkdir -p /tmp/shell-demo && cd /tmp/shell-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/shell.toml" conda.toml`
Enter
Wait /\$/
Type "conda workspace install"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Spawn an interactive shell in a workspace env"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda workspace shell -e default"
Enter
Wait /\(default\)/
Sleep 1s

Type@80ms "python --version"
Enter
Wait /\(default\)/
Sleep 1500ms

Type@80ms "which python"
Enter
Wait /\(default\)/
Sleep 1500ms

Type "# Exit to return to the parent shell"
Enter
Sleep 500ms

Type@80ms "exit"
Enter
Wait /\$/
Sleep 1500ms
