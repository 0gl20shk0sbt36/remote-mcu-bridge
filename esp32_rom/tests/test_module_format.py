"""Native module-format and NOR footer power-cut tests (no hardware).

Run: python3 esp32_rom/tests/test_module_format.py -v
The power-cut model is deliberately independent of the producer: NOR writes
clear bits, and *every* byte of the final 4096-byte page must be zero to commit.
"""

from pathlib import Path
import importlib.util
import sys
import re
import unittest


def at(text, literal):
    """Locate a code anchor, tolerating formatter whitespace around tokens."""
    pattern = re.escape(literal)
    for token, flexible in (
        (r"\ ", r"\s*"),
        (",", r"\s*,\s*"),
        (r"\(", r"\(\s*"),
        (r"\)", r"\s*\)"),
        (r"\{", r"\{\s*"),
        (r"\}", r"\s*\}"),
        (";", r"\s*;"),
        ("=", r"\s*=\s*"),
    ):
        pattern = pattern.replace(token, flexible)
    match = re.search(pattern, text)
    if not match:
        raise ValueError("missing anchor: " + literal)
    return match.start()


def flat(text):
    """Compare C token runs without depending on formatting whitespace."""
    return re.sub(r"\s+", "", text)


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "module" / "build_module.py"
ABI_PATH = ROOT / "main" / "module_abi.h"


def load_builder():
    """Import the real producer without requiring a package or invoking its CLI."""
    spec = importlib.util.spec_from_file_location("module_format_builder", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module producer: {BUILDER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FOOTER_SIZE = 4096
ERASED = b"\xff" * FOOTER_SIZE
COMMITTED = b"\x00" * FOOTER_SIZE


def footer_committed(footer):
    """Reference boot-admission rule, not a hardware implementation."""
    return len(footer) == FOOTER_SIZE and footer == COMMITTED


class FooterPowerCutTests(unittest.TestCase):
    def test_exact_page_states_and_lengths(self):
        self.assertFalse(footer_committed(ERASED))
        self.assertTrue(footer_committed(COMMITTED))
        for length in (0, 1, 4095, 4097, 8192):
            self.assertFalse(footer_committed(bytes(length)))

    def test_every_byte_commit_cut(self):
        # All 4097 cuts, including before and after the final byte.
        for cut in range(FOOTER_SIZE + 1):
            footer = COMMITTED[:cut] + ERASED[cut:]
            self.assertEqual(
                footer_committed(footer), cut == FOOTER_SIZE, f"commit cut after byte {cut}"
            )

    def test_every_bit_commit_cut(self):
        # Every interruption in a byte-by-byte, low-bit-first NOR program.
        for cleared in range(FOOTER_SIZE * 8 + 1):
            whole, bits = divmod(cleared, 8)
            footer = bytearray(ERASED)
            footer[:whole] = COMMITTED[:whole]
            if bits:
                footer[whole] = 0xFF & ~((1 << bits) - 1)
            self.assertEqual(
                footer_committed(footer),
                cleared == FOOTER_SIZE * 8,
                f"commit cut after bit {cleared}",
            )

    def test_every_position_and_nonzero_value_rejected(self):
        # Exhaust all 4096 * 255 one-byte deviations, not just 0xff tails.
        footer = bytearray(COMMITTED)
        for offset in range(FOOTER_SIZE):
            for value in range(1, 256):
                footer[offset] = value
                if footer_committed(footer):
                    self.fail(f"accepted nonzero byte {value:#x} at {offset}")
            footer[offset] = 0

    def test_update_body_is_never_visible_before_commit(self):
        # A realistic multi-chunk body: invalidate first, write body, then
        # program footer. Check every byte interruption in the body write.
        # The body is test data, not a purported compiled firmware artifact.
        body = bytes(range(256)) * 33
        flash = bytearray(b"\xff" * (len(body) + FOOTER_SIZE))
        for offset, value in enumerate(body):
            flash[offset] &= value  # NOR cannot set a bit without an erase.
            self.assertFalse(
                footer_committed(flash[-FOOTER_SIZE:]), f"body cut after byte {offset + 1}"
            )
        self.assertEqual(flash[:-FOOTER_SIZE], body)
        flash[-FOOTER_SIZE:] = COMMITTED
        self.assertTrue(footer_committed(flash[-FOOTER_SIZE:]))

    def test_every_bit_erase_cut(self):
        # A torn sector erase must never accidentally recommit a footer.
        for erased in range(FOOTER_SIZE * 8 + 1):
            whole, bits = divmod(erased, 8)
            footer = bytearray(COMMITTED)
            footer[:whole] = ERASED[:whole]
            if bits:
                footer[whole] = (1 << bits) - 1
            self.assertEqual(
                footer_committed(footer), erased == 0, f"invalidation cut after bit {erased}"
            )

    def test_every_byte_erase_cut(self):
        # During invalidation, only the entirely untouched old footer is valid.
        for cut in range(FOOTER_SIZE + 1):
            footer = ERASED[:cut] + COMMITTED[cut:]
            self.assertEqual(
                footer_committed(footer), cut == 0, f"invalidation cut after byte {cut}"
            )


@unittest.skipUnless(
    BUILDER_PATH.is_file() and ABI_PATH.is_file(), "module producer/ABI not yet present"
)
class ModuleFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = load_builder()
        cls.abi = ABI_PATH.read_text()

    def test_producer_imports_without_running_cli(self):
        self.assertEqual(self.builder.__file__, str(BUILDER_PATH))
        self.assertTrue(self.abi.strip(), "ABI header is empty")

    def fixture(self, **changes):
        # Format-only fixture, not a compiled/hardware module or predicted VA.
        fields = dict(
            magic=0x314D5242,
            abi=4,
            hs=40,
            total=48,
            text=0x42200028,
            ts=4,
            data=0x3FC90000,
            ds=4,
            bs=4,
            entry=0x42200028,
        )
        fields.update(changes)
        return self.builder.HEADER.pack(*fields.values()) + bytes(8)

    def test_xip_abi4_layout(self):
        self.assertEqual(self.builder.validate_image(self.fixture())[1], 4)
        for changes in (
            dict(abi=1),
            dict(abi=3),
            dict(text=0x40380028),
            dict(text=0x42200000),
            dict(entry=0x4220002C),
            dict(bs=32768),
            dict(ts=0xFFFFFFFF),
            dict(total=47),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.builder.validate_image(self.fixture(**changes))

    def test_runtime_uses_managed_xip_and_defers_entry(self):
        source = (ROOT / "main/module_runtime.c").read_text()
        # The executable mapping moved behind rmb/port.h: the resident must ask
        # the port for the slot mapping, while the platform owns the IDF mmap
        # flags and the matching munmap. Assert both halves, not just the move.
        platform = "".join(p.read_text() for p in sorted((ROOT / "main/platform").glob("*.c")))
        self.assertIn(flat("rmb_port_slot_map(&text)"), flat(source))
        self.assertIn(flat("ESP_PARTITION_MMAP_INST"), flat(platform))
        self.assertIn(flat("esp_partition_munmap(mapped_handle)"), flat(platform))
        self.assertNotIn(flat("ESP_PARTITION_MMAP_INST"), flat(source))
        self.assertNotIn(flat("module_text_start"), flat(source))
        self.assertNotIn(flat("24576"), flat(source))
        finish = source.split("int module_runtime_install_finish(", 1)[1].split(
            "int module_rom_validate(", 1
        )[0]
        self.assertNotIn(flat("load(p)"), flat(finish))
        self.assertNotIn(flat("module_entry_fn"), flat(finish))
        # Admission/commit moved, not removed: inspect production core and its
        # actual adapter callbacks, while preserving all former assertions.
        core = (ROOT.parent / "core/update.c").read_text()
        self.assertIn(flat("bridge_update_finish(&update,digest)"), flat(finish))
        self.assertIn(flat("bridge_footer_verify(u,255)"), flat(core))
        self.assertIn(flat("bridge_footer_verify(u,0)"), flat(core))
        # Hashing moved with storage behind the port: the resident still owns
        # the digest contract (slot + base binding), the platform runs SHA-256.
        self.assertIn(flat("rmb_port_slot_sha256((uint32_t)o,(uint32_t)n,digest)"), flat(source))
        self.assertIn(flat("rmb_port_base_sha256(base)"), flat(source))
        self.assertIn(flat("mbedtls_sha256_finish"), flat(platform))
        self.assertIn(flat("u->ops->check_image(u->ctx,u->expected)"), flat(core))
        self.assertIn(flat("u->ops->sha256(u->ctx,0,u->expected,actual)"), flat(core))
        self.assertLess(at(core, "memcmp(actual,digest,32)"), at(core, "/* Admission complete"))
        self.assertIn(flat("storage_hash,storage_check,storage_sync"), flat(source))

    def test_builder_requires_observed_address(self):
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                str(BUILDER_PATH),
                "--base-elf",
                "unused.elf",
                "--output",
                "unused.bin",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--text-address", result.stderr)


if __name__ == "__main__":
    unittest.main()
