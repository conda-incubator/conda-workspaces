Source demos/_settings.tape

Output demos/templates.gif

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `export PS1="$DEMO_PS1" && mkdir -p /tmp/.demo-home && echo 'PS1="$ "' > /tmp/.demo-home/.bash_profile && export HOME=/tmp/.demo-home`
Enter
Wait /\$/
Type "rm -rf /tmp/tpl-demo && mkdir -p /tmp/tpl-demo && cd /tmp/tpl-demo"
Enter
Wait /\$/
Type "cat > conda.toml << 'EOF'"
Enter
Type "[tasks]"
Enter
Type `platform = "echo 'Running on {{ conda.platform }}'"`
Enter
Type `info = "echo 'conda {{ conda.version }} on {{ conda.platform }}'"`
Enter
Type ""
Enter
Type "[tasks.greet]"
Enter
Type `cmd = "echo 'Hello, {{ name }}!'"`
Enter
Type ""
Enter
Type "[[tasks.greet.args]]"
Enter
Type `arg = "name"`
Enter
Type `default = "world"`
Enter
Type "EOF"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Tasks support Jinja2 templates"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type "# Templates resolve conda context variables"
Enter
Sleep 500ms

Type@80ms "conda task run platform"
Enter
Wait /\$/
Sleep 2s

Type "# Tasks can accept arguments"
Enter
Sleep 500ms

Type@80ms "conda task run greet"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda task run greet conda-user"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda task run info"
Enter
Wait /\$/
Sleep 3s
