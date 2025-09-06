# Virtual sdcard support (print files directly from a host g-code file)
#
# Copyright (C) 2018  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, logging,threading
import subprocess,util #flsun add
import sys #flsun add
import importlib #flsun add
importlib.reload(sys) #flsun add
from . import gcode_file_parse
import json
#sys.setdefaultencoding('utf8') #flsun add ,add the three line to support Chinese,now don't need it because klipper use python3
VALID_GCODE_EXTS = ['gcode', 'g', 'gco']

class VirtualSD:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:shutdown",
                                            self.handle_shutdown)
        # sdcard state
        sd = config.get('path')
        self.sdcard_dirname = os.path.normpath(os.path.expanduser(sd))
        self.current_file = None
        self.file_position = self.file_size = 0
        self.power_loss_restart = 0
        self.resume_pre_gcode = None
        # first layer detect
        self.first_layer_detect = False
        self.first_layer_endpos = -1
        # Print Stat Tracking
        self.print_stats = self.printer.load_object(config, 'print_stats')
        # Work timer
        self.reactor = self.printer.get_reactor()
        self.must_pause_work = self.cmd_from_sd = False
        self.next_file_position = 0
        self.work_timer = None
        # Error handling
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.on_error_gcode = gcode_macro.load_template(
            config, 'on_error_gcode', '')
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        for cmd in ['M20', 'M21', 'M23', 'M24', 'M25', 'M26', 'M27']:
            self.gcode.register_command(cmd, getattr(self, 'cmd_' + cmd))
        for cmd in ['M28', 'M29', 'M30']:
            self.gcode.register_command(cmd, self.cmd_error)
        self.gcode.register_command(
            "SDCARD_RESET_FILE", self.cmd_SDCARD_RESET_FILE,
            desc=self.cmd_SDCARD_RESET_FILE_help)
        self.gcode.register_command(
            "SDCARD_PRINT_FILE", self.cmd_SDCARD_PRINT_FILE,
            desc=self.cmd_SDCARD_PRINT_FILE_help)
        self.gcode.register_command("POWER_LOSS_RESTART_PRINT", self.cmd_POWER_LOSS_RESTART_PRINT, #wzy add
            desc=self.cmd_POWER_LOSS_RESTART_PRINT_help)
    def handle_shutdown(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            try:
                readpos = max(self.file_position - 1024, 0)
                readcount = self.file_position - readpos
                self.current_file.seek(readpos)
                data = self.current_file.read(readcount + 128)
            except:
                logging.exception("virtual_sdcard shutdown read")
                return
            logging.info("Virtual sdcard (%d): %s\nUpcoming (%d): %s",
                         readpos, repr(data[:readcount]),
                         self.file_position, repr(data[readcount:]))
    def stats(self, eventtime):
        if self.work_timer is None:
            return False, ""
        return True, "sd_pos=%d" % (self.file_position,)
    def get_file_list(self, check_subdirs=False):
        if check_subdirs:
            flist = []
            for root, dirs, files in os.walk(
                    self.sdcard_dirname, followlinks=True):
                for name in files:
                    ext = name[name.rfind('.')+1:]
                    if ext not in VALID_GCODE_EXTS:
                        continue
                    full_path = os.path.join(root, name)
                    r_path = full_path[len(self.sdcard_dirname) + 1:]
                    size = os.path.getsize(full_path)
                    flist.append((r_path, size))
            return sorted(flist, key=lambda f: f[0].lower())
        else:
            dname = self.sdcard_dirname
            try:
                filenames = os.listdir(self.sdcard_dirname)
                return [(fname, os.path.getsize(os.path.join(dname, fname)))
                        for fname in sorted(filenames, key=str.lower)
                        if not fname.startswith('.')
                        and os.path.isfile((os.path.join(dname, fname)))]
            except:
                logging.exception("virtual_sdcard get_file_list")
                raise self.gcode.error("Unable to get file list")
    def get_status(self, eventtime):
        return {
            'file_path': self.file_path(),
            'progress': self.progress(),
            'is_active': self.is_active(),
            'file_position': self.file_position,
            'file_size': self.file_size,
        }
    def file_path(self):
        if self.current_file:
            return self.current_file.name
        return None
    def progress(self):
        if self.file_size:
            return float(self.file_position) / self.file_size
        else:
            return 0.
    def is_active(self):
        return self.work_timer is not None
    def do_pause(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            while self.work_timer is not None and not self.cmd_from_sd:
                self.reactor.pause(self.reactor.monotonic() + .001)
    def do_resume(self,resume_pre_gcode=None):
        if self.work_timer is not None:
            raise self.gcode.error("SD busy")
        self.must_pause_work = False
        self.resume_pre_gcode = resume_pre_gcode
        self.work_timer = self.reactor.register_timer(
            self.work_handler, self.reactor.NOW)
    def do_cancel(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
            self.print_stats.note_cancel()
        self.file_position = self.file_size = 0.
    # G-Code commands
    def cmd_error(self, gcmd):
        raise gcmd.error("SD write not supported")
    def _reset_file(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
        self.file_position = self.file_size = 0.
        self.print_stats.reset()
        self.printer.send_event("virtual_sdcard:reset_file")
    cmd_SDCARD_RESET_FILE_help = "Clears a loaded SD File. Stops the print "\
        "if necessary"
    def cmd_SDCARD_RESET_FILE(self, gcmd):
        if self.cmd_from_sd:
            raise gcmd.error(
                "SDCARD_RESET_FILE cannot be run from the sdcard")
        self._reset_file()
    cmd_SDCARD_PRINT_FILE_help = "Loads a SD file and starts the print.  May "\
        "include files in subdirectories."
    def cmd_SDCARD_PRINT_FILE(self, gcmd):
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        filename = gcmd.get("FILENAME")
        if filename[0] == '/':
            filename = filename[1:]
        self._load_file(gcmd, filename, check_subdirs=True)
        self.do_resume()
    def cmd_M20(self, gcmd):
        # List SD card
        files = self.get_file_list()
        gcmd.respond_raw("Begin file list")
        for fname, fsize in files:
            gcmd.respond_raw("%s %d" % (fname, fsize))
        gcmd.respond_raw("End file list")
    def cmd_M21(self, gcmd):
        # Initialize SD card
        gcmd.respond_raw("SD card ok")
    def cmd_M23(self, gcmd):
        # Select SD file
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        filename = gcmd.get_raw_command_parameters().strip()
        if filename.startswith('/'):
            filename = filename[1:]
        self._load_file(gcmd, filename)
    cmd_POWER_LOSS_RESTART_PRINT_help = "Restart print after power loss and power on" #wzy add
    def cmd_POWER_LOSS_RESTART_PRINT(self, gcmd): #wzy add
        filename = gcmd.get("FILENAME")
        fileposition = gcmd.get("FILEPOSITION")
        fname = filename[len(self.sdcard_dirname)+1:]
        print_duration = gcmd.get("PRINT_DURATION")
        self.print_stats.modify_print_time(float(print_duration))
        self._load_file(gcmd, fname, fileposition, check_subdirs=True)
        self.do_resume()
    def _load_file(self, gcmd, filename, fileposition=0, check_subdirs=False):
        files = self.get_file_list(check_subdirs)
        flist = [f[0] for f in files]
        files_by_lower = { fname.lower(): fname for fname, fsize in files }
        fname = filename
        try:
            if fname not in flist:
                fname = files_by_lower[fname.lower()]
            fname = os.path.join(self.sdcard_dirname, fname)
            f = open(fname, 'rb')
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(0)
        except:
            logging.exception("virtual_sdcard file open")
            raise gcmd.error("Unable to open file")
        gcmd.respond_raw("File opened:%s Size:%d" % (filename, fsize))
        gcmd.respond_raw("File selected")

        command = ["rm", "/home/pi/flsun_func/AI_detect/print_log.txt"]
        util.start_subprocess(command)
        command = ["rm", "/home/pi/flsun_func/AI_detect/before_print_log.txt"]
        util.start_subprocess(command)

        self.current_file = f
        self.file_position = int(fileposition) #wzy modify
        self.file_size = fsize
        self.print_stats.set_current_file(filename)
        # start print，default set power loss flag to 0
        #flsun add, run START_PRINT when start a print
        if(self.file_position == 0): #wzy modify
            self.gcode.run_script_from_command("G28")
        # get sundry detection switch 
        sundry_detect_start = False
        if self.file_position == 0:
            try:
                parser = gcode_file_parse.GCodeFileParse()
                layer_info = json.loads(parser.parse_file(fname, only_first=True))
                if layer_info and layer_info["layer_total"] >= 2:
                    self.first_layer_endpos = layer_info["info"][1]["pos"]
                else:
                    self.first_layer_endpos = -1
                logging.info("first layer endpos: %d"%self.first_layer_endpos)
            except Exception as e:
                logging.warning(f"get first layer endpos error: {e}")
                self.first_layer_endpos = -1
            try:
                self.first_layer_detect = False
                with open("/home/pi/qt/out_linux/AiConfig.ini","r") as file_op:
                    for line in file_op:
                        if "FirstDetection=true" in line and self.first_layer_endpos > 0:
                            self.first_layer_detect = True
                        if "SundryDetection=true" in line:
                            sundry_detect_start = True
            except: 
                logging.warning("get sundry detection switch error")
            # 非断电续打时，清空延时摄影目录
            command = "rm /home/pi/flsun_func/time_lapse/png/*"
            util.start_subprocess(command, shell=True)
        if sundry_detect_start and str(filename).strip() != ".test/line.gcode" and str(filename).strip() != ".test/cube.gcode":
            self.gcode.run_script_from_command("M117 start sundry detection")
            command = ["bash", "/home/pi/flsun_func/AI_detect/before_printing_run.sh"]
            util.start_subprocess(command)
        if(self.file_position == 0):
            self.power_loss_restart = 0
            self.gcode.run_script_from_command("START_PRINT")
            if sundry_detect_start:
                self.gcode.run_script_from_command("G4 P10000")
        else:
            self.power_loss_restart = 1

    def parse_string(self, ch, word): #flsun add
        start = word.index(ch) + 1 # get first ch position
        if ' ' in word[start:]:
            end = word.index(' ', start, -1)
            num_str = word[start:end]
        else:
            end = -1
            num_str = word[start:]
        num = float(num_str)
        return num
    def cmd_M24(self, gcmd):
        # Start/resume SD print
        self.do_resume()
    def cmd_M25(self, gcmd):
        # Pause SD print
        self.do_pause()
    def cmd_M26(self, gcmd):
        # Set SD position
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        pos = gcmd.get_int('S', minval=0)
        self.file_position = pos
    def cmd_M27(self, gcmd):
        # Report SD print status
        if self.current_file is None:
            gcmd.respond_raw("Not SD printing.")
            return
        gcmd.respond_raw("SD printing byte %d/%d"
                         % (self.file_position, self.file_size))
    def get_file_position(self):
        return self.next_file_position
    def set_file_position(self, pos):
        self.next_file_position = pos
    def is_cmd_from_sd(self):
        return self.cmd_from_sd
    def _pre_action(self):
        if self.power_loss_restart != 0:
            self.power_loss_restart = 0
            try:
                self.gcode.run_script("PRE_RESUME_INTERRUPTED")
            except Exception as e:
                logging.exception("resume interrupt error:%s"%str(e))
                raise
        if self.resume_pre_gcode is not None:
            try:
                self.cmd_from_sd = True
                self.gcode.run_script(self.resume_pre_gcode)
                self.resume_pre_gcode = None
                self.cmd_from_sd = False
            except Exception as e:
                logging.exception("resume error:%s"%str(e))
                self.resume_pre_gcode = None
                raise
        return
    # Background work timer
    def _thread_read_handler(self,read_data):
        try:
            logging.info("read gcode 8K")
            if self.current_file is None:
                read_data['result'] = "Error: File closed"
                return 
            read_data['data'] = self.current_file.read(8192)
            logging.info("read gcode 8K finished")
        except:
            logging.exception("virtual_sdcard read")
            read_data['result'] = "virtual_sdcard read error"
            return
    # 启动一个线程，读取文件, 在等待时长超时，切换协程，防止阻塞主线程
    def _read_data(self):
        thread_read = {"data": b"","result": None}
        read_thread_id = threading.Thread(target=self._thread_read_handler, args=(thread_read,))
        read_thread_id.start()
        while True:
            read_thread_id.join(0.01)
            if read_thread_id.is_alive():
                logging.info("Read thread is still alive")
                self.reactor.pause(self.reactor.monotonic() + 0.01)
                continue
            else:
                break
            
        if thread_read['result']:
            raise thread_read['result']
        logging.info("Read thread over,get data")

        return thread_read['data']
    def work_handler(self, eventtime):
        has_pre_check = False
        logging.info("Starting SD card print (position %d)", self.file_position)
        self.reactor.unregister_timer(self.work_timer)
        try:
            self.current_file.seek(self.file_position)
        except:
            logging.exception("virtual_sdcard seek")
            self.work_timer = None
            self.power_loss_restart = 0
            return self.reactor.NEVER
        self.print_stats.note_start()

        # 打印前的准备动作
        try:
            self._pre_action()
        except Exception as e:
            self.work_timer = None
            self.cmd_from_sd = False
            self.print_stats.note_error(str(e))
            return self.reactor.NEVER

        gcode_mutex = self.gcode.get_mutex()
        partial_input = b""
        lines = []
        error_message = None
        while not self.must_pause_work:
            if not lines:
                # Read more data
                try:
                    data = self._read_data()
                except Exception as e:
                    logging.exception("virtual_sdcard read,err:%s"%str(e))
                    error_message = str(e)
                    break
                if not data:
                    # End of file
                    self.current_file.close()
                    self.current_file = None
                    logging.info("Finished SD card print")
                    self.gcode.respond_raw("Done printing file")
                    break
                lines = data.split(b'\n')
                lines[0] = partial_input + lines[0]
                partial_input = lines.pop()
                lines.reverse()
                self.reactor.pause(self.reactor.NOW)
                continue
            # Pause if any other request is pending in the gcode class
            if gcode_mutex.test():
                self.reactor.pause(self.reactor.monotonic() + 0.100)
                continue
            # Dispatch command
            self.cmd_from_sd = True
            line = lines.pop()
            next_file_position = self.file_position + len(line) + 1
            self.next_file_position = next_file_position
            try:
                if not has_pre_check:
                    has_pre_check = True
                    #filament check
                    filament_sensor = self.printer.lookup_object("filament_switch_sensor filament_sensor")
                    runout_helper = filament_sensor.runout_helper
                    if runout_helper.check_to_pause():
                        break
                    motion_sensor = self.printer.lookup_object("filament_motion_sensor my_sensor")
                    motion_sensor.recover_motion_check(self.reactor.monotonic()) # 恢复堵料检测
                self.gcode.run_script(line.decode('utf-8'))
                if next_file_position == self.first_layer_endpos:
                    # 修改电流
                    self.gcode.run_script("RESTORE_E_CURRENT")
                    if self.first_layer_detect:
                        #self.gcode.run_script("PAUSE")
                        #subprocess.Popen(["bash", "/home/pi/flsun_func/Structured_light/move_model.sh"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) #flsun add
                        self.first_layer_detect = False
                        break
            except self.gcode.error as e:
                error_message = str(e)
                if self.printer.power_loss_occur:
                    # if power loss occur, pause current greenlet and schedule other greenlet
                    self.reactor.pause(self.reactor.NEVER)
                try:
                    self.gcode.run_script(self.on_error_gcode.render())
                except:
                    logging.exception("virtual_sdcard on_error")
                break
            except:
                logging.exception("virtual_sdcard dispatch")
                break
            self.cmd_from_sd = False
            self.file_position = self.next_file_position
            # Do we need to skip around?
            if self.next_file_position != next_file_position:
                try:
                    self.current_file.seek(self.file_position)
                except:
                    logging.exception("virtual_sdcard seek")
                    self.work_timer = None
                    return self.reactor.NEVER
                lines = []
                partial_input = ""
        logging.info("Exiting SD card print (position %d)", self.file_position)
        self.work_timer = None
        self.cmd_from_sd = False
        if error_message is not None:
            self.print_stats.note_error(error_message)
        elif self.current_file is not None:
            self.print_stats.note_pause()
        else:
            self.print_stats.note_complete()
            #flsun add, run END_PRINT when end a print
            self.gcode.run_script_from_command("END_PRINT")

        return self.reactor.NEVER

def load_config(config):
    return VirtualSD(config)
