# Build the STS2_MCP mod for macOS / Linux (counterpart of build.ps1).
#
# Usage:
#   ./build.sh [--game-dir "/path/to/Slay the Spire 2"] [--configuration Release]
#
# The game's managed assemblies live inside the app bundle on macOS:
#   <game>/SlayTheSpire2.app/Contents/Resources/data_sts2_macos_arm64
set -euo pipefail

game_dir="${STS2_GAME_DIR:-}"
configuration="Release"

while [ $# -gt 0 ]; do
  case "$1" in
    --game-dir) game_dir="$2"; shift 2 ;;
    --configuration) configuration="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$game_dir" ]; then
  echo "ERROR: pass --game-dir or set STS2_GAME_DIR" >&2
  exit 1
fi

uname_s="$(uname -s)"
if [ "$uname_s" = "Darwin" ]; then
  data_dir="$game_dir/SlayTheSpire2.app/Contents/Resources/data_sts2_macos_arm64"
else
  data_dir="$game_dir/data_sts2_linuxbsd_x86_64"
fi

if [ ! -f "$data_dir/sts2.dll" ]; then
  echo "ERROR: sts2.dll not found in $data_dir" >&2
  echo "Check that --game-dir points at the Slay the Spire 2 install root." >&2
  exit 1
fi

if ! command -v dotnet >/dev/null 2>&1; then
  echo "ERROR: dotnet not found. Install the .NET 9 SDK." >&2
  exit 1
fi

here="$(cd "$(dirname "$0")" && pwd)"
out="$here/out/STS2_MCP"

echo "=== Building STS2_MCP ($configuration) ==="
echo "Game directory : $game_dir"
echo "Assemblies     : $data_dir"
dotnet build "$here/STS2_MCP.csproj" -c "$configuration" -o "$out" \
  -p:STS2GameDir="$game_dir" -p:STS2GameDataDir="$data_dir"

cat <<EOF

Build succeeded. Install:
  1) copy  $out/STS2_MCP.dll        -> <game>/mods/STS2_MCP/
  2) copy  $here/mod_manifest.json  -> <game>/mods/STS2_MCP/manifest.json
On macOS <game> is the folder that contains SlayTheSpire2.app; if the loader
does not pick it up, also try <game>/SlayTheSpire2.app/Contents/MacOS/mods/.
EOF
