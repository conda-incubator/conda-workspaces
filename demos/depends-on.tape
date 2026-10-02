Source demos/_settings.tape

Output demos/depends-on.gif

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `export PS1="$DEMO_PS1" && mkdir -p /tmp/.demo-home && echo 'PS1="$ "' > /tmp/.demo-home/.bash_profile && export HOME=/tmp/.demo-home`
Enter
Wait /\$/
Type "rm -rf /tmp/deps-demo && mkdir -p /tmp/deps-demo && cd /tmp/deps-demo"
Enter
Wait /\$/
Type "cat > conda.toml << 'EOF'"
Enter
Type "[tasks]"
Enter
Type `lint = "echo '✓ Linting passed'"`
Enter
Type `test = "echo '✓ Tests passed'"`
Enter
Type ""
Enter
Type "[tasks.check]"
Enter
Type `cmd = "echo '✓ All checks complete!'"`
Enter
Type `depends-on = ["lint", "test"]`
Enter
Type "EOF"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Tasks can depend on other tasks"
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

Type "# Running 'check' runs its dependencies first"
Enter
Sleep 500ms

Type@80ms "conda task run check"
Enter
Wait /\$/
Sleep 3s

Type "# Skip dependencies with --skip-deps"
Enter
Sleep 500ms

Type@80ms "conda task run check --skip-deps"
Enter
Wait /\$/
Sleep 3s
