Source demos/_settings.tape

Output demos/archives-bundle.gif
Output demos/archives-bundle.mp4

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
Type "rm -rf /tmp/bundle-demo && mkdir -p /tmp/bundle-demo && cd /tmp/bundle-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/archives.toml" conda.toml`
Enter
Wait /\$/
Type `mkdir -p src tests && echo 'from flask import Flask; app = Flask(__name__)' > src/app.py && echo 'def test_health(): assert True' > tests/test_app.py`
Enter
Wait /\$/
Type "conda workspace install"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Bundle packages for offline/air-gapped deployment"
Enter
Sleep 500ms

Type "# --lock solves first, --bundle includes .conda packages"
Enter
Sleep 500ms

Type@80ms "conda workspace archive --lock --bundle -o offline.tar.zst"
Enter
Wait /\$/
Sleep 2s

Type "# The archive now includes a packages/ directory"
Enter
Sleep 500ms

Type@80ms "tar --list -f offline.tar.zst | grep packages/ | head -5"
Enter
Wait /\$/
Sleep 3s

Type "# On the air-gapped machine: extract and install"
Enter
Sleep 500ms

Type@80ms "conda workspace unarchive offline.tar.zst --target /tmp/offline"
Enter
Wait /\$/
Sleep 2s

Type@80ms "cd /tmp/offline && conda workspace install --locked"
Enter
Wait /\$/
Sleep 3s
