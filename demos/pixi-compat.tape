Source demos/_settings.tape

Output demos/pixi-compat.gif

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
Type "rm -rf /tmp/pixi-project && mkdir -p /tmp/pixi-project && cd /tmp/pixi-project"
Enter
Wait /\$/
Type `cp "$FIXTURES/pixi-compat.toml" pixi.toml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# conda-workspaces reads pixi.toml"
Enter
Sleep 500ms

Type@80ms "bat pixi.toml"
Enter
Wait /\$/
Sleep 3s

Type "# Install its environments with conda"
Enter
Sleep 500ms

Type@80ms "conda workspace install"
Enter
Wait /\$/
Sleep 2s

Type "# Environments live under .conda/envs/, not .pixi/envs/"
Enter
Sleep 500ms

Type@80ms "ls .conda/envs/"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda workspace run -e test -- pytest --version"
Enter
Wait /\$/
Sleep 3s
