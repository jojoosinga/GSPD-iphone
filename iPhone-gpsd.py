#!/usr/bin/python
#
# By Balint Seeber, 2010
# http://spench.net/drupal/software/iphone-gps
#
# Uses portions of:
# http://gpsd.berlios.de/
# http://fragments.turtlemeat.com/pythonwebserver.php
#
# This code is provided 'as is'. It should NOT be used where timely and/or accurate position data is required.

import sys, os, signal, time, getopt, socket
import string,cgi,time,urlparse
from os import curdir, sep
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import exceptions, threading, pty, termios
import SocketServer
from operator import xor
#from datetime import time, date, datetime
import datetime

import gps

WRITE_PAD = 0.033 # 33ms

class FakeGPS:
    "A fake GPS is a pty with a test log ready to be cycled to it."
    def __init__(self, #logfp,
                 speed=38400, databits=8, parity='N', stopbits=1,
                 #predump=False, progress=None
                 ):
        #self.progress = progress
        #self.go_predicate = lambda: True
        #self.readers = 0
        #self.index = 0
        self.speed = speed
        #self.name = None
        baudrates = {
            0: termios.B0,
            50: termios.B50,
            75: termios.B75,
            110: termios.B110,
            134: termios.B134,
            150: termios.B150,
            200: termios.B200,
            300: termios.B300,
            600: termios.B600,
            1200: termios.B1200,
            1800: termios.B1800,
            2400: termios.B2400,
            4800: termios.B4800,
            9600: termios.B9600,
            19200: termios.B19200,
            38400: termios.B38400,
            57600: termios.B57600,
            115200: termios.B115200,
            230400: termios.B230400,
        }
        speed = baudrates[speed]	# Throw an error if the speed isn't legal
        #if type(logfp) == type(""):
        #    logfp = open(logfp, "r");
        #self.name = logfp.name
        #self.testload = TestLoad(logfp, predump)
        #self.progress("gpsfake: %s provides %d sentences\n" % (self.name, len(self.testload.sentences)))
        # FIXME: explicit arguments should probably override this
        #if self.testload.serial:
        #    (speed, databits, parity, stopbits) = self.testload.serial
        (self.master_fd, self.slave_fd) = pty.openpty()
        self.slave = os.ttyname(self.slave_fd)
        (iflag, oflag, cflag, lflag, ispeed, ospeed, cc) = termios.tcgetattr(self.slave_fd)
        cc[termios.VMIN] = 1
        cflag &= ~(termios.PARENB | termios.PARODD | termios.CRTSCTS)
        cflag |= termios.CREAD | termios.CLOCAL
        iflag = oflag = lflag = 0
        iflag &=~ (termios.PARMRK | termios.INPCK)
        cflag &=~ (termios.CSIZE | termios.CSTOPB | termios.PARENB | termios.PARODD)
        if databits == 7:
            cflag |= termios.CS7
        else:
            cflag |= termios.CS8
        if stopbits == 2:
            cflag |= termios.CSTOPB
        if parity == 'E':
            iflag |= termios.INPCK
            cflag |= termios.PARENB
        elif parity == 'O':
            iflag |= termios.INPCK
            cflag |= termios.PARENB | termios.PARODD
        ispeed = ospeed = speed
        termios.tcsetattr(self.slave_fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])

    def read(self):
        "Discard control strings written by gpsd."
        # A tcflush implementation works on Linux but fails on OpenBSD 4.
        termios.tcflush(self.master_fd, termios.TCIFLUSH)
        # Alas, the FIONREAD version also works on Linux and fails on OpenBSD.
        #try:
        #    buf = array.array('i', [0])
        #    fcntl.ioctl(self.master_fd, termios.FIONREAD, buf, True)
        #    n = struct.unpack('i', buf)[0]
        #    os.read(self.master_fd, n)
        #except IOError:
        #    pass

    def feed(self, line):
        "Feed a line from the contents of the GPS log to the daemon."
        #line = self.testload.sentences[self.index % len(self.testload.sentences)]
        os.write(self.master_fd, line)
        #if self.progress:
        #    self.progress("gpsfake: %s feeds %d=%s\n" % (self.name, len(line), `line`))
        time.sleep(WRITE_PAD)
        #self.index += 1

    def drain(self):
        "Wait for the associated device to drain (e.g. before closing)."
        termios.tcdrain(self.master_fd)

class DaemonError(exceptions.Exception):
    def __init__(self, msg):
        self.msg = msg
    def __str__(self):
        return repr(self.msg)

class DaemonInstance:
    "Control a gpsd instance."
    def __init__(self, control_socket=None):
        self.sockfile = None
        self.pid = None
        if control_socket:
            self.control_socket = control_socket
        else:
            self.control_socket = "/tmp/gpsfake-%d.sock" % os.getpid()
        self.pidfile  = "/tmp/gpsfake_pid-%s" % os.getpid()
    def spawn(self, options, port, background=False, prefix=""):
        "Spawn a daemon instance."
        self.spawncmd = None
        if not '/usr/sbin' in os.environ['PATH']:
            os.environ['PATH']=os.environ['PATH'] + ":/usr/sbin"
        for path in os.environ['PATH'].split(':'):
            _spawncmd = "%s/gpsd" % path
            if os.path.isfile(_spawncmd) and os.access(_spawncmd, os.X_OK):
                self.spawncmd = _spawncmd
                break

        if not self.spawncmd:
            raise DaemonError("Cannot execute gpsd: executable not found.")
        # The -b option to suppress hanging on probe returns is needed to cope
        # with OpenBSD (and possibly other non-Linux systems) that don't support
        # anything we can use to implement the FakeGPS.read() method
        self.spawncmd += " -b -N -S %s -F %s -P %s %s" % (port, self.control_socket, self.pidfile, options)
        if prefix:
            self.spawncmd = prefix + " " + self.spawncmd.strip()
        if background:
            self.spawncmd += " &"
        status = os.system(self.spawncmd)
        if os.WIFSIGNALED(status) or os.WEXITSTATUS(status):
            raise DaemonError("daemon exited with status %d" % status)
    def wait_pid(self):
        "Wait for the daemon, get its PID and a control-socket connection."
        while True:
            try:
                fp = open(self.pidfile)
            except IOError:
                time.sleep(0.5)
                continue
            try:
                fp.seek(0)
                pidstr = fp.read()
                self.pid = int(pidstr)
            except ValueError:
                time.sleep(0.5)
                continue	# Avoid race condition -- PID not yet written
            fp.close()
            break
    def __get_control_socket(self):
        # Now we know it's running, get a connection to the control socket.
        if not os.path.exists(self.control_socket):
            return None
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, 0)
            self.sock.connect(self.control_socket)
        except socket.error:
            if self.sock:
                self.sock.close()
            self.sock = None
        return self.sock
    def is_alive(self):
        "Is the daemon still alive?"
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False
    def add_device(self, path):
        "Add a device to the daemon's internal search list."
        if self.__get_control_socket():
            self.sock.sendall("+%s\r\n" % path)
            self.sock.recv(12)
            self.sock.close()
    def remove_device(self, path):
        "Remove a device from the daemon's internal search list."
        if self.__get_control_socket():
            self.sock.sendall("-%s\r\n" % path)
            self.sock.recv(12)
            self.sock.close()
    def kill(self):
        "Kill the daemon instance."
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except OSError:
                pass
            self.pid = None
            time.sleep(1)	# Give signal time to land


class MyHandler(BaseHTTPRequestHandler):
    def ReadFile(self, filePath):
        try:
            f = open(filePath)
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(f.read())
            f.close()
        except IOError:
            self.send_error(404, 'File Not Found: %s' % filePath)

    #def log_message(format, ...)
    #   pass

    def do_GET(self):
        #print curdir + sep, self.path
        if self.path == "/":
            self.ReadFile(curdir + "/index.html")
        elif self.path.endswith(".html"):
            self.ReadFile(curdir + self.path) # self.path has /test.html
        else:
            self.send_error(404, 'Unknown file type')

    def do_POST(self):
        global rootnode
        ctype, pdict = cgi.parse_header(self.headers.getheader('Content-Length'))
        length = int(ctype);
        #print "Length: ", length
        ctype, pdict = cgi.parse_header(self.headers.getheader('Content-Type'))
        #print "Type: ", ctype

        if ctype == 'multipart/form-data':
            query = cgi.parse_multipart(self.rfile, pdict)
        elif ctype == 'application/x-www-form-urlencoded':
            form = self.rfile.read(length)
            #print form
            query = cgi.parse_qs(form)
            #query = urlparse.parse_qs(form) # 2.6.4
        else:
            self.send_error(403, 'Unknown content type: %s' % ctype)
            return

        self.send_response(200)
        self.end_headers()

        #print "Query: ", query
        vLat = query.get('Lat')
        vLon = query.get('Lon')
        vAcc = query.get('Acc')
        vTime = query.get('Time')

        print "Received %i updates:" % len(vLat)

        for i in range(0, len(vLat)):
            print "%s,%s,%s,%s" % (vLat[i], vLon[i], vAcc[i], vTime[i])

            time_str = vTime[i] # ms since the epoch
            try:
                timei = int(time_str)
            except:
                print "Time not available"
                return
            time_mod = timei % 1000
            time_sec = (timei - time_mod) / 1000;
            td = datetime.datetime.fromtimestamp(time_sec)
            ms = int(time_mod / 10) # Does a floor
            nmea_time = td.strftime("%H%M%S.") + ("%02i" % ms)
            date = td.strftime("%d%m%y")

            valid = "V"
            faa = "N"
            try:
                fAcc = int(float(vAcc[i]))
                if (fAcc <= 25):
                    valid = "A"
                    faa = "A"
            except:
                pass

            latf = float(vLat[i])
            lonf = float(vLon[i])
            latH = "N"
            lonH = "E"
            if (latf < 0.0):
                latH = "S"
                latf = -latf
            if (lonf < 0.0):
                lonH = "W"
                lonf = - lonf
            latD = int(latf)
            lonD = int(lonf)
            latMf = (latf - float(latD)) * 60.0
            lonMf = (lonf - float(lonD)) * 60.0
            #latM = int(latMf)
            #lonM = int(lonMf)

            lat = str(latD) + ("%.6f" % latMf) + "," + latH
            lon = str(lonD) + ("%.6f" % lonMf) + "," + lonH

            speed = ""      # 0.0
            heading = ""    # 0.0

            rmc = "GPRMC," + nmea_time + "," + valid + "," + lat + "," + lon + "," + speed + "," + heading + "," + date + ",,," + faa
            nmea = map(ord, rmc)
            check = reduce(xor, nmea)
            rmc = "$" + rmc + ("*%X" % check)
            print rmc
            rmc += "\r\n"
            m.newgps.feed(rmc)
        else:
            print("");

class MyMain:
    def main(self):
        try:
            print "Current directory: " + os.getcwd()

            self.serverWeb = HTTPServer(('', 80), MyHandler)
            #server_thread = threading.Thread(target=serverWeb.serve_forever)
            #server_thread.setDaemon(True)
            #server_thread.start()
            #print "HTTP Server running in thread:", server_thread.getName()

            self.daemon = DaemonInstance()
            self.daemon.spawn(background=True, prefix="", port=gps.GPSD_PORT, options="-G")
            self.daemon.wait_pid()
            print "GPSd running"

            self.newgps = FakeGPS()
            self.daemon.add_device(self.newgps.slave)
            print "Added ", self.newgps.slave

            print "HTTP Server running..."
            self.serverWeb.serve_forever()
        except KeyboardInterrupt:
            print '^C received, shutting down server'
            self.daemon.remove_device(self.newgps.slave)
            print "Removed device"
            self.daemon.kill()
            print "Killed GPSd"
            self.serverWeb.socket.close()
            print "Closed HTTP socket"

m = MyMain()

if __name__ == '__main__':
    m.main()
