# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0
#
# The contents of this file are subject to the Mozilla Public License
# Version 1.1 (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS"
# basis, WITHOUT WARRANTY OF ANY KIND, either express or implied. See
# the License for the specific language governing rights and
# limitations under the License.
#
# The Original Code is Pika.
#
# The Initial Developers of the Original Code are LShift Ltd, Cohesive
# Financial Technologies LLC, and Rabbit Technologies Ltd.  Portions
# created before 22-Nov-2008 00:00:00 GMT by LShift Ltd, Cohesive
# Financial Technologies LLC, or Rabbit Technologies Ltd are Copyright
# (C) 2007-2008 LShift Ltd, Cohesive Financial Technologies LLC, and
# Rabbit Technologies Ltd.
#
# Portions created by LShift Ltd are Copyright (C) 2007-2009 LShift
# Ltd. Portions created by Cohesive Financial Technologies LLC are
# Copyright (C) 2007-2009 Cohesive Financial Technologies
# LLC. Portions created by Rabbit Technologies Ltd are Copyright (C)
# 2007-2009 Rabbit Technologies Ltd.
#
# Portions created by Tony Garnock-Jones are Copyright (C) 2009-2010
# LShift Ltd and Tony Garnock-Jones.
#
# All Rights Reserved.
#
# Contributor(s): ______________________________________.
#
# Alternatively, the contents of this file may be used under the terms
# of the GNU General Public License Version 2 or later (the "GPL"), in
# which case the provisions of the GPL are applicable instead of those
# above. If you wish to allow use of your version of this file only
# under the terms of the GPL, and not to allow others to use your
# version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the
# notice and other provisions required by the GPL. If you do not
# delete the provisions above, a recipient may use your version of
# this file under the terms of any one of the MPL or the GPL.
#
# ***** END LICENSE BLOCK *****

import sys
import traceback
import socket
import asyncore
import time
import select
from heapq import heappush, heappop
from errno import EAGAIN
import connection
import platform
import spec
from datetime import datetime

try:
    import ssl
    SSL = True
except ImportError:
    SSL = False


class RabbitDispatcher(asyncore.dispatcher):
    def __init__(self, connection):
        asyncore.dispatcher.__init__(self)
        self.connection = connection

    def handle_connect(self):
        self.connection.on_connected()
        if self.connection.parameters.ssl:
            self.socket.do_handshake()

    def handle_close(self):
        self.connection.on_disconnected()
        self.connection.dispatcher = None
        self.close()

    def handle_read(self):
        try:
            buf = self.recv(self.connection.suggested_buffer_size())
        except socket.error as exn:
            if hasattr(exn, 'errno') and (exn.errno == EAGAIN):
                # Weird, but happens very occasionally.
                return
            else:
                self.handle_close()
                return

        if not buf:
            self.close()
            return

        self.connection.on_data_available(buf)

    def writable(self):
        return bool(self.connection.outbound_buffer)

    def handle_write(self):
        fragment = self.connection.outbound_buffer.read()
        r = self.send(fragment)
        self.connection.outbound_buffer.consume(r)


class AsyncoreConnection(connection.Connection):
    def delayed_call(self, delay_sec, callback):
        add_oneshot_timer_rel(delay_sec, callback)

    def connect(self, host, port):
        self.dispatcher = RabbitDispatcher(self)
        self.dispatcher.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        # Wrap the SSL socket if we SSL turned on
        if self.parameters.ssl:
            self.dispatcher.socket.setblocking(1)
            self.dispatcher.socket.settimeout(30)
            if self.parameters.ssl_options:
                self.dispatcher.socket = ssl.wrap_socket(self.dispatcher.socket,
                                                         **self.parameters.ssl_options)
            else:
                self.dispatcher.socket = ssl.wrap_socket(self.dispatcher.socket)

            # Fix 2.7.1+ SSL socket bug
            # If Python version is 2.7.1, we should connect it first,
            # then everything works OK
            # if we use Python 2.7.2, and we connect it first, then we will get a
            # double connect exception.
            # so we only connect the socket when Python version is 2.7.1
            if platform.python_version().startswith("2.7.1"):
                self.dispatcher.socket.connect((host, port or spec.PORT))

        # Set the socket to non-blocking
        if not self.parameters.ssl:
            self.dispatcher.socket.settimeout(None)
            self.dispatcher.socket.setblocking(0)
        self.dispatcher.connect((host, port or spec.PORT))

    def disconnect_transport(self):
        if self.dispatcher:
            self.dispatcher.close()
            # Then we should delete reference for Heartbeat callback in timer_heap
            # if we do not do this, timer_heap will be a memory leak maker: The
            # Connection object will be referenced for a very long time(maybe forever)
            # So we should remove the callback reference in timer_heap list
            for i in xrange(len(timer_heap)):
                try:
                    conn_obj = timer_heap[i][1].im_self.connection
                    if not conn_obj.connection_open:
                        del timer_heap[i]
                except:
                    pass

    def flush_outbound(self):
        while self.outbound_buffer:
            self.drain_events()

    def wait_for_open(self):
        while (not self.connection_open) and \
                (self.reconnection_strategy.can_reconnect() or (not self.connection_close)):
            start = datetime.now()
            self.drain_events(300)
            delta = datetime.now() - start
            if delta.seconds >= 300:
                raise Exception("Wait for open timeout")

    def drain_events(self, timeout=None):
        loop(count=1, timeout=timeout)

timer_heap = []


def add_oneshot_timer_abs(firing_time, callback):
    heappush(timer_heap, (firing_time, callback))


def add_oneshot_timer_rel(firing_delay, callback):
    add_oneshot_timer_abs(time.time() + firing_delay, callback)


def next_event_timeout(default_timeout=None):
    cutoff = run_timers_internal()
    if timer_heap:
        timeout = timer_heap[0][0] - cutoff
        if default_timeout is not None and timeout > default_timeout:
            timeout = default_timeout
    elif default_timeout is None:
        timeout = 30.0  # default timeout
    else:
        timeout = default_timeout
    return timeout


def log_timer_error(info):
    sys.stderr.write('EXCEPTION IN ASYNCORE_ADAPTER TIMER\n')
    traceback.print_exception(*info)


def run_timers_internal():
    cutoff = time.time()
    while timer_heap and timer_heap[0][0] < cutoff:
        try:
            heappop(timer_heap)[1]()
        except:
            log_timer_error(sys.exc_info())
        cutoff = time.time()
    return cutoff


def loop1(map, timeout=None):
    if map:
        asyncore.loop(timeout=next_event_timeout(timeout), map=map, count=1, use_poll=True)
    else:
        time.sleep(next_event_timeout(timeout))


def loop(map=None, count=None, timeout=None):
    if map is None:
        map = asyncore.socket_map
    if count is None:
        while (map or timer_heap):
            loop1(map, timeout)
    else:
        while (map or timer_heap) and count > 0:
            loop1(map, timeout)
            count = count - 1
        run_timers_internal()
