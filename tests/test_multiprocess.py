from .helper import mock_sys_stream, unittest
import signal
import sys
from praw import multiprocess
from time import time


class MultiProcessUnitTest(unittest.TestCase):
    @mock_sys_stream('stdout')
    def test_multiprocess_start(self):
        before = time()
        server, thread = multiprocess.run(return_objects=True)
        # We need to wait until the try/except block is entered
        # before raising a KeyboardInterrupt
        sigtime = int(time() - before) + 1
        server.shutdown()
        server.server_close()
        thread.join()
        sys.stdout.seek(0)
        data = sys.stdout.read()
        goto = sys.stdout.tell()
        self.assertIn('Listening on localhost port 10101\n', data)
        if not hasattr(signal, 'SIGALRM'):
            return  # Only some versions of Unix can run this test

        oldsignal = signal.signal(signal.SIGALRM,
                                  signal.getsignal(signal.SIGINT))
        signal.alarm(sigtime)  # asynchronously raise a KeyboardInterrupt
        multiprocess.run()
        signal.signal(signal.SIGALRM, oldsignal)  # reset the signal handler
        sys.stdout.seek(goto)
        data = sys.stdout.read()
        self.assertIn('Listening on localhost port 10101\n', data)
        self.assertIn('CTRL-C to shutdown server.\n', data)
        self.assertIn('Goodbye!\n', data)
