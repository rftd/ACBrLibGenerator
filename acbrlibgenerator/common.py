"""Process execution and atomic manifests. Commands never pass through a shell."""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class Runner:
    def __init__(self, log: Path):
        log.parent.mkdir(parents=True, exist_ok=True)
        self.log = log

    def say(self, text):
        print(text, flush=True)
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(text + "\n")

    def run(self, args, cwd=None):
        args = [str(a) for a in args]
        self.say("$ " + json.dumps(args, ensure_ascii=False))
        options = {"start_new_session": True} if os.name != "nt" else {}
        process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **options)
        try:
            for line in process.stdout:
                self.say(line.rstrip("\r\n"))
            code = process.wait()
        except BaseException:
            if process.poll() is None:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            raise
        finally:
            process.stdout.close()
        if code:
            raise subprocess.CalledProcessError(code, args)


def primary_config(environment: Path) -> Path:
    own = environment / "config_lazarus"
    if own.is_dir():
        return own
    if os.name == "nt":
        return Path(os.environ["APPDATA"]) / "lazarus"
    return Path.home() / ".lazarus"


def lazbuild(environment: Path) -> Path:
    name = "lazbuild.exe" if os.name == "nt" else "lazbuild"
    return environment / name if (environment / name).is_file() else environment / "lazarus" / name
