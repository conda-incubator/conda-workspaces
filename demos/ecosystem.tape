Source demos/_settings.tape

Output demos/ecosystem.gif

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
Type "rm -rf /tmp/ecosystem && mkdir -p /tmp/ecosystem && cd /tmp/ecosystem"
Enter
Wait /\$/
Type `cp "$FIXTURES/ecosystem.toml" conda.toml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Define workspace environments and tasks in one manifest"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type "# Install the workspace"
Enter
Sleep 500ms

Type@80ms "conda workspace install"
Enter
Wait /\$/
Sleep 2s

Type "# List and run tasks in the workspace environment"
Enter
Sleep 500ms

Type@80ms "conda task list"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda task run -e default test"
Enter
Wait /\$/
Sleep 3s
