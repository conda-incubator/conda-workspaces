Source demos/_settings.tape

Output demos/showcase.gif
Output demos/showcase.mp4

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
Type "rm -rf /tmp/showcase && mkdir -p /tmp/showcase && cd /tmp/showcase"
Enter
Wait /\$/
Type "cat > test_demo.py << 'PYEOF'"
Enter
Type "def test_one(): assert 1 + 1 == 2"
Enter
Type "def test_two(): assert 'conda' in 'conda-workspaces'"
Enter
Type "PYEOF"
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

# --- Act 1: Set the scene ---

Sleep 5s

Type "# From zero to a working multi-env project in 2 minutes"
Enter
Sleep 2s

# --- Act 2: The new way ---

Type "# Step 1: scaffold a workspace"
Enter
Sleep 500ms

Type@50ms "conda workspace init --name my-project"
Enter
Wait /\$/
Sleep 1s

Type "# Step 2: declare dependencies"
Enter
Sleep 500ms

Type@50ms `conda workspace add "python>=3.12" "numpy>=2"`
Enter
Wait /\$/
Sleep 1s

Type@50ms `conda workspace add -e test "pytest>=8"`
Enter
Wait /\$/
Sleep 1s

Type "# Here's what we've got"
Enter
Sleep 500ms

Type@50ms "bat conda.toml"
Enter
Wait /\$/
Sleep 6s

Type "# Step 3: install all environments"
Enter
Sleep 8s

Type@50ms "conda workspace install"
Enter
Wait /\$/
Sleep 2s

Type "# Two environments, ready to go"
Enter
Sleep 3s

Type@80ms "conda workspace envs"
Enter
Wait /\$/
Sleep 2s

Type "# Run code in the test environment"
Enter
Sleep 500ms

Type@80ms `conda workspace run -e test -- python -c "import numpy; print('numpy', numpy.__version__)"`
Enter
Wait /\$/
Sleep 2s

Type "# Step 4: add tasks for automation"
Enter
Sleep 500ms

Type@80ms `cat "$FIXTURES/showcase.toml" >> conda.toml`
Enter
Wait /\$/
Sleep 500ms

Type@80ms "bat conda.toml"
Enter
Wait /\$/
Sleep 4s

Type@80ms "conda task list"
Enter
Wait /\$/
Sleep 2s

Type "# Run the test task — dependencies resolve automatically"
Enter
Sleep 500ms

Type@80ms "conda task run -e test test"
Enter
Wait /\$/
Sleep 2s

# --- Act 3: The payoff ---

Type "# One manifest. Multiple environments. Tasks. Lockfile. All conda."
Enter
Sleep 15s
