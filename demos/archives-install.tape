Source demos/_settings.tape

Output demos/archives-install.gif
Output demos/archives-install.mp4

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
Type "rm -rf /tmp/install-demo && mkdir -p /tmp/install-demo && cd /tmp/install-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/archives.toml" conda.toml`
Enter
Wait /\$/
Type `mkdir -p src tests && echo 'from flask import Flask; app = Flask(__name__)' > src/app.py && echo 'def test_health(): assert True' > tests/test_app.py`
Enter
Wait /\$/
Type "conda workspace lock"
Enter
Wait /\$/
Type "conda workspace archive"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Extract and install in one step with --install"
Enter
Sleep 500ms

Type@80ms "ls weather-api.tar.zst"
Enter
Wait /\$/
Sleep 1s

Type@80ms "conda workspace unarchive weather-api.tar.zst --target /tmp/deployed --install"
Enter
Wait /\$/
Sleep 3s

Type "# The workspace is ready to use"
Enter
Sleep 500ms

Type@80ms "ls /tmp/deployed/"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda workspace envs -f /tmp/deployed/conda.toml"
Enter
Wait /\$/
Sleep 3s
