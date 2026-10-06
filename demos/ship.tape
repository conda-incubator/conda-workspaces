Source demos/_settings.tape
Output demos/ship.gif
Output docs/_build/ship-demo.txt
Source demos/_ship_setup.tape

Type "# Ship a locked Python environment"
Enter
Type@25ms "bat conda.toml"
Enter
Wait /\$/
Sleep 4s

Type "clear"
Enter
Wait /\$/
Type "# Preview without downloading or writing artifacts"
Enter
Type@25ms `conda workspace ship -e runtime --platform "$DEMO_PLATFORM" \`
Enter
Type@25ms `  -o dist --dry-run --json 2>preview.log`
Enter
Wait+Line /^\$ *$/
Sleep 3s
Type `test ! -e dist && echo "No artifacts written"`
Enter
Wait+Line /^\$ *$/
Sleep 2s

Type "clear"
Enter
Wait /\$/
Type "# Build the online launcher from the same lock"
Enter
Type@25ms `conda workspace ship -e runtime --platform "$DEMO_PLATFORM" \`
Enter
Type@25ms `  -o dist --json 2>build.log`
Enter
Hide
Sleep 500ms
Wait+Line@10m /^\$ *$/
Show
Sleep 3s

Type "clear"
Enter
Wait /\$/
Type "# First launch installs Python, later launches reuse it"
Enter
Type@25ms `CONDA_SHIP_PREFIX="$PWD/.runtime" ./dist/workspace-python --version 2>runtime.log`
Enter
Hide
Sleep 500ms
Wait+Line@10m /^\$ *$/
Show
Sleep 3s
Type@25ms `CONDA_SHIP_PREFIX="$PWD/.runtime" ./dist/workspace-python \`
Enter
Type@25ms `  -c 'print("Hello from the locked environment")'`
Enter
Wait+Line /^\$ *$/
Sleep 3s

Hide
Type `python -c 'import hashlib, pathlib; print(*[hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in ("conda.toml", "conda.lock")], sep="\n")' > source-after.sha256`
Enter
Wait /\$/
Type "clear"
Enter
Wait /\$/
Show
Type "# Keep the source files and the generated SBOM"
Enter
Type `diff source.sha256 source-after.sha256 && echo "Manifest and lock unchanged"`
Enter
Wait+Line /^\$ *$/
Type `test ! -d .conda && echo "No workspace environment installed"`
Enter
Wait+Line /^\$ *$/
Type `ls dist/*.cdx.json`
Enter
Wait+Line /^\$ *$/
Sleep 4s
