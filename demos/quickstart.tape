Source demos/_settings.tape

Output demos/quickstart.gif
Output demos/quickstart.mp4

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `export PS1="$DEMO_PS1" && mkdir -p /tmp/.demo-home && echo 'PS1="$ "' > /tmp/.demo-home/.bash_profile && export HOME=/tmp/.demo-home && export PYTHONWARNINGS=ignore::SyntaxWarning`
Enter
Wait /\$/
Type "rm -rf /tmp/my-project && mkdir -p /tmp/my-project && cd /tmp/my-project"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# Initialize a new workspace"
Enter
Sleep 500ms

Type@80ms "conda workspace init --name my-project -c conda-forge"
Enter
Wait /\$/
Sleep 1s

Type "# Add dependencies, then declare a named environment"
Enter
Sleep 500ms

Type@80ms `conda workspace add "python>=3.12" "numpy>=2"`
Enter
Wait /\$/
Sleep 1s

Type@80ms "conda workspace add -e test"
Enter
Wait /\$/
Sleep 1s

Type "# Inspect the manifest conda-workspaces wrote"
Enter
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type "# See the environments it built"
Enter
Sleep 500ms

Type@80ms "conda workspace envs"
Enter
Wait /\$/
Sleep 2s

Type "# Run a command in the test environment"
Enter
Sleep 500ms

Type@80ms `conda workspace run -e test -- python -c "import numpy; print(numpy.__version__)"`
Enter
Wait /\$/
Sleep 3s
