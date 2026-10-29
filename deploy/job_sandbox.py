"""Per-test limits inside a fresh Docker container; never used by the web app."""
import math
import os
import selectors
import signal
import subprocess
import tempfile
import time

OUTPUT_LIMIT = 64 * 1024


def run(exe, stdin_data="", time_limit=None, workdir=None, argv=None):
    import resource
    import judge

    seconds = time_limit or 3
    started = time.monotonic()
    result = {"sandboxed": True, "backend": "docker-per-job", "error": "",
              "timed_out": False, "output_limited": False, "exit_code": None,
              "cpu_ms": 0, "peak_mem": 0, "note": "独立临时容器 / 无网络 / 无账号数据卷",
              "tle_kind": "", "limits": {"mem": 256 * 1024 * 1024, "wall_ms": seconds * 1000}}

    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(seconds), math.ceil(seconds) + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    buffers = [bytearray(), bytearray()]
    totals = [0, 0]
    # A file avoids blocking the supervisor while a program refuses to read stdin.
    with tempfile.TemporaryFile() as input_file:
        input_file.write(stdin_data.encode("utf-8"))
        input_file.seek(0)
        process = subprocess.Popen([exe] + list(argv or []), stdin=input_file,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=workdir,
            env=judge._clean_env(), start_new_session=True, preexec_fn=limits)
        try:
            with selectors.DefaultSelector() as selector:
                for index, stream in enumerate((process.stdout, process.stderr)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, index)
                while selector.get_map():
                    if time.monotonic() - started >= seconds:
                        result.update(timed_out=True, tle_kind="wall")
                        break
                    for key, _ in selector.select(.05):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        index = key.data
                        totals[index] += len(chunk)
                        buffers[index].extend(chunk[:max(0, OUTPUT_LIMIT - len(buffers[index]))])
                        if totals[index] > OUTPUT_LIMIT:
                            result["output_limited"] = True
                    if result["output_limited"]:
                        break
                if not result["timed_out"] and not result["output_limited"]:
                    try:
                        process.wait(timeout=max(.01, seconds - (time.monotonic() - started)))
                    except subprocess.TimeoutExpired:
                        result.update(timed_out=True, tle_kind="wall")
        finally:
            # Includes descendants even if the original process already exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
            process.stderr.close()
    code = process.returncode
    if code == -signal.SIGXCPU:
        result.update(timed_out=True, tle_kind="cpu")
    result.update(exit_code=256 - code if code < 0 else code,
                  stdout=buffers[0].decode("utf-8", "replace"),
                  stderr=buffers[1].decode("utf-8", "replace"),
                  out_bytes=totals[0], err_bytes=totals[1],
                  wall_ms=int((time.monotonic() - started) * 1000))
    return result
