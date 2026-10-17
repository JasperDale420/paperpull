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

    python server/lock.py --base

The FROM line names its base by tag and digest, so every build starts from
the same files. This writes the digest the tag points at today, which
brings Debian's and Python's newest fixes for that base, and leaves the
tag as it is. Dependabot proposes the same change each week, and with
--check this only says whether the tag has moved on, as the Server image
workflow's weekly run does. The lock
names the tag, not the digest, since a new digest of one tag is the same
Python release on the same Debian release and resolves the same packages.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent
PIP_TOOLS = "pip-tools==7.6.2"


FROM = re.compile(r"^FROM\s+(\S+)", re.M)
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def base_image(dockerfile: Path = SERVER / "Dockerfile") -> str:
    """The FROM image with its digest, python:3.12-slim-bookworm@sha256:..."""
    found = FROM.search(dockerfile.read_text(encoding="utf-8"))
    if not found:
        raise SystemExit("server/Dockerfile has no FROM line.")
    return found.group(1)


def base_tag(image: str) -> str:
    """The image without its digest, python:3.12-slim-bookworm."""
    return image.split("@", 1)[0]


def header(image: str) -> str:
    return ("# Every package the server image installs, with the hashes of its files.\n"
            "# Written by server/lock.py from requirements.in, resolved in %s.\n"
            "# Do not edit by hand. Change requirements.in and run the script.\n" % base_tag(image))


def refresh_base(dockerfile: Path = SERVER / "Dockerfile", check: bool = False) -> int:
    """Write the digest the FROM tag points at today into the FROM line.
    With check, write nothing and answer 1 when the line names another."""
    image = base_image(dockerfile)
    tag = base_tag(image)
    if ":" not in tag.rsplit("/", 1)[-1]:
        # A digest alone names no tag to follow, and docker would read latest.
        print("server/Dockerfile's FROM %s names no tag, so there is none to follow." % image,
              file=sys.stderr)
        return 1
    try:
        done = subprocess.run(["docker", "buildx", "imagetools", "inspect", tag,
                               "--format", "{{.Manifest.Digest}}"],
                              capture_output=True, text=True, encoding="utf-8")
    except OSError as e:
        done = subprocess.CompletedProcess([], 1, "", "docker did not start, %s\n" % e)
    digest = done.stdout.strip()
    if done.returncode != 0 or not DIGEST.fullmatch(digest):
        sys.stderr.write(done.stderr[-2000:])
        print("No digest was found for %s, server/Dockerfile is unchanged." % tag, file=sys.stderr)
        return 1
    new = "%s@%s" % (tag, digest)
    if new == image:
        print("server/Dockerfile already names %s." % new)
        return 0
    if check:
        print("server/Dockerfile names %s, and %s is now %s.\n"
              "Run python server/lock.py --base to take it." % (image, tag, digest), file=sys.stderr)
        return 1
    text = dockerfile.read_bytes().decode("utf-8")      # its own line endings kept
    start, end = FROM.search(text).span(1)
    dockerfile.write_bytes((text[:start] + new + text[end:]).encode("utf-8"))
    print("server/Dockerfile now names %s.\n"
          "Build and check it with server/build.py and server/smoke.py, then commit it." % new)
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--base"]:
        return refresh_base()
    if argv == ["--base", "--check"]:
        return refresh_base(check=True)
    if argv:
        print("usage: python server/lock.py [--base [--check]]", file=sys.stderr)
        return 2
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
