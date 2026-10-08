Require pixi
Require bat

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `set -e -o pipefail && export PS1="$DEMO_PS1"`
Enter
Wait /\$/
Type `python -m conda_ship.cli build --help | grep -q -- --source-lock`
Enter
Wait /\$/
Type `DEMO_SOURCE="$PWD/examples/ship" && DEMO_DIR=$(mktemp -d /tmp/workspace-ship-demo.XXXXXX)`
Enter
Wait /\$/
Type `cp "$DEMO_SOURCE"/{conda.toml,conda.lock} "$DEMO_DIR"/ && cd "$DEMO_DIR"`
Enter
Wait /\$/
Type `DEMO_PLATFORM=$(python -c 'from conda.base.context import context; print(context.subdir)')`
Enter
Wait /\$/
Type `python -c 'import hashlib, pathlib; print(*[hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in ("conda.toml", "conda.lock")], sep="\n")' > source.sha256`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show
