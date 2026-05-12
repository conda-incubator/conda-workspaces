Source demos/_settings.tape

Output demos/archives.gif
Output demos/archives.mp4

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
Type "rm -rf /tmp/archives-demo && mkdir -p /tmp/archives-demo && cd /tmp/archives-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/archives.toml" conda.toml`
Enter
Wait /\$/
Type `mkdir -p src && echo 'print("hello")' > src/app.py`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Create a workspace archive from the current project"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type@80ms "conda workspace lock"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda workspace archive -o my-project.tar.gz"
Enter
Wait /\$/
Sleep 2s

Type "# Archive includes the manifest, lockfile, and source files"
Enter
Sleep 500ms

Type@80ms "tar tzf my-project.tar.gz"
Enter
Wait /\$/
Sleep 3s

Type "# Extract the archive into a new directory"
Enter
Sleep 500ms

Type@80ms "conda workspace unarchive my-project.tar.gz --target /tmp/restored"
Enter
Wait /\$/
Sleep 2s

Type@80ms "ls /tmp/restored/"
Enter
Wait /\$/
Sleep 3s
