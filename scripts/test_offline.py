"""Execute the complete suite with outbound socket connections denied."""
from pathlib import Path
import os
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    os.chdir(ROOT)
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline test: connection denied')), \
         patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('Offline test: connection denied')), \
         patch.object(socket, 'getaddrinfo', side_effect=AssertionError('Offline test: DNS denied')):
        suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
