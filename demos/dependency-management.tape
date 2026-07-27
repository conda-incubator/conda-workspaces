Source demos/_settings.tape

Output demos/dependency-management.gif
Output demos/dependency-management.mp4

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
Type "rm -rf /tmp/dependency-demo && mkdir -p /tmp/dependency-demo && cd /tmp/dependency-demo"
Enter
Wait /\$/
Type `cp "$FIXTURES/dependency-management.toml" conda.toml`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Put dependencies in a shared feature or one environment"
Enter
Sleep 500ms

Type@80ms `conda workspace add --feature test "pytest>=8,<9" --no-lockfile-update`
Enter
Wait /\$/
Sleep 1s

Type@80ms "conda workspace add --environment test coverage --no-lockfile-update"
Enter
Wait /\$/
Sleep 1s

Type "# Platform selectors target one declaration table"
Enter
Sleep 500ms

Type@80ms "conda workspace add --environment test --platform linux-64 typing_extensions --no-lockfile-update"
Enter
Wait /\$/
Sleep 1s

Type "# A bare update keeps the existing pytest constraint"
Enter
Sleep 500ms

Type@80ms "conda workspace update --feature test pytest --no-install"
Enter
Wait /\$/
Sleep 2s

Type "# The manifest keeps each dependency at its chosen location"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 4s
