"""Keep the computer from going to sleep while a pull or build runs.

A sleep in the middle of a pull stops it (and once left damaged cache files behind), so the long
commands run inside keep_awake(). The screen may still turn off; only system sleep is blocked.
Normal sleep settings come back as soon as the command ends, however it ends.
"""

import os
import subprocess
import sys
from contextlib import contextmanager


@contextmanager
def keep_awake():
    if sys.platform == "win32":
        import ctypes
        es_continuous, es_system_required = 0x80000000, 0x00000001
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadExecutionState(es_continuous | es_system_required)
        try:
            yield
        finally:
            kernel32.SetThreadExecutionState(es_continuous)
    elif sys.platform == "darwin":
        # caffeinate -i: no idle sleep; -w: until this process exits (even if it's killed)
        proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        try:
            yield
        finally:
            proc.terminate()
    else:
        yield
