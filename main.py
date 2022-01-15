import socket
from threading import Thread
import os
import json
from base64 import b64encode as b64e, b64decode as b64d
from io import BytesIO
import hashlib
import time
import queue

BUFFER = 1024

def jsondict(**kwargs):
    return json.dumps(kwargs).encode()


class ServerInstance:
    PASSWORD_HASH = b'u\xbb\x1d\xcf\xcdx`fy)\x80\x11iE\xeb\x88\xf5M\xc1Z\xd8fQ\x9bB1VY"{f\xe9'

    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('192.168.1.199', 37380))
        self.socket.listen(1)
        self.thread = Thread(target=self._main, daemon=True)
        self.conn: socket.socket = None
        self.addr = None
        self.shared_buffer = BytesIO()

    @classmethod
    def file_reader(self, fp):
        with open(fp, 'rb') as f:
            while (data:=f.read(1024)):
                yield data

    def send(self, **kwargs):
        try:
            self.conn.send(jsondict(**kwargs))#+b'\x1c')
        except Exception as e:
            print('Connection crashed with', e)

    def handle_connection(self):
        while True:
            try:
                msg = json.loads(self.conn.recv(BUFFER).decode())
            except ConnectionError:
                print('Client died?')
                return 0

            try:
                if msg['cmd'] == 'cd':
                    os.chdir(msg['data'])
                    self.send(cmd='response', data=os.getcwd())
                elif msg['cmd'] == 'py':
                    exec(msg['data'])
                    self.send(cmd='resp_complete')
                elif msg['cmd'] == 'read_shared':
                    self.send(cmd='response', data=self.shared_buffer.getvalue().decode())
                elif msg['cmd'] == 'clear_shared':
                    self.shared_buffer.close()
                    self.shared_buffer = BytesIO()
                    self.send(cmd='resp_complete')
                elif msg['cmd'] == 'ls':
                    self.send(cmd='response', data=json.dumps(os.listdir(os.getcwd())))
                elif msg['cmd'] == 'cwd':
                    self.send(cmd='response', data=os.getcwd())
                elif msg['cmd'] in ('cpfile', 'copy'):
                    fp = msg['data']
                    try:
                        self.send(cmd='file_start', file_size=os.path.getsize(fp))
                        assert self.conn.recv(128) == b'OK'
                        for i, chunk in enumerate(self.file_reader(fp)):
                            self.send(cmd='file_data', number=i+1, data=b64e(chunk).decode())
                            assert self.conn.recv(128) == b'OK'
                        self.send(cmd='file_complete')
                    except Exception as e:
                        self.send(cmd='error', data=str(e))

                elif msg['cmd'] in ('exit', 'quit'):
                    print('Client safely quit.')
                    self.send(cmd='exited')
                    return 0
                elif msg['cmd'] == 'close':
                    print('Death order received.')
                    self.send(cmd='closed')
                    return 1
                elif msg['cmd'] == 'ping':
                    self.send(cmd='response', data='pong')
                else:
                    self.send(cmd='error', data='unknown command')
            except Exception as e:
                print(str(e))
                self.send(cmd='error', data=str(e))

    def _main(self):
        while True:
            self.conn, self.addr = self.socket.accept()
            print('New client!', self.addr)
            if hashlib.sha256(self.conn.recv(128)).digest() != self.PASSWORD_HASH:
                self.conn.send(b'PWD_BAD')
                self.conn.close()
                print('He failed the password check omegalul.')
                continue
            self.conn.send(b'PWD_GOOD')
            if self.handle_connection() == 1:
                self.conn.close()
                self.socket.close()
                return
            self.conn.close()

    def start(self):
        self.thread.start()
        print('Server is running!')

class ClientInstance:
    def __init__(self, server_ip, server_port=37380):
        self.ip = server_ip
        self.port = server_port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.output_file = ''
        self.messages = queue.Queue()
        self.temp_buffer = BytesIO()

    def _receive_messages(self):
        C = b'\x1c'
        while True:
            data = self.socket.recv(BUFFER)
            if len(split:=data.split(C)) > 1:
                for msg in split:
                    self.temp_buffer.write(msg)
                    self.messages.put(self.temp_buffer.getvalue())
                    self.temp_buffer = BytesIO()
            else:
                self.temp_buffer.write(data)

    def connect(self, pwd):
        self.socket.connect((self.ip, self.port))
        self.socket.send(pwd.encode())
        if self.socket.recv(32) == b'PWD_BAD':
            print('Bad password!')
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            return
        while True:
            inp = input('>> ')
            sp = inp.find(' ')
            if sp == -1:
                cmd = inp
                data = ''
            else:
                cmd = inp[:sp]
                data = inp[sp+1:]

            if cmd == 'help':
                print('Available server commands:\ncd, py, read_shared, clear_shared, ls, cwd, copy, exit, quit, close, ping\n\
Available client commands:\nhelp, set_output_file, clientpy')
                continue
            elif cmd == 'clientpy':
                exec(data)
                continue
            elif cmd in ('set_output_file', 'sof'):
                self.output_file = data
                if os.path.exists(self.output_file):
                    if input('This file already exists. Do you want to overwrite it? [y/n] : ')[0].lower() != 'y':
                        print('Operation canceled.')
                        continue
                print(f'Set client output file to {self.output_file}.')
                continue
            elif cmd == 'ping':
                self.socket.send(jsondict(cmd='ping'))
            else:
                self.socket.send(jsondict(cmd=cmd, data=data))

            resp = json.loads(self.socket.recv(BUFFER).decode())
            if resp['cmd'] == 'resp_complete':
                print('Operation completed with no returned data.')
            elif resp['cmd'] == 'exited':
                print('Connection severed safely.')
                return
            elif resp['cmd'] == 'closed':
                print('Server closed safely.')
                return
            elif resp['cmd'] == 'response':
                print('Server response:', resp['data'])
            elif resp['cmd'] == 'error':
                print('Server error:', resp['data'])
            elif resp['cmd'] == 'file_start':
                self.socket.send(b'OK')
                fs = int(resp['file_size'])
                update_time = time.time()
                failed = False

                print('Transferring %s bytes...' % fs)

                with open(self.output_file, 'wb+') as f:
                    while (rdata := json.loads(self.socket.recv(BUFFER*2).decode()))['cmd'] != 'file_complete':
                        self.socket.send(b'OK')
                        if rdata['cmd'] == 'error':
                            print('Server error:', rdata['data'])
                            failed = True
                            break
                        else:
                            percent_done = int(rdata['number']) * 1024 / fs * 100
                            if time.time() - update_time > 3:
                                update_time = time.time()
                                print(f'{round(percent_done, 1)}% done...')
                            f.write(b64d(rdata['data']))
                    if not failed:
                        print('Transfer complete!')


MODE = 0

if MODE == 0:
    s = ServerInstance()
    s.start()
elif MODE == 1:
    c = ClientInstance('192.168.1.199')
