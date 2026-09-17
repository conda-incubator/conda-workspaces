Source demos/_settings.tape

Output demos/image.gif
Output demos/image.mp4

Require docker
Require pixi
Require bat

Hide
Type `eval "$(pixi shell-hook -s bash -e dev)"`
Enter
Wait /\)/
Type `set -e -o pipefail && export PS1="$DEMO_PS1"`
Enter
Wait /\$/
Type `test "$(docker info --format '{{.OSType}}')" = linux && docker buildx version >/dev/null`
Enter
Wait /\$/
Type `case "$(docker info --format '{{.Architecture}}')" in aarch64|arm64) DEMO_PLATFORM=linux-aarch64 ;; x86_64|amd64) DEMO_PLATFORM=linux-64 ;; *) exit 1 ;; esac`
Enter
Wait /\$/
Type `DEMO_SOURCE="$PWD/examples/container" && DEMO_DIR=$(mktemp -d "${TMPDIR:-/tmp}/workspace-image-demo.XXXXXX")`
Enter
Wait /\$/
Type `cp "$DEMO_SOURCE"/{conda.toml,conda.lock,activate.sh,workspace_container_example.py} "$DEMO_DIR"/ && cd "$DEMO_DIR"`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show

Type "# A locked environment, copied source, and activation settings"
Enter
Sleep 500ms

Type@60ms "bat conda.toml"
Enter
Wait /\$/
Sleep 3s

Type "# Build for the Docker host's Linux architecture"
Enter
Type `echo "$DEMO_PLATFORM"`
Enter
Wait /\$/
Sleep 1s

Type@60ms `conda workspace image -e runtime --platform "$DEMO_PLATFORM" \`
Enter
Type@60ms `  -t workspace-image-demo --load -- python -m workspace_container_example`
Sleep 1s
Enter
Hide
Wait+Line@20m /^\$ *$/
Show
Sleep 2s

Type "clear"
Enter
Wait /\$/
Type "# Run offline with the locked Python and activation hooks"
Enter
Sleep 500ms

Type@60ms "docker run --rm --network none workspace-image-demo"
Enter
Wait+Line /^\$ *$/
Sleep 4s

Type "# Override the command without losing activation"
Enter
Sleep 500ms

Type@60ms `docker run --rm --network none workspace-image-demo \`
Enter
Type@60ms `  python -m workspace_container_example "another argument"`
Enter
Wait+Line /^\$ *$/
Sleep 4s
