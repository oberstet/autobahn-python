###############################################################################
#
# The MIT License (MIT)
#
# Copyright (c) typedef int GmbH
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
###############################################################################

import os
import struct
import zlib

if os.environ.get("USE_TWISTED", False):
    from base64 import b64decode
    from unittest.mock import MagicMock, patch

    from txaio.testutil import replace_loop

    from autobahn.testutil import FakeTransport
    from autobahn.twisted.websocket import (
        WebSocketClientFactory,
        WebSocketClientProtocol,
        WebSocketServerFactory,
        WebSocketServerProtocol,
    )
    from autobahn.exception import PayloadExceededError
    from autobahn.websocket.compress_deflate import PerMessageDeflate
    from twisted.internet.address import IPv4Address
    from twisted.internet.task import Clock
    from twisted.trial import unittest

    @patch("base64.b64encode")
    def create_client_frame(b64patch, **kwargs):
        """
        Kind-of hack-y; maybe better to re-factor the Protocol to have a
        frame-encoder method-call? Anyway, makes a throwaway protocol
        encode a frame for us, collects the .sendData call and returns
        the data that would have gone out. Accepts all the kwargs that
        WebSocketClientProtocol.sendFrame() accepts.
        """

        # only real way to inject a "known" secret-key for the headers
        # to line up... :/
        b64patch.return_value = b"QIatSt9QkZPyS4QQfdufO8TgkL0="

        factory = WebSocketClientFactory(protocols=["wamp.2.json"])
        factory.protocol = WebSocketClientProtocol
        factory.doStart()
        proto = factory.buildProtocol(IPv4Address("TCP", "127.0.0.9", 65534))
        proto.transport = FakeTransport()
        proto.connectionMade()
        proto.data = mock_handshake_server
        proto.processHandshake()

        data = []

        def collect(d, *args):
            data.append(d)

        proto.sendData = collect

        proto.sendFrame(**kwargs)
        return b"".join(data)

    # beware the evils of line-endings...
    mock_handshake_client = b"GET / HTTP/1.1\r\nUser-Agent: AutobahnPython/0.10.2\r\nHost: localhost:80\r\nUpgrade: WebSocket\r\nConnection: Upgrade\r\nPragma: no-cache\r\nCache-Control: no-cache\r\nSec-WebSocket-Key: 6Jid6RgXpH0RVegaNSs/4g==\r\nSec-WebSocket-Protocol: wamp.2.json\r\nSec-WebSocket-Version: 13\r\n\r\n"

    mock_handshake_server = b'HTTP/1.1 101 Switching Protocols\r\nServer: AutobahnPython/0.10.2\r\nX-Powered-By: AutobahnPython/0.10.2\r\nUpgrade: WebSocket\r\nConnection: Upgrade\r\nSec-WebSocket-Protocol: wamp.2.json\r\nSec-WebSocket-Accept: QIatSt9QkZPyS4QQfdufO8TgkL0=\r\n\r\n\x81~\x02\x19[1,"crossbar",{"roles":{"subscriber":{"features":{"publisher_identification":true,"pattern_based_subscription":true,"subscription_revocation":true}},"publisher":{"features":{"publisher_identification":true,"publisher_exclusion":true,"subscriber_blackwhite_listing":true}},"caller":{"features":{"caller_identification":true,"progressive_call_results":true}},"callee":{"features":{"progressive_call_results":true,"pattern_based_registration":true,"registration_revocation":true,"shared_registration":true,"caller_identification":true}}}}]\x18'

    class TestDeflate(unittest.TestCase):
        @staticmethod
        def _decoder(max_message_size=None):
            return PerMessageDeflate(
                is_server=False,
                server_no_context_takeover=False,
                client_no_context_takeover=False,
                server_max_window_bits=15,
                client_max_window_bits=15,
                mem_level=8,
                max_message_size=max_message_size,
            )

        @staticmethod
        def _compress(payload, window_bits=15):
            # Produce the permessage-deflate wire body for `payload`: raw
            # DEFLATE, Z_SYNC_FLUSH, with the trailing 0x00 0x00 0xff 0xff
            # stripped (mirrors PerMessageDeflate.end_compress_message()).
            compressor = zlib.compressobj(
                zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -window_bits
            )
            return (
                compressor.compress(payload) + compressor.flush(zlib.Z_SYNC_FLUSH)
            )[:-4]

        def test_max_size_rejects_oversized(self):
            # A message that inflates beyond max_message_size must be rejected
            # cleanly (PayloadExceededError), NOT silently truncated to the cap.
            decoder = self._decoder(max_message_size=10)
            body = self._compress(b"x" * 2000)
            decoder.start_decompress_message()
            self.assertRaises(
                PayloadExceededError, decoder.decompress_message_data, body
            )

        def test_max_size_boundary_ok(self):
            # A message whose inflated size is exactly max_message_size is
            # accepted and returned in full (the limit is "strictly greater").
            decoder = self._decoder(max_message_size=2000)
            body = self._compress(b"x" * 2000)
            decoder.start_decompress_message()
            data = decoder.decompress_message_data(body)
            self.assertEqual(data, b"x" * 2000)

        def test_under_max_size_roundtrips_without_corruption(self):
            # Under the cap: full data returned AND end_decompress_message()
            # must not raise. The old truncation left the stream mid-token,
            # so the sync-flush trailer raised zlib error -3.
            decoder = self._decoder(max_message_size=2000)
            payload = b"x" * 1000
            body = self._compress(payload)
            decoder.start_decompress_message()
            data = decoder.decompress_message_data(body)
            self.assertEqual(data, payload)
            decoder.end_decompress_message()

        def test_max_size_cumulative_across_frames(self):
            # The cap bounds the whole message, not each frame: a message
            # delivered in two frame-sized chunks whose combined inflated size
            # exceeds the cap must be rejected, not truncated.
            decoder = self._decoder(max_message_size=1500)
            body = self._compress(b"x" * 2000)
            half = len(body) // 2
            decoder.start_decompress_message()

            def feed_both():
                decoder.decompress_message_data(body[:half])
                decoder.decompress_message_data(body[half:])

            self.assertRaises(PayloadExceededError, feed_both)

        def test_no_max_size(self):
            decoder = self._decoder(max_message_size=None)
            body = self._compress(b"x" * 2000)
            decoder.start_decompress_message()
            data = decoder.decompress_message_data(body)
            self.assertEqual(data, b"x" * 2000)

    class TestClient(unittest.TestCase):
        def setUp(self):
            self.factory = WebSocketClientFactory(protocols=["wamp.2.json"])
            self.factory.protocol = WebSocketClientProtocol
            self.factory.doStart()

            self.proto = self.factory.buildProtocol(
                IPv4Address("TCP", "127.0.0.1", 65534)
            )
            self.transport = FakeTransport()
            self.proto.transport = self.transport
            self.proto.connectionMade()

        def tearDown(self):
            if self.proto.openHandshakeTimeoutCall:
                self.proto.openHandshakeTimeoutCall.cancel()
            self.factory.doStop()
            # not really necessary, but ...
            del self.factory
            del self.proto

        def test_missing_reason_raw(self):
            # we want to hit the "STATE_OPEN" case, so pretend we're there
            self.proto.echoCloseCodeReason = True
            self.proto.state = self.proto.STATE_OPEN
            self.proto.websocket_version = 1

            self.proto.sendCloseFrame = MagicMock()

            self.proto.onCloseFrame(1000, None)

        def test_unclean_timeout_client(self):
            """
            make a delayed call to drop the connection (client-side)
            """

            if False:
                self.proto.factory._log = print

            # get to STATE_OPEN
            self.proto.websocket_key = b64decode("6Jid6RgXpH0RVegaNSs/4g==")
            self.proto.data = mock_handshake_server
            self.proto.processHandshake()
            self.assertEqual(self.proto.state, WebSocketServerProtocol.STATE_OPEN)
            self.assertTrue(self.proto.serverConnectionDropTimeout > 0)

            with replace_loop(Clock()) as reactor:
                # now 'do the test' and transition to CLOSING
                self.proto.sendCloseFrame()
                self.proto.onCloseFrame(1000, b"raw reason")

                # check we scheduled a call
                self.assertEqual(len(reactor.calls), 1)
                self.assertEqual(
                    reactor.calls[0].func, self.proto.onServerConnectionDropTimeout
                )
                self.assertEqual(
                    reactor.calls[0].getTime(), self.proto.serverConnectionDropTimeout
                )

                # now, advance the clock past the call (and thereby
                # execute it)
                reactor.advance(self.proto.closeHandshakeTimeout + 1)

                # we should have called abortConnection
                self.assertTrue(self.proto.transport.abort_called())
                # ...too "internal" for an assert?
                self.assertEqual(self.proto.state, WebSocketServerProtocol.STATE_CLOSED)

    class TestPing(unittest.TestCase):
        def setUp(self):
            self.factory = WebSocketServerFactory(protocols=["wamp.2.json"])
            self.factory.protocol = WebSocketServerProtocol
            self.factory.doStart()

            self.proto = self.factory.buildProtocol(
                IPv4Address("TCP", "127.0.0.1", 65534)
            )
            self.transport = MagicMock()
            self.proto.transport = self.transport
            self.proto.connectionMade()

        def tearDown(self):
            if self.proto.openHandshakeTimeoutCall:
                self.proto.openHandshakeTimeoutCall.cancel()
            self.factory.doStop()
            # not really necessary, but ...
            del self.factory
            del self.proto

        def test_unclean_timeout(self):
            """
            make a delayed call to drop the connection
            """
            # first we have to drive the protocol to STATE_CLOSING
            # ... which we achieve by sendCloseFrame after we're in
            # STATE_OPEN
            # XXX double-check this is the correct code-path to get here
            # "normally"?

            # get to STATE_OPEN
            self.proto.data = mock_handshake_client
            self.proto.processHandshake()
            self.assertTrue(self.proto.state == WebSocketServerProtocol.STATE_OPEN)

            with replace_loop(Clock()) as reactor:
                # now 'do the test' and transition to CLOSING
                self.proto.sendCloseFrame()

                # check we scheduled a call
                self.assertEqual(len(reactor.calls), 1)

                # now, advance the clock past the call (and thereby
                # execute it)
                reactor.advance(self.proto.closeHandshakeTimeout + 1)

                # we should have called abortConnection
                self.assertEqual(
                    "call.abortConnection()", str(self.proto.transport.method_calls[-1])
                )
                self.assertTrue(self.proto.transport.abortConnection.called)
                # ...too "internal" for an assert?
                self.assertEqual(self.proto.state, WebSocketServerProtocol.STATE_CLOSED)

        def test_auto_pingpong_timeout(self):
            """
            autoping and autoping-timeout timing
            """
            # options are evaluated in succeedHandshake, called below
            self.proto.autoPingInterval = 5
            self.proto.autoPingTimeout = 2

            with replace_loop(Clock()) as reactor:
                # get to STATE_OPEN
                self.proto.data = mock_handshake_client
                self.proto.processHandshake()
                self.assertTrue(self.proto.state == WebSocketServerProtocol.STATE_OPEN)

                # we should have scheduled an autoPing
                self.assertEqual(1, len(reactor.calls))

                # advance past first auto-ping timeout
                reactor.advance(5)

                # first element from args tuple from transport.write()
                # call is our data
                self.assertTrue(self.transport.write.called)
                data = self.transport.write.call_args[0][0]

                _data = bytes([data[0]])

                # the opcode is the lower 7 bits of the first byte.
                (opcode,) = struct.unpack("B", _data)
                opcode = opcode & (~0x80)

                # ... and should be "9" for ping
                self.assertEqual(9, opcode)

                # Because we have autoPingTimeout there should be
                # another delayed-called created now
                self.assertEqual(1, len(reactor.calls))
                self.assertNotEqual(self.proto.state, self.proto.STATE_CLOSED)

                # ...which we'll now cause to trigger, aborting the connection
                reactor.advance(3)
                self.assertEqual(self.proto.state, self.proto.STATE_CLOSED)

        def test_auto_ping_got_pong(self):
            """
            auto-ping with correct reply cancels timeout
            """
            # options are evaluated in succeedHandshake, called below
            self.proto.autoPingInterval = 5
            self.proto.autoPingTimeout = 2

            with replace_loop(Clock()) as reactor:
                # get to STATE_OPEN
                self.proto.data = mock_handshake_client
                self.proto.processHandshake()
                self.assertTrue(self.proto.state == WebSocketServerProtocol.STATE_OPEN)

                # we should have scheduled an autoPing
                self.assertEqual(1, len(reactor.calls))

                # advance past first auto-ping timeout
                reactor.advance(5)

                # should have an auto-ping timeout scheduled, and we
                # save it for later (to check it got cancelled)
                self.assertEqual(1, len(reactor.calls))
                timeout_call = reactor.calls[0]

                # elsewhere we check that we actually send an opcode-9
                # message; now we just blindly inject our own reply
                # with a PONG frame

                frame = create_client_frame(
                    opcode=10, payload=self.proto.autoPingPending
                )
                self.proto.data = frame
                # really needed twice; does header first, then rest
                self.proto.processData()
                self.proto.processData()

                # which should have cancelled the call
                self.assertTrue(timeout_call.cancelled)
