## What changed

<!-- One paragraph. Link the issue if there is one. -->

## Why

<!-- The problem this solves. Keep it about behaviour, not implementation trivia. -->

## Verification

<!-- Paste real commands and results, not intentions. -->

- [ ] `python3 tools/format_code.py --check`
- [ ] `IDF_PATH=... ctest --preset native` (all pass)
- [ ] If the resident changed: `python3 esp32_rom/tests/test_port_boundary.py`
- [ ] If the resident changed: `safe` and `compile-enabled` builds
- [ ] If a service changed: rebuilt against the exact base ELF and module-linked
- [ ] No credentials, private firmware, build output or dated evidence dumps added

## Notes for reviewers

<!-- Risk areas, follow-ups, anything deliberately left out. -->
