Source demos/_settings.tape

Output demos/import.gif
Output demos/import.mp4

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
Type "rm -rf /tmp/import-demo && mkdir -p /tmp/import-demo && cd /tmp/import-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/import-env.yml" environment.yml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Import an environment.yml into a conda.toml workspace"
Enter
Sleep 500ms

Type@80ms "bat environment.yml"
Enter
Wait /\$/
Sleep 3s

Type@80ms "conda workspace import environment.yml"
Enter
Wait /\$/
Sleep 1s

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 4s

Hide
Type "rm conda.toml"
Enter
Wait /\$/
Type `cp "$FIXTURES/import-ap.yml" anaconda-project.yml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Import an anaconda-project.yml"
Enter
Sleep 500ms

Type@80ms "bat anaconda-project.yml"
Enter
Wait /\$/
Sleep 4s

Type@80ms "conda workspace import anaconda-project.yml"
Enter
Wait /\$/
Sleep 1s

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 5s
