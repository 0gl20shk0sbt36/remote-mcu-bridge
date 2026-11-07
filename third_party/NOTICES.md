# Third-party notices and redistribution boundaries

## ESP-IDF v5.5.5 and cJSON

Espressif ESP-IDF core is under Apache-2.0. Its bundled libraries, vendor binary
archives and transitive components are **not all under that license**. Preserve
their original component licenses/notices when SDK source or linked binaries are
redistributed. `licenses/ESP-IDF-Apache-2.0.txt` is copied from the inspected SDK.

The directly required IDF `json` component uses cJSON 1.7.19, pinned by the SDK at
`b2890c8d76bbb64e710585ebc0a917196b9c67e7`. Copyright (c) 2009-2017 Dave Gamble and
cJSON contributors. Its MIT license is copied verbatim in `licenses/cJSON-MIT.txt`;
retain its copyright and permission notice in copies/substantial portions.

## STM32CubeF1, HAL and CMSIS

The Cube package is a mixed-license distribution, not one permissively licensed
unit. `licenses/STM32CubeF1-component-license-table.md` preserves the local package
license summary; the component's own actual license text controls. This bootstrap
selects only the Cube CMSIS core plus STM32F1 HAL and CMSIS-device source trees,
not middleware/BSPs. A Git checkout or archive of the Cube superproject can still
contain unrelated upstream material: it is fetched for builds, not redistributed
as project-authored source.

* STM32F1 HAL: copyright 2016 STMicroelectronics, BSD-3-Clause; full text in
  `licenses/STM32F1-HAL-BSD-3-Clause.txt`.
* CMSIS core: Arm Limited and contributors, Apache-2.0; full local text in
  `licenses/CMSIS-Apache-2.0.txt`. Keep per-file Arm copyright/SPDX notices.
* STM32F1 CMSIS device: Arm Limited / STMicroelectronics, Apache-2.0; full local
  text in `licenses/STM32F1-device-Apache-2.0.txt`. Keep per-file notices.

BSD source distributions must retain the copyright, conditions and disclaimer;
binary distributions must reproduce them in documentation or accompanying
materials. Do not imply endorsement by ST or contributors. Apache-2.0 requires
a copy of the license, retained applicable attribution notices, prominent notices
of modifications, and preservation of applicable upstream NOTICE text when present.

The three `CubeMX-F103-*-license-reference.txt` files preserve the separate
license references found in the generated application's local `Drivers/` tree.
They are conditional package-license references, **not replacements for the full
license texts or evidence of a missing Package_license file**. The HAL fallback
refers to BSD-3-Clause and CMSIS/device fallbacks to Apache-2.0.

## STM32CubeMX-generated and ST-derived application files

Preserve **every existing copyright, `@attention`, SPDX/license and AS-IS header**
in `app_cubemx_f103/Core/Inc`, `Core/Src`, `startup_stm32f103xb.s`, linker scripts,
and any retained `Drivers` headers. Do not remove them during sanitization or
replace them with a project-wide copyright/SPDX claim. Modifications do not erase
the original notices.

Inspected `Core/Src/main.c` carries ST's 2026 copyright and says its license is
in the component-root `LICENSE`, otherwise it is provided AS-IS. The retained
`system_stm32f1xx.c` carries ST's 2017-2021 notice with the same reference. Other
generated/system/startup files need per-file review. **AS-IS is a disclaimer, not
an independently sufficient redistribution grant.** Neither a repository-wide
license for original project code nor an unrelated HAL license should be assumed
to supply the missing generated-code license automatically.

Publication integration must identify and retain the applicable original CubeMX
component/package license at the location referenced by those headers, or obtain
an explicit grant/re-generate from a package with clear redistributable terms.
Do not silently invent one. This task intentionally did not edit generated source
or place a blanket `LICENSE` in its component root. Treat unresolved generated-code
license references as a release-review blocker, not as resolved by this inventory.

## Inventory provenance

`licenses/index.json` records original repository/SDK-relative sources and SHA-256
of the byte-identical copied texts, including original line endings. No personal
filesystem paths are included. These records describe the inspected source set;
they are not legal advice or a complete inventory of all SDK transitive binaries.
