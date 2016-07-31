"""Provides a request server to be used with the multiprocess handler."""

from __future__ import print_function, unicode_literals

import socket
import sys
from optparse import OptionParser
from praw import __version__
from praw.handlers import DefaultHandler
from requests import Session
from six.moves import cPickle, socketserver  # pylint: disable=F0401
from threading import Lock, Thread


class PRAWMultiprocessServer(socketserver.ThreadingTCPServer):
    # pylint: disable=R0903,W0232
    """A TCP server that creates new threads per connection.

    Note: Only a RequestHandler from this module is guaranteed
    to work appropriately in ratelimiting requests and in closing.
    """

    allow_reuse_address = True

    @staticmethod
    def handle_error(_, client_addr):
        """Mute tracebacks of common errors."""
        exc_type, exc_value, _ = sys.exc_info()
        if exc_type is socket.error and exc_value[0] == 32:
            pass
        elif exc_type is cPickle.UnpicklingError:
            sys.stderr.write('Invalid connection from {0}\n'
                             .format(client_addr[0]))
            sys.stderr.flush()
        else:
            raise

    def server_close(self):
        """Called to clean-up the server."""
        # original server_close(), ThreadingMixin is an old-style class,
        # so calling super() won't work here unfortunately
        self.socket.close()
        # clean up the requests Session
        self.RequestHandlerClass.http.close()


class RequestHandler(socketserver.StreamRequestHandler):
    # pylint: disable=W0232
    """A class that handles incoming requests.

    Requests to the same domain are cached and rate-limited.

    """

    ca_lock = Lock()  # lock around cache and timeouts
    cache = {}  # caches requests
    http = Session()  # used to make requests
    last_call = {}  # Stores a two-item list: [lock, previous_call_time]
    rl_lock = Lock()  # lock used for adding items to last_call
    timeouts = {}  # store the time items in cache were entered

    # Add in methods to evict cache
    do_evict = DefaultHandler.evict
    do_clear_cache = DefaultHandler.clear_cache

    @staticmethod
    def cache_hit_callback(key):
        """Output when a cache hit occurs."""
        sys.stdout.write('HIT {0} {1}\n'.format(
            'POST' if key[1][1] else 'GET', key[0]))
        sys.stdout.flush()

    @DefaultHandler.with_cache
    @DefaultHandler.rate_limit
    def do_request(self, request, proxies, timeout, **_):
        """Dispatch the actual request and return the result."""
        sys.stdout.write('{0} {1}'.format(request.method, request.url))
        sys.stdout.flush()
        return self.http.send(request, proxies=proxies, timeout=timeout,
                              allow_redirects=False)

    def handle(self):
        """Parse the RPC, make the call, and pickle up the return value."""
        data = cPickle.load(self.rfile)  # pylint: disable=E1101
        method = data.pop('method')
        try:
            retval = getattr(self, 'do_{0}'.format(method))(**data)
        except Exception as e:
            # All exceptions should be passed to the client
            retval = e
        cPickle.dump(retval, self.wfile,  # pylint: disable=E1101
                     cPickle.HIGHEST_PROTOCOL)


def run(address='localhost', port=10101, return_objects=False):
    """The entry point from the praw-multiprocess utility.

    :param address: The address or host to listen on. 0.0.0.0 to
        listen on all addresses. Default: localhost
    :param port: The port to listen for requests on. Default: 10101

    :return: A tuple of the server object and the thread serving it
        if return_objects is True, else block the thread until a
        KeyboardInterrupt is raised, upon which, shutdown the server.

    Note: Command line arguments -a/--addr and -p/--port take priority
        over the parameters.
    """
    parser = OptionParser(version='%prog {0}'.format(__version__))
    parser.add_option('-a', '--addr', default='localhost',
                      help=('The address or host to listen on. Specify -a '
                            '0.0.0.0 to listen on all addresses. '
                            'Default: localhost'))
    parser.add_option('-p', '--port', type='int', default='10101',
                      help=('The port to listen for requests on. '
                            'Default: 10101'))
    options, _ = parser.parse_args()
    address = address or options.addr
    port = port or options.port
    server = PRAWMultiprocessServer((address, port), RequestHandler)
    sys.stdout.write('Listening on {0} port {1}\n'.format(address, port))
    sys.stdout.flush()
    server_thread = Thread(target=server.serve_forever)
    server_thread.daemon = not return_objects  # For Ctrl-Z
    server_thread.start()
    if return_objects:
        return server, server_thread

    try:
        sys.stdout.write('CTRL-C to shutdown server.\n')
        sys.stdout.flush()
        while True:
            pass
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()
        server_thread.join()
        sys.stdout.write('Goodbye!\n')
        sys.stdout.flush()
