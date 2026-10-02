Source demos/_settings.tape

Output demos/task-pixi-compat.gif

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `export PS1="$DEMO_PS1" && mkdir -p /tmp/.demo-home && echo 'PS1="$ "' > /tmp/.demo-home/.bash_profile && export HOME=/tmp/.demo-home`
Enter
Wait /\$/
Type "rm -rf /tmp/task-pixi && mkdir -p /tmp/task-pixi && cd /tmp/task-pixi"
Enter
Wait /\$/
Type "cat > pixi.toml << 'EOF'"
Enter
Type "[workspace]"
Enter
Type `name = "my-pixi-project"`
Enter
Type `channels = ["conda-forge"]`
Enter
Type `platforms = ["osx-arm64", "linux-64"]`
Enter
Type ""
Enter
Type "[tasks]"
Enter
Type `hello = "echo 'Hello from pixi tasks!'"`
Enter
Type `test = "echo 'Running tests...'"`
Enter
Type ""
Enter
Type "[tasks.check]"
Enter
Type `cmd = "echo 'All good!'"`
Enter
Type `depends-on = ["test"]`
Enter
Type "EOF"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Define tasks in pixi.toml"
Enter
Sleep 500ms

Type@80ms "bat pixi.toml"
Enter
Wait /\$/
Sleep 3s

Type "# List and run them with conda task"
Enter
Sleep 500ms

Type@80ms "conda task list"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda task run check"
Enter
Wait /\$/
Sleep 2s

Type "# Export the tasks to conda.toml"
Enter
Sleep 500ms

Type@80ms "conda task export -o conda.toml"
Enter
Wait /\$/
Sleep 1s

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s
