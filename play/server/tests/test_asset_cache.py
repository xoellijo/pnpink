import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from boardzilla_lite.app import asset_response


class AssetCacheTests(unittest.TestCase):
    def test_revalidates_cached_asset_and_detects_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "card.png"
            target.write_bytes(b"first")

            response = asset_response(SimpleNamespace(headers={}), target, headers={"Cache-Control": "private, no-cache"})
            etag = response.headers["ETag"]

            cached = asset_response(SimpleNamespace(headers={"If-None-Match": etag}), target)
            self.assertEqual(cached.status, 304)

            previous_mtime = target.stat().st_mtime_ns
            target.write_bytes(b"newer")
            os.utime(target, ns=(previous_mtime + 1_000_000_000, previous_mtime + 1_000_000_000))

            updated = asset_response(SimpleNamespace(headers={"If-None-Match": etag}), target)
            self.assertEqual(updated.status, 200)
            self.assertNotEqual(updated.headers["ETag"], etag)


if __name__ == "__main__":
    unittest.main()
