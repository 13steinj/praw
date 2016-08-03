import errno
from mock import patch
from os import strerror
from praw import Reddit, multiprocess
from praw import handlers
from praw.errors import ClientException
import signal
from six import assertRaisesRegex
from six.moves.cPickle import UnpicklingError
import socket
import sys
from time import time
from .helper import (betamax_multiprocess_custom_header,
                     betamax_multiprocess, betamax,
                     mock_sys_stream, NewOAuthPRAWTest,
                     PRAWTest, USER_AGENT)


class MultiProcessUnitTest(PRAWTest):
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
                raise {1: socket.error,
                       2: UnpicklingError,
                       3: Exception}[num](errno.EPIPE, strerror(errno.EPIPE))
            except:
                return sys.exc_info()

        with patch.object(sys, 'exc_info', side_effect=[
            getexcinfo(1),
            getexcinfo(2),
            getexcinfo(3),
        ]):
            server.handle_error('', ('127.0.0.1', 10101))  # pass
            with mock_sys_stream('stderr'):
                server.handle_error('', ('127.0.0.1', 10101))
                sys.stderr.seek(0)
                self.assertEqual('Invalid connection from 127.0.0.1\n',
                                 sys.stderr.read())
            self.assertEqual(
                self.assertRaisesAndReturn(
                    Exception, server.handle_error, '', ('127.0.0.1', 10101)
                ).args,
                (errno.EPIPE, strerror(errno.EPIPE)),
            )

        server.server_close()


class MultiProcessIntegrationTest(NewOAuthPRAWTest):
    def setUp(self):
        self.configure()
        self.server, self.server_thread = multiprocess.run(return_objects=True)
        self.r = Reddit(USER_AGENT, handler=handlers.MultiprocessHandler())

    def tearDown(self):
        # clean up the server
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join()

    @betamax_multiprocess_custom_header()
    def test_multiprocess_cache(self):
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
        # this tests the actual eviction
        self.r.handler.evict(
            'https://oauth.reddit.com/r/reddit_api_test/new')
        self.assertEqual(self.server.RequestHandlerClass.cache, {})

    def test_multiprocess_mock_exception(self):
        with patch.object(multiprocess.RequestHandler, 'do_request',
                          side_effect=Exception('Mocked Exception Raise')):
            assertRaisesRegex(self, Exception, '^Mocked Exception Raise$',
                              next, self.r.get_new())

        with patch.object(handlers.socket.socket, 'connect',
                          side_effect=socket.error(errno.EPIPE,
                                                   strerror(errno.EPIPE))):
            assertRaisesRegex(self, socket.error, strerror(errno.EPIPE),
                              next, self.r.get_new())

    def test_multiprocess_handler_connection_refused(self):
        # socket.socket.connect is a C function, so it can't be mocked
        # appropriately becaause any access to attributes of the object
        # become NULL when trying to use the unbound method on an instance,
        # which in turn because the defaults are not applicable to the
        # socket settings the handler uses, causes the pipe to jam
        # instead lets shutdown the server, let the connection refuse, then
        # asynchronously start it back up again. But, this test can not be
        # run on all platforms, so let's quick return first.
        if not hasattr(signal, 'SIGALRM'):
            return
        self.tearDown()

        def newsig(*a):
            self.server, self.server_thread = multiprocess.run(
                return_objects=True)
        oldsig = signal.signal(signal.SIGALRM, newsig)
        signal.alarm(2)  # initial wait time is 2 seconds
        with mock_sys_stream("stderr"):
            self.r.handler.clear_cache()  # an actual request shouldn't be made
            sys.stderr.seek(0)
            self.assertEqual('Cannot connect to multiprocess server. I'
                             's it running? Retrying in 2 seconds.\n',
                             sys.stderr.read())
        signal.signal(signal.SIGALRM, oldsig)

    def test_multiprocess_handler_socket_read_errors(self):
        with patch.object(handlers.socket.socket, "connect",
                          side_effect=socket.error(errno.EALREADY,
                                                   strerror(errno.EALREADY))):
            with mock_sys_stream("stderr"):
                assertRaisesRegex(
                    self, ClientException,
                    '^Successive failures reading '
                    'from the multiprocess server\.$',
                    self.r.handler.clear_cache)
                sys.stderr.seek(0)
                self.assertIn(
                    'Lost connection with multiprocess server'
                    ' during read. Trying again.\n',
                    sys.stderr.read())

    def test_multiprocess_handler_pickle_read_errors(self):
        with patch.object(handlers.cPickle, 'load', side_effect=EOFError()):
            with mock_sys_stream("stderr"):
                assertRaisesRegex(
                    self, ClientException,
                    '^Successive failures reading '
                    'from the multiprocess server\.$',
                    self.r.handler.clear_cache)
                sys.stderr.seek(0)
                self.assertIn(
                    'Lost connection with multiprocess server'
                    ' during read. Trying again.\n',
                    sys.stderr.read())

    @betamax_multiprocess()
    def _test_multiprocess_equivalency_server(self):
        self.r.refresh_access_information(self.refresh_token['new_read'])
        return list(self.r.get_subreddit(self.sr).get_new())

    @betamax()
    def _test_multiprocess_equivalency_default(self):
        self.r.refresh_access_information(self.refresh_token['new_read'])
        return list(self.r.get_subreddit(self.sr).get_new())

    def test_multiprocess_equivalency(self):
        from_server = self._test_multiprocess_equivalency_server()
        super(MultiProcessIntegrationTest, self).setUp()
        from_default = self._test_multiprocess_equivalency_default()
        self.assertEqual(from_server, from_default)