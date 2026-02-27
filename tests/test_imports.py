# [2026-02-19] Chore: translate comments to English.
import sys, os, inspect, pathlib, importlib, time
print("python:", sys.version)
print("dont_write_bytecode:", sys.dont_write_bytecode)
print("pycache_prefix:", getattr(sys, "pycache_prefix", None))
print("prefix:", sys.prefix)
print("exec_prefix:", sys.exec_prefix)
print("executable:", sys.executable)

t0 = time.perf_counter()
import requests
dt = (time.perf_counter()-t0)*1000
print(f"import requests: {dt:.1f} ms")
req_file = inspect.getfile(requests)
pkg_dir = pathlib.Path(req_file).parent
pyc_dir = pkg_dir / "__pycache__"
print("requests.__file__:", req_file)
print("__pycache__ exists:", pyc_dir.exists(), "writable:", os.access(pkg_dir, os.W_OK))

# are .pyc files being created?
for p in sorted(pyc_dir.glob("requests*.pyc")) if pyc_dir.exists() else []:
    print("pyc:", p)
