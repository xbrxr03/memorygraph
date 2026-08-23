from __future__ import annotations

import os
import subprocess
import sys


def test_worker_can_be_imported_before_application_package() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = "src"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import memorygraph.worker; "
                "import memorygraph.application; "
                "from memorygraph.application import DurableWorkerService"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
