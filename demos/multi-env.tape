Source demos/_settings.tape

Output demos/multi-env.gif

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
Type "rm -rf /tmp/multi-env && mkdir -p /tmp/multi-env && cd /tmp/multi-env"
Enter
Wait /\$/
Type `cp "$FIXTURES/multi-env.toml" conda.toml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# One manifest with multiple environments"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type "# Install all environments at once"
Enter
Sleep 500ms

Type@80ms "conda workspace install"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda workspace envs"
Enter
Wait /\$/
Sleep 2s

Type "# Inspect individual environments"
Enter
Sleep 500ms

Type@80ms "conda workspace info -e test"
Enter
Wait /\$/
Sleep 2s

Type@80ms "conda workspace info -e docs"
Enter
Wait /\$/
Sleep 2s

Type "# Run tools in the right environment"
Enter
Sleep 500ms

Type@80ms "conda workspace run -e lint -- ruff --version"
Enter
Wait /\$/
Sleep 3s
