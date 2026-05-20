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
Type `mkdir -p src tests && echo 'from flask import Flask; app = Flask(__name__)' > src/app.py && echo 'def test_health(): assert True' > tests/test_app.py`
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
Sleep 4s

Type "# --lock solves and writes conda.lock before archiving"
Enter
Sleep 500ms

Type@80ms "conda workspace archive --lock"
Enter
Wait /\$/
Sleep 2s

Type "# Archive contains manifest, lockfile, and source files"
Enter
Sleep 500ms

Type@80ms "tar --list -f weather-api.tar.zst"
Enter
Wait /\$/
Sleep 3s

Type "# Extract and restore the workspace elsewhere"
Enter
Sleep 500ms

Type@80ms "conda workspace unarchive weather-api.tar.zst --target /tmp/restored"
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
