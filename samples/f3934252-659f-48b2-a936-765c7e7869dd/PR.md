# fix: bug: unix_addr check is useless (fixes #1348)

## Problem

The unix_addr check is useless because it only checks if the address can be resolved, not if the socket actually exists. The current implementation uses net.ResolveUnixAddr which will never throw an error when the network is 'unix'.

## Solution

Updates the isUnixAddrResolvable function in baked_in.go to check if the file exists and if it's a socket using os.Stat and checking the file mode.

## Test plan

- [ ] go test ./... -count=1
- [ ] go test -run TestIsUnixAddrResolvable ./baked_in.go -count=1

---

Fixes #1348
