import math, os, time, traceback
hb = os.environ.get("PNPINK_CPU_BURN_HEARTBEAT", "cpu_burn.hb")
try:
    with open(hb, "a", encoding="utf-8") as f:
        f.write("start\n")
        f.flush()
    x = 0.123456789
    n = 0
    while True:
        for _ in range(5000000):
            x = math.sin(x) * math.cos(x) + 1.000001
        n += 1
        if (n % 2) == 0:
            with open(hb, "a", encoding="utf-8") as f:
                f.write(f"beat {n}\n")
                f.flush()
except Exception:
    with open(hb + ".err", "a", encoding="utf-8") as f:
        traceback.print_exc(file=f)
    time.sleep(600)
