Source demos/_settings.tape

Output demos/caching.gif

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `export PS1="$DEMO_PS1" && mkdir -p /tmp/.demo-home && echo 'PS1="$ "' > /tmp/.demo-home/.bash_profile && export HOME=/tmp/.demo-home`
Enter
Wait /\$/
Type "rm -rf /tmp/cache-demo && mkdir -p /tmp/cache-demo && cd /tmp/cache-demo"
Enter
Wait /\$/
Type "mkdir -p src dist"
Enter
Wait /\$/
Type `echo "print('hello')" > src/main.py`
Enter
Wait /\$/
Type "cat > conda.toml << 'EOF'"
Enter
Type "[tasks.build]"
Enter
Type `cmd = "echo 'Building...' && cp src/main.py dist/main.py && echo 'Done!'"`
Enter
Type `inputs = ["src/*.py"]`
Enter
Type `outputs = ["dist/*.py"]`
Enter
Type "EOF"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Tasks with inputs/outputs are cached"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type "# The first run executes the task"
Enter
Sleep 500ms

Type@80ms "conda task run build"
Enter
Wait /\$/
Sleep 2s

Type "# The second run skips the unchanged task"
Enter
Sleep 500ms

Type@80ms "conda task run build"
Enter
Wait /\$/
Sleep 2s

Type "# Modify a source file to invalidate the cache"
Enter
Sleep 500ms

Type@80ms `echo "# updated" >> src/main.py`
Enter
Wait /\$/
Sleep 500ms

Type@80ms "conda task run build"
Enter
Wait /\$/
Sleep 3s
