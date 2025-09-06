import logging
from logging.handlers import SocketHandler
import logging.handlers
import time
import socket

# 定义Unix域套接字路径
SOCK_PATH = '/home/pi/temp/klipper_log.sock'

# 非阻塞Unix Sock日志处理器
class UnixSocketHandler(logging.handlers.SocketHandler):
    def makeSocket(self):
        #print("connect to unix socket")
        result = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        result.setblocking(False)
        try:
            result.connect(self.address)
        except OSError:
            result.close()
            raise
        return result
def set_logging(debuglevel):
    # 配置日志格式
    #log_format = logging.Formatter('[%(asctime)s][%(levelname)s]:%(message)s\n%(exc_info)s')
    # 创建一个logger
    logger = logging.getLogger()
    logger.setLevel(debuglevel)  # 设置日志级别
    # 创建一个RemoteHandler实例
    remote_handler = UnixSocketHandler(host=SOCK_PATH,port=None)
    #remote_handler.setFormatter(log_format)
    # 将handler添加到logger
    logger.addHandler(remote_handler)
    return logger

