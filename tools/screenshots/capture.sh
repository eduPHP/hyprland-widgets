#!/usr/bin/env bash
# capture.sh NAMESPACE OUT.png — grab one hypr-ui popover (its layer surface) at native scale.
# The surface includes the transparent shadow margin; compose.py trims it.
set -euo pipefail
ns=$1 out=$2
geo=$(hyprctl layers -j | python3 -c "
import json, sys
ns = sys.argv[1]
for mon in json.load(sys.stdin).values():
    for layer in mon['levels'].values():
        for l in layer:
            if l['namespace'] == ns:
                print(f\"{l['x']},{l['y']} {l['w']}x{l['h']}\"); sys.exit()
sys.exit('no layer ' + ns)" "$ns")
grim -g "$geo" "$out"
echo "$ns $geo -> $out"
