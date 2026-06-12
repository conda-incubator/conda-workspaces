Source demos/_settings.tape

Output demos/archives-receipt.gif
Output demos/archives-receipt.mp4

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
Type "rm -rf /tmp/receipt-demo && mkdir -p /tmp/receipt-demo && cd /tmp/receipt-demo"
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

Type "# Create an archive with an external receipt"
Enter
Sleep 500ms

Type@80ms "conda workspace archive --lock --receipt -o weather-api.tar.zst"
Enter
Wait /\$/
Sleep 2s

Type "# The receipt is an in-toto Statement JSON sidecar"
Enter
Sleep 500ms

Type@80ms "ls weather-api.tar.zst*"
Enter
Wait /\$/
Sleep 1s

Type@80ms "python -m json.tool weather-api.tar.zst.receipt.json | sed -n '1,24p'"
Enter
Wait /\$/
Sleep 4s

Type "# Verify the archive and extracted lockfile before moving into place"
Enter
Sleep 500ms

Type@80ms "conda workspace unarchive weather-api.tar.zst --receipt --target /tmp/receipt-restored"
Enter
Wait /\$/
Sleep 2s

Type@80ms "ls /tmp/receipt-restored/"
Enter
Wait /\$/
Sleep 2s
