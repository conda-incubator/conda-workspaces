Source demos/_settings.tape

Output demos/platform-overrides.gif

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `export PS1="$DEMO_PS1" && mkdir -p /tmp/.demo-home && echo 'PS1="$ "' > /tmp/.demo-home/.bash_profile && export HOME=/tmp/.demo-home`
Enter
Wait /\$/
Type "rm -rf /tmp/override-demo && mkdir -p /tmp/override-demo && cd /tmp/override-demo"
Enter
Wait /\$/
Type "cat > conda.toml << 'EOF'"
Enter
Type "[tasks]"
Enter
Type `clean = "rm -rf build/"`
Enter
Type ""
Enter
Type "[tasks.build]"
Enter
Type `cmd = "echo 'Building on Unix...'"`
Enter
Type `depends-on = ["clean"]`
Enter
Type ""
Enter
Type "[target.win-64.tasks]"
Enter
Type `clean = "rmdir /s /q build"`
Enter
Type ""
Enter
Type "[target.win-64.tasks.build]"
Enter
Type `cmd = "echo 'Building on Windows...'"`
Enter
Type `depends-on = ["clean"]`
Enter
Type "EOF"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Tasks can have platform-specific overrides"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type@80ms "conda task list"
Enter
Wait /\$/
Sleep 2s

Type "# The right version runs for your platform"
Enter
Sleep 500ms

Type@80ms "conda task run build"
Enter
Wait /\$/
Sleep 3s
