Source demos/_settings.tape
Output demos/ship-bundle.gif
Output docs/_build/ship-bundle-demo.txt
Source demos/_ship_setup.tape

Type "# Embed the locked package archives in the launcher"
Enter
Type@25ms `conda workspace ship -e runtime --platform "$DEMO_PLATFORM" \`
Enter
Type@25ms `  -o dist-embedded --artifact-layout embedded 2>embedded.log`
Enter
Hide
Sleep 500ms
Wait+Line@10m /^\$ *$/
Show
Sleep 2s

Type "clear"
Enter
Wait /\$/
Type "# First launch can install with offline mode enabled"
Enter
Type@25ms `WORKSPACE_PYTHON_OFFLINE=1 CONDA_SHIP_PREFIX="$PWD/.embedded" \`
Enter
Type@25ms `  ./dist-embedded/workspace-python --version 2>embedded-runtime.log`
Enter
Hide
Sleep 500ms
Wait+Line@10m /^\$ *$/
Show
Sleep 4s

Type "clear"
Enter
Wait /\$/
Type "# Or distribute package archives as a separate bundle"
Enter
Type@25ms `conda workspace ship -e runtime --platform "$DEMO_PLATFORM" \`
Enter
Type@25ms `  -o dist-external --artifact-layout external 2>external.log`
Enter
Hide
Sleep 500ms
Wait+Line@10m /^\$ *$/
Show
Sleep 2s
Type@25ms `mkdir packages && tar -xf dist-external/*.bundle.tar.zst -C packages`
Enter
Wait+Line@10m /^\$ *$/
Sleep 2s

Type "clear"
Enter
Wait /\$/
Type "# Point the launcher at the extracted bundle"
Enter
Type@25ms `WORKSPACE_PYTHON_BUNDLE="$PWD/packages" WORKSPACE_PYTHON_OFFLINE=1 \`
Enter
Type@25ms `  CONDA_SHIP_PREFIX="$PWD/.external" ./dist-external/workspace-python \`
Enter
Type@25ms `  --version 2>external-runtime.log`
Enter
Hide
Sleep 500ms
Wait+Line@10m /^\$ *$/
Show
Sleep 4s
