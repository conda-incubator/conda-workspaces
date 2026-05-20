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

Type "# Package a workspace into a portable archive"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type "# Lock and create the archive (defaults to .tar.zst)"
Enter
Sleep 500ms

Type@80ms "conda workspace lock"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda workspace archive"
Enter
Wait /\$/
Sleep 2s

Type "# Archive contains manifest, lockfile, and source files"
Enter
Sleep 500ms

Type@80ms "tar --list -f my-project.tar.zst | head -10"
Enter
Wait /\$/
Sleep 3s

Type "# Extract the archive on another machine"
Enter
Sleep 500ms

Type@80ms "conda workspace unarchive my-project.tar.zst --target /tmp/restored"
Enter
Wait /\$/
Sleep 2s

Type@80ms "ls /tmp/restored/"
Enter
Wait /\$/
Sleep 2s

Type "# Install from the lockfile -- no solving needed"
Enter
Sleep 500ms

Type@80ms "cd /tmp/restored && conda workspace install --locked"
Enter
Wait /\$/
Sleep 3s
