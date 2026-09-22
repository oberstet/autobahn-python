#!/usr/bin/env python3
# Copyright (c) typedef int GmbH, Germany, 2025. All rights reserved.
#
# Smoke tests for autobahn package verification.
# Used by CI to verify wheels and sdists actually work after building.

"""
Smoke tests for autobahn package.

This script verifies that an autobahn installation is functional by testing:
1. Import autobahn and check version
2. Import autobahn.websocket (WebSocket protocol)
3. Import autobahn.wamp (WAMP protocol)
4. Import autobahn.flatbuffers and check version (shared test)
5. Verify flatc binary is available and executable (shared test)
6. Verify reflection files are present (shared test)
7. Verify NVX acceleration matches expectation (opt-in via AUTOBAHN_EXPECT_NVX)
8. Exercise the XOR masker and UTF-8 validator the install actually ships

ALL TESTS ARE REQUIRED. Both wheel installs and sdist installs MUST
provide identical functionality including the flatc binary and
reflection.bfbs file.

Set AUTOBAHN_EXPECT_NVX=1/0 to assert the native accelerator is (not) active;
when unset, test 7 reports the state but does not fail.

Note: FlatBuffers/flatc tests use shared functions from smoke_test_flatc.py
      (source: wamp-cicd/scripts/flatc/smoke_test_flatc.py)
"""

import os
import platform
import sys
import sysconfig

# Import shared FlatBuffers test functions
from smoke_test_flatc import (
    test_import_flatbuffers,
    test_flatc_binary,
    test_reflection_files,
)

# Package name for shared tests
PACKAGE_NAME = "autobahn"


def test_import_autobahn():
    """Test 1: Import autobahn and check version."""
    print("Test 1: Importing autobahn and checking version...")
    try:
        import autobahn
        print(f"  autobahn version: {autobahn.__version__}")
        print("  PASS")
        return True
    except Exception as e:
        print(f"  FAIL: Could not import autobahn: {e}")
        return False


def test_import_websocket():
    """Test 2: Import autobahn.websocket (WebSocket protocol)."""
    print("Test 2: Importing autobahn.websocket...")
    try:
        from autobahn.websocket import protocol
        print("  WebSocket protocol module loaded")
        print("  PASS")
        return True
    except Exception as e:
        print(f"  FAIL: Could not import autobahn.websocket: {e}")
        return False


def test_import_wamp():
    """Test 3: Import autobahn.wamp (WAMP protocol)."""
    print("Test 3: Importing autobahn.wamp...")
    try:
        from autobahn.wamp import types
        print("  WAMP types module loaded")
        print("  PASS")
        return True
    except Exception as e:
        print(f"  FAIL: Could not import autobahn.wamp: {e}")
        return False


def test_nvx_state():
    """Test 7: NVX acceleration matches AUTOBAHN_EXPECT_NVX, if set."""
    print("Test 7: Checking NVX native acceleration state...")
    try:
        from autobahn.websocket import HAS_NVX, USES_NVX

        print(f"  HAS_NVX: {HAS_NVX}  USES_NVX: {USES_NVX}")

        expect = os.environ.get("AUTOBAHN_EXPECT_NVX")
        if expect is None:
            print("  AUTOBAHN_EXPECT_NVX not set - reporting only")
            print("  PASS")
            return True

        if expect == "1":
            if not HAS_NVX:
                print("  FAIL: NVX expected, but the extension is missing or failed to load")
                return False
            if not USES_NVX:
                print("  FAIL: NVX is present but not active")
                return False
            import _nvx_xormasker

            print(f"  NVX module: {_nvx_xormasker.__file__}")
        elif USES_NVX:
            print("  FAIL: NVX is active, but this install is expected to be NVX-free")
            return False

        print("  PASS")
        return True
    except Exception as e:
        print(f"  FAIL: NVX check failed: {e}")
        return False


def test_websocket_primitives():
    """Test 8: Exercise the XOR masker and UTF-8 validator that are shipped.

    Runs whichever implementation is active, so a compiled-but-broken NVX binary
    fails here rather than silently producing wrong frames at run-time.
    """
    print("Test 8: Exercising WebSocket primitives...")
    try:
        from autobahn.websocket.xormasker import create_xor_masker
        from autobahn.websocket.utf8validator import Utf8Validator

        payload = "autobahn smoke test payload äöü".encode("utf-8") * 8
        mask = b"\x37\xfa\x21\x3d"

        masked = create_xor_masker(mask, len(payload)).process(payload)
        if masked == payload:
            print("  FAIL: masking was a no-op")
            return False
        if create_xor_masker(mask, len(payload)).process(masked) != payload:
            print("  FAIL: XOR mask round-trip did not reproduce the payload")
            return False
        print(f"  XOR masker: round-trip OK on {len(payload)} bytes")

        validator = Utf8Validator()
        validator.reset()
        valid, ends_on_codepoint, _, _ = validator.validate(payload)
        if not (valid and ends_on_codepoint):
            print("  FAIL: valid UTF-8 was rejected")
            return False
        validator.reset()
        if validator.validate(b"\xf8\xa1\xa1\xa1\xa1")[0]:
            print("  FAIL: invalid UTF-8 was accepted")
            return False
        print("  UTF-8 validator: accept/reject OK")

        print("  PASS")
        return True
    except Exception as e:
        print(f"  FAIL: WebSocket primitives failed: {e}")
        return False


def main():
    """Run all smoke tests."""
    print("=" * 72)
    print("  SMOKE TESTS - Verifying autobahn installation")
    print("=" * 72)
    print()
    print(f"Python:    {sys.version}")
    print(f"Platform:  {sysconfig.get_platform()}  (machine: {platform.machine()})")
    print()

    # All tests are REQUIRED - sdist MUST provide same functionality as wheels
    # Tests 4-6 use shared functions from smoke_test_flatc.py
    tests = [
        ("Test 1", test_import_autobahn),
        ("Test 2", test_import_websocket),
        ("Test 3", test_import_wamp),
        ("Test 4", lambda: test_import_flatbuffers(PACKAGE_NAME)),
        ("Test 5", lambda: test_flatc_binary(PACKAGE_NAME)),
        ("Test 6", lambda: test_reflection_files(PACKAGE_NAME)),
        ("Test 7", test_nvx_state),
        ("Test 8", test_websocket_primitives),
    ]

    failures = 0
    passed = 0

    for name, test in tests:
        result = test()
        if result is True:
            passed += 1
        else:
            failures += 1
        print()

    total = len(tests)
    print("=" * 72)
    if failures == 0:
        print(f"ALL SMOKE TESTS PASSED ({passed}/{total})")
        print("=" * 72)
        return 0
    else:
        print(f"SMOKE TESTS FAILED ({passed} passed, {failures} failed)")
        print("=" * 72)
        return 1


if __name__ == "__main__":
    sys.exit(main())
