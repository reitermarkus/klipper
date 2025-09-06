import socket, select, logging, os, time, queue
import threading, struct, pickle, sys, signal
from logging.handlers import TimedRotatingFileHandler, RotatingFileHandler
from contextlib import closing

# 定义Unix域套接字路径和日志文件路径
SOCK_PATH = '/home/pi/temp/klipper_log.sock'
MAIN_LOG_FILE = '/home/pi/printer_data/logs/klippy.log'
ERRORCODE_LOG_FILE = '/home/pi/klipper_logs/mylog.txt'

# 创建日志记录器并配置日志
def create_main_logger():
    # 主日志配置
    main_logger = logging.getLogger()
    main_logger.setLevel(logging.INFO)
    
    main_file_handler = TimedRotatingFileHandler(MAIN_LOG_FILE, when='midnight', interval=1, backupCount=3)
    main_formatter = logging.Formatter('[%(asctime)s][%(levelname)s] %(message)s')
    main_file_handler.setFormatter(main_formatter)
    main_logger.addHandler(main_file_handler)

    return main_logger

def create_errcode_logger():
    # 错误码日志配置
    errcode_logger = logging.getLogger('mylog')
    errcode_logger.setLevel(logging.INFO)
    
    errcode_file_handler = RotatingFileHandler(ERRORCODE_LOG_FILE, maxBytes=1024 * 1024, backupCount=3)
    errcode_formatter = logging.Formatter('Time: %(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    errcode_file_handler.setFormatter(errcode_formatter)
    errcode_logger.addHandler(errcode_file_handler)

    with open(ERRORCODE_LOG_FILE,"a"):
        pass

    return errcode_logger

# 接收并解析数据
def data_handle(sock):
    try:
        chunk = sock.recv(4)
        if len(chunk) < 4:
            return None
        slen = struct.unpack('>L', chunk)[0]
        data = b''
        while len(data) < slen:
            data += sock.recv(min(slen - len(data), 1024))
        obj = pickle.loads(data)
        return logging.makeLogRecord(obj)
    except (struct.error, pickle.UnpicklingError, EOFError) as e:
        logging.error(f"Error handling data: {e}")
        return None

# 处理日志记录
def record_handle(msg_queue, main_logger, errcode_logger):
    while True:
        try:
            record = msg_queue.get(True)
            if record is None:
                break
            logger = main_logger if record.name == 'root' else errcode_logger
            if record.name == 'root':
                msg = record.msg
                if record.exc_text is not None:
                    msg = record.msg + '\n' +record.exc_text
                logger.log(record.levelno, msg)
            else:
                logger.log(record.levelno, record.msg)
        except Exception as e:
            logging.error(f"Error in record_handle: {e}")

def handle_system_exit(signum,frame):
    logging.info("log server recv system exit signal")
    time.sleep(2)
    global exit_flag
    exit_flag = True
    logging.info("log server do system exit")
    # 关闭日志记录器
    logging.shutdown()
    sys.exit(0)
# 启动服务器
def start_server():
    main_logger = create_main_logger()
    errcode_logger = create_errcode_logger()
    
    # 删除已存在的套接字文件（如果存在）
    if os.path.exists(SOCK_PATH):
        os.remove(SOCK_PATH)

    with closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as server:
        server.bind(SOCK_PATH)
        server.listen(2)
        main_logger.info(f'Server listening on {SOCK_PATH}')

        # 启动日志处理线程
        msg_queue = queue.Queue(maxsize=100)
        thread = threading.Thread(target=record_handle, args=(msg_queue, main_logger, errcode_logger))
        thread.daemon = True
        thread.start()

        sockets = [server]

        while not exit_flag:
            readable, _, _ = select.select(sockets, [], [])
            for sock in readable:
                if sock == server:
                    # 接受新的连接
                    client_socket, addr = server.accept()
                    sockets.append(client_socket)
                    main_logger.info(f'Connection')
                else:
                    # 处理已连接的客户端
                    try:
                        data = data_handle(sock)
                        if data:
                            msg_queue.put_nowait(data)
                        else:
                            # 客户端断开连接
                            main_logger.info(f'Connection closed')
                            sock.close()
                            sockets.remove(sock)
                    except queue.Full:
                        main_logger.error("Message queue is full")
                    except Exception as e:
                        main_logger.error(f"Error handling connection: {e}")
                        sock.close()
                        sockets.remove(sock)

exit_flag = False

if __name__ == "__main__":
    #signal.signal(signal.SIGTERM, handle_system_exit)
    start_server()
