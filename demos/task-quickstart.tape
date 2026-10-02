Source demos/_settings.tape

Output demos/task-quickstart.gif

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `export PS1="$DEMO_PS1" && mkdir -p /tmp/.demo-home && echo 'PS1="$ "' > /tmp/.demo-home/.bash_profile && export HOME=/tmp/.demo-home`
Enter
Wait /\$/
Type "rm -rf /tmp/task-demo && mkdir -p /tmp/task-demo && cd /tmp/task-demo"
Enter
Wait /\$/
Type "cat > conda.toml << 'EOF'"
Enter
Type "[tasks]"
Enter
Type `hello = "echo 'Hello from conda tasks!'"`
Enter
Type `test = "python -m pytest --co -q"`
Enter
Type `lint = "echo 'Linting... all clean!'"`
Enter
Type "EOF"
Enter
Wait /\$/
Type "cat > conftest.py << 'EOF'"
Enter
Type "def test_example():"
Enter
Type "    pass"
Enter
Type "EOF"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Define tasks in conda.toml"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 2s

Type "# List available tasks"
Enter
Sleep 500ms

Type@80ms "conda task list"
Enter
Wait /\$/
Sleep 2s

Type "# Run tasks in your conda environment"
Enter
Sleep 500ms

Type@80ms "conda task run hello"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda task run lint"
Enter
Wait /\$/
Sleep 2s
