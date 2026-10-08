import subprocess
import sys
import threading
import time
import unittest

from process_io import iter_text_chunks


class ProcessIoTests(unittest.TestCase):
    def test_yields_flushed_prompt_before_process_exit(self):
        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-c",
                "import sys,time;sys.stdout.write('> ');sys.stdout.flush();time.sleep(.5)",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        first_chunk = threading.Event()
        chunks = []

        def read_output():
            for chunk in iter_text_chunks(process.stdout):
                chunks.append(chunk)
                first_chunk.set()

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        self.assertTrue(first_chunk.wait(0.4), "flushed output remained buffered")
        process.wait(timeout=2)
        reader.join(timeout=1)
        process.stdout.close()
        self.assertEqual("".join(chunks), "> ")


if __name__ == "__main__":
    unittest.main()
