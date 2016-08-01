from mock import patch
from praw import Reddit, multiprocess
from praw.handlers import MultiprocessHandler
import signal
from six import assertRaisesRegex
from six.moves.cPickle import UnpicklingError
import socket
import sys
from sys import exc_info as exc__info
from time import time
from .helper import (betamax_multiprocess_custom_header, mock_sys_stream,
                     NewOAuthPRAWTest, unittest, USER_AGENT)


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

    def test_multiprocess_server_error_handler(self):
        # this could be tested as a static method, but it would normally
        # only be used as a bound method by the server internally
        server = multiprocess.PRAWMultiprocessServer(
            ('localhost', 10101), multiprocess.RequestHandler)

        def getexcinfo(num):
            try:
                raise {1: socket.error if sys.version_info < (3, 3)
                       else BrokenPipeError,  # NOQA
                       2: UnpicklingError,
                       3: Exception}[num](32)
            except:
                return exc__info()

        with patch.object(sys, 'exc_info', side_effect=[
            getexcinfo(1),
            getexcinfo(2),
            getexcinfo(3),
        ]):
            server.handle_error('', ('127.0.0.1', 10102))  # pass
            with mock_sys_stream('stderr'):
                server.handle_error('', ('127.0.0.1', 10102))
                sys.stderr.seek(0)
                self.assertEqual('Invalid connection from 127.0.0.1\n',
                                 sys.stderr.read())
            self.assertRaises(Exception, server.handle_error, '',
                              ('127.0.0.1', 10102))

        server.server_close()


class MultiProcessIntegrationTest(NewOAuthPRAWTest):
    def setUp(self):
        self.configure()
        self.server, self.server_thread = multiprocess.run(return_objects=True)
        self.r = Reddit(USER_AGENT, handler=MultiprocessHandler())

    def tearDown(self):
        # clean up the server
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join()

    @betamax_multiprocess_custom_header()
    def test_multiprocess_cache_hit_callback(self):
        self.r.refresh_access_information(self.refresh_token['new_read'])
        with self.set_custom_header_match(
                'test_multiprocess_cache_hit_callback__record'):
            list(self.r.get_subreddit(self.sr).get_new())  # get the cfduid
            list(self.r.get_subreddit(self.sr).get_new())  # put in the cache
            with mock_sys_stream("stdout"):
                list(self.r.get_subreddit(self.sr).get_new())
                sys.stdout.seek(0)
                self.assertEqual(
                    'HIT GET https://oauth.reddit.com/r/reddit_api_test/new\n',
                    sys.stdout.read())

    def test_multiprocess_mock_exception(self):
        def raiser(*a, **kw):
            raise Exception('Mocked Exception Raise')
        with patch.object(multiprocess.RequestHandler, 'do_request',
                          wraps=raiser):
            assertRaisesRegex(self, Exception, '^Mocked Exception Raise$',
                              next, self.r.get_new())
