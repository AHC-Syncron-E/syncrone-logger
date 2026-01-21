import sys


def patch_exe(exe_path):
    print(f"Patching {exe_path} to remove Admin requirement...")

    try:
        with open(exe_path, "rb") as f:
            content = f.read()

        # The strings to look for (Standard 7-Zip SFX Manifest patterns)
        # We handle both single and double quotes just in case.
        patterns = [
            b'level="requireAdministrator"',
            b"level='requireAdministrator'"
        ]

        found = False
        new_content = content

        for pattern in patterns:
            if pattern in content:
                print(f"Found Admin manifest: {pattern.decode()}")

                # Replacement: "asInvoker" is shorter than "requireAdministrator".
                # We MUST pad with spaces to keep the file size exactly the same.
                # requireAdministrator = 20 chars
                # asInvoker           = 9 chars
                # Padding             = 11 spaces
                replacement = b'level="asInvoker"           '

                # Verify lengths match (Critical for binary patching)
                if len(replacement) != len(pattern):
                    # Adjust if pattern was different quotes, though len is usually same
                    diff = len(pattern) - len(b'level="asInvoker"')
                    replacement = b'level="asInvoker"' + (b' ' * diff)

                new_content = new_content.replace(pattern, replacement)
                found = True

        if found:
            with open(exe_path, "wb") as f:
                f.write(new_content)
            print("SUCCESS: Patched manifest to 'asInvoker'.")
        else:
            print("WARNING: Could not find 'requireAdministrator' in the file.")
            print("It might already be patched, compressed, or using a different manifest.")

    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    patch_exe("Syncron-E_Logger.exe")