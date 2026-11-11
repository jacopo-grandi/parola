#!/bin/sh
# Build dist/PAROLA.app (decision 0028). Step 1 of phase 8: a development build that runs the
# engine of this repo (.venv/bin/parola) on the calls in data/private/calls. The self-contained
# build, with Python and its packages inside the app, is step 3.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
app="$repo/dist/PAROLA.app"
version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$repo/pyproject.toml" | head -1)
# Visible versioning (decision 0045): the build carries the commit it was compiled from.
commit=$(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo unknown)
builddate=$(date +%Y-%m-%d)

swift build -c release --package-path "$here"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$here/.build/release/PAROLA" "$app/Contents/MacOS/PAROLA"

# The icon (app/icon/icon.png, drawn by app/icon/make_icon.swift) at every size macOS asks for.
iconset=$(mktemp -d)/AppIcon.iconset
mkdir -p "$iconset"
for px in 16 32 128 256 512; do
    sips -z $px $px "$here/icon/icon.png" --out "$iconset/icon_${px}x${px}.png" >/dev/null
    sips -z $((px * 2)) $((px * 2)) "$here/icon/icon.png" --out "$iconset/icon_${px}x${px}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$app/Contents/Resources/AppIcon.icns"
rm -rf "$(dirname "$iconset")"

# The name Finder, the Dock and Spotlight show: "P.A.R.O.L.A.", while the bundle stays PAROLA.app
# (paths, scripts and the docs keep a plain name). Finder reads it only from a localization, and
# only while the plain CFBundleDisplayName matches the file name (else it shows the file name).
for lang in en it; do
    mkdir -p "$app/Contents/Resources/$lang.lproj"
    printf '"CFBundleDisplayName" = "P.A.R.O.L.A.";\n"CFBundleName" = "P.A.R.O.L.A.";\n' \
        > "$app/Contents/Resources/$lang.lproj/InfoPlist.strings"
done

cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>PAROLA</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>dev.parola.app</string>
  <key>CFBundleName</key><string>PAROLA</string>
  <key>CFBundleDisplayName</key><string>PAROLA</string>
  <key>LSHasLocalizedDisplayName</key><true/>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>LSMinimumSystemVersion</key><string>14.2</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>P.A.R.O.L.A. registra il tuo microfono per trascrivere in locale la tua parte della call.</string>
  <key>NSAudioCaptureUsageDescription</key><string>P.A.R.O.L.A. registra l'audio della call per trascrivere in locale gli altri partecipanti.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>PAROLAEngine</key><string>$repo/.venv/bin/parola</string>
  <key>PAROLADataDir</key><string>$repo/data/private/calls</string>
  <key>PAROLABuildCommit</key><string>$commit</string>
  <key>PAROLABuildDate</key><string>$builddate</string>
</dict>
</plist>
PLIST

# Apple Silicon runs only signed code: an ad-hoc signature, until there is a Developer ID.
codesign --force --sign - "$app"
echo "built $app"
