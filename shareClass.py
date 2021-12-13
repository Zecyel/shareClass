import socket
from queue import Queue
from _thread import start_new_thread as createThread

port = 1627
host = socket.gethostname()
maxBufferSize = 1048576 # 1M
splitter = "\t" # 消息分隔符

class WebShare():

    _isClient = True # client or server
    # 现在只能两个进程连接，所以要么是 server 要么是 client

    _server_sock = None
    _client_sock = socket.socket() # the socket of client or server

    _msgPool = Queue() # 消息池

    def __init__(self):
        # self._client_sock.settimeout(0.1) # set 100ms of timeout
        # 不设置 timeout 是为了保证两端连接上了
        # 所以 __init__ 是阻塞式的
        self._isClient = self._tryConnect()
        if not self._isClient:
            self._server_sock = socket.socket() # create a server
            self._server_sock.bind((host, port))
            self._server_sock.listen(1) # 点对点
            self._client_sock, _ = self._server_sock.accept() # addr 丢弃
        createThread(self._autoRecv, tuple())
    
    def __del__(self):
        self._client_sock.close()
        if not self._isClient:
            self._server_sock.close()

    def _tryConnect(self):
        try:
            self._client_sock.connect((host, port))
            return True # 如果连接上了自己就是 client
        except:
            return False

    def _recvMsg(self):
        return self._client_sock.recv(maxBufferSize).decode()

    def _sendMsg(self, msg):
        self._client_sock.send(msg.encode())

    _halfMsg = "" # 如果上一条消息接收到一般没有后面的分隔符 splitter，那么说明受到了半条消息

    def _autoRecv(self):
        while True:
            msg = (self._halfMsg + self._recvMsg()).split(splitter) # 随便调了一个字符作分隔
            self._halfMsg = msg[-1] # 如果消息是完整的则 msg[-1] == ""
            for i in msg[:-1]:
                self._msgPool.put(i)
    
    def sendMsg(self, msg): # 对外的就只有这两个函数了
        self._sendMsg(msg + splitter)

    def getMsg(self): # 对外的就只有这两个函数了
        if self._msgPool.empty():
            return False, None
        return True, self._msgPool.get()

class SpinLock():
    _spinLock = False
    _v = None
    def __init__(self, v):
        self._v = v
        self._spinLock = False
        
    def run(self, op):
        while self._spinLock:
            pass
        self._spinLock= True
        ret = op(self._v)
        self._spinLock = False
        return ret

import time # 用来生成时间戳

class WebTunnel(): # 让每条消息都能得到一个它对应的回复，通过给每条消息一个 id 来实现
    # 把时间戳作为 id 防撞
    _web = None
    _handler = lambda x: "" # 必须是一个传入 str 返回 str 的函数
    _mp = SpinLock({}) # key: msgId   value: msgContent

    def __init__(self): # 阻塞式
        self._web = WebShare()

    def bindHandler(self, foo):
        self._handler = foo

    def _autoRun(self):
        while True:
            stt, msg = self._web.getMsg()
            if not stt:
                continue
            # msg 结构： 请求/反馈 + 空格 + id + 空格 + 消息正文
            tmp = msg.split(" ", 2)
            msgType = tmp[0]
            msgId = int(tmp[1])
            msg = tmp[2]
            if msgType == "Req":
                self._web.sendMsg("Resp %d %s" % (msgId, self._handler(msg)))
            elif msgType == "Resp":
                def foo(x): x[msgId] = msg
                self._mp.run(foo)
            else:
                assert False

    def activate(self):
        createThread(self._autoRun, tuple())

    def sendMsg(self, msg): # 阻塞式
        _id = int(__import__("time").time() * 10000000)
        self._web.sendMsg("Req %d %s" % (_id, msg))
        while not self._mp.run(lambda x: _id in x):
            pass
        ret = self._mp.run(lambda x: x[_id])
        self._mp.run(lambda x: x.pop(_id))
        return ret

    def sendMsgUnblocking(self, msg, callback): # 非阻塞式
        ret = [""]
        def waiter(self, msg, ret):
            ret[0] = self.sendMsg(msg)
        createThread(waiter, (msg, ret))
        callback(ret[0])

import json # json 会把元组转换成列表，后续可能会换用别的库或者自己写一个

class ShareClass():
    _sharePool = {}

    def __init__(self): # 阻塞式
        self._webTunnel = WebTunnel()
        self._webTunnel.bindHandler(self._autoRespond)
        self._webTunnel.activate()

    def _autoRespond(self, msg):
        msg = json.loads(msg)
        try:
            ret = getattr(self._sharePool[msg["name"]], msg["funcName"])(*msg["args"], **msg["kwargs"])
        except:
            ret = None
        return json.dumps(ret)
            
    def share(self, name, a):
        self._sharePool[name] = a

    def unshare(self, name):
        self._sharePool.pop(name)
    
    def __getitem__(self, name):
        if name in self._sharePool:
            return self._sharePool[name]
        else:
            return self._getRemoteClass(name)

    def __setitem__(self, name, obj):
        if name in self._sharePool:
            self._sharePool[name] = obj
        else:
            assert "Permission Denied" == ""

    def _getRemoteClass(self, name):

        def generateVirtFunc(funcName):
            def virtFunc(*args, **kwargs):
                msg = {"name": name, "funcName": funcName, "args": args, "kwargs": kwargs}
                ret = json.loads(self._webTunnel.sendMsg(json.dumps(msg)))
                return ret

            return virtFunc
        class VirtualClass():
            def __getattr__(subSelf, key):
                return generateVirtFunc(key)

        a = VirtualClass() # 创建一个虚拟类的实例
        return a