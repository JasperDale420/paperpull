"""Write server/requirements.txt, every package the server image installs,
each at one version with the hashes of its files, from the direct pins in
server/requirements.in.

    python server/lock.py

It resolves inside the image's own base, the FROM line of server/Dockerfile,
so the packages chosen are the ones that Python on Linux needs, whatever
computer runs it, and only versions with built packages, as the image
installs them. Nothing is mounted, requirements.in goes in on standard
input and the lock comes back on standard output.

The Dockerfile installs with --require-hashes, so pip refuses a file whose
hash is not written here and a package not written here at all. Raise a
version in requirements.in on purpose, with a test run, then run this and
commit both files.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent
PIP_TOOLS = "pip-tools==7.6.2"


def base_image(dockerfile: Path = SERVER / "Dockerfile") -> str:
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        found = re.match(r"FROM\s+(\S+)", line)
        if found:
            return found.group(1)
    raise SystemExit("server/Dockerfile has no FROM line.")


def header(image: str) -> str:
    return ("# Every package the server image installs, with the hashes of its files.\n"
            "# Written by server/lock.py from requirements.in, resolved in %s.\n"
            "# Do not edit by hand. Change requirements.in and run the script.\n" % image)


def main() -> int:
    image = base_image()
    script = ("cd /tmp && cat > requirements.in"
              " && pip install --quiet --disable-pip-version-check --root-user-action=ignore %s"
              " && CUSTOM_COMPILE_COMMAND='python server/lock.py'"
              " pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url"
              " --pip-args '--only-binary :all:' --output-file - requirements.in" % PIP_TOOLS)
    given = (SERVER / "requirements.in").read_text(encoding="utf-8")
    done = subprocess.run(["docker", "run", "--rm", "-i", image, "sh", "-c", script],
                          input=given, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0 or "--hash=sha256:" not in done.stdout:
        sys.stderr.write(done.stderr[-4000:])
        print("The lock was not written.", file=sys.stderr)
        return 1
    lock = header(image) + done.stdout.replace("\r\n", "\n")
    (SERVER / "requirements.txt").write_text(lock, encoding="utf-8", newline="\n")
    pins = len(re.findall(r"^[A-Za-z0-9_.-]+==", lock, flags=re.M))
    print("server/requirements.txt holds %d packages, resolved in %s." % (pins, image))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
