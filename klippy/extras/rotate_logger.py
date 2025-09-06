# Copyright (c) 2024,郑州潮阔电子科技有限公司
# All rights reserved.
# 
# 文件名称：rotate_logger.py
# 摘    要：klipper中error类信息的格式化显示：time+err_code+err_msg+operate，主要用于前端展示
# 
# 当前版本：1.0
# 作    者：李旭明
# 完成日期：2024年10月25日
#
# 修订记录：

import logging,os
import extras.flsun_warning as warning_info
from socklogger import UnixSocketHandler

SOCK_PATH = '/home/pi/temp/klipper_log.sock'

class RotatingLogger:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:shutdown",
                                            self._handle_shutdown)
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = UnixSocketHandler(host=SOCK_PATH,port=None)
        self.logger.addHandler(handler)
        logging.info("rotate logger startup finished")
    
    def _handle_shutdown(self):
        self.logger.handlers.clear()

    def _ensure_directory_exists(self):
        directory = os.path.dirname(self.filename)
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
                logging.info(f"Created directory: {directory}")
                self.initdir = True
            except OSError as e:
                logging.error(f"Failed to create directory {directory}: {e}")

        with open(self.filename, 'a'):
            pass

    def _find_key(self, message):
        warn_dict = warning_info.warning_dict
        warn_code = "99-99-999"
        for key, value in warn_dict.items():
            if value in message:  
                warn_code = key
                break 
        return warn_code

    def _msg_format(self, message, operate):
        # find err-code and  format msg
        warn_code = self._find_key(message)
        ret_msg = "  Code:" + warn_code + "  Info: " + message + "  Operate: " + operate + "   \n"
        return ret_msg

    def info(self, message, operate="None"):
        self.logger.info(self._msg_format(message, operate))
    
    def warning(self, message, operate="None"):
        self.logger.warning(self._msg_format(message, operate))
    
    def error(self, message, operate="Reboot", notify=False):
        self.logger.error(self._msg_format(message, operate))
        if notify :
            gcode =  self.printer.lookup_object('gcode')
            warn_code = self._find_key(message)
            gcode.respond_raw('error_info:%s  %s' % (warn_code, message))

def load_config(config):
    return RotatingLogger(config)