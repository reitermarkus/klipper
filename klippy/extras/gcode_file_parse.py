# Copyright (c) 2025,郑州潮阔电子科技有限公司
# All rights reserved.
# 
# 文件名称：gcode_file_parse.py
# 摘    要：gcode文件解析模块，解析层高及换层，使用方法如下
#    parser = GCodeFileParse()
#    jsondata = parser.parse_file('test.gcode')
#    layer = GCodeFileLayerInfo()
#    layer.import_by_json(jsondata)
#    layer_info = layer.get_layer_info_by_pos(2473769)
#    print(f"========{layer_info}")
# 测试输出结果示例：
#    $ python3 gcode_file_parse_test.py 
#    ========{'layer': 106, 'height': 0.25, 'print': 26.6, 'pos': 2473767}
# 
# 当前版本：1.0
# 作    者：郭夫华
# 完成日期：2025年01月10日
#
# 修订记录：

import os
import logging
import sys
import importlib
import re
import json
from collections import deque
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gcode import CommandError  # 假设类在该模块中
from gcode import GCodeCommand  # 假设类在该模块中
importlib.reload(sys)
VALID_GCODE_EXTS = ['gcode', 'g', 'gco']

# Parse and dispatch G-Code commands
class GCodeFileParseDispatch:
    error = CommandError
    def __init__(self):
        self.gcode_handlers = {}
        self.status_commands = {}

    def register_command(self, cmd, func):
        if func is None:
            old_cmd = self.gcode_handlers.get(cmd)
            if cmd in self.gcode_handlers:
                del self.gcode_handlers[cmd]
            self._build_status_commands()
            return old_cmd
        if cmd in self.gcode_handlers:
            raise self.error("gcode command %s already registered" % (cmd,))
        self.gcode_handlers[cmd] = func
        self._build_status_commands()
    def get_status(self, eventtime):
        return {'commands': self.status_commands}
    def _build_status_commands(self):
        commands = {cmd: {} for cmd in self.gcode_handlers}
        self.status_commands = commands

    # Parse input into commands
    args_r = re.compile('([A-Z_]+|[A-Z*/])')
    def _process_commands(self, commands, need_ack=True):
        for line in commands:
            # Ignore comments and leading/trailing spaces
            line = origline = line.strip()
            cpos = line.find(';')
            if cpos >= 0:
                line = line[:cpos]
            # Break line into parts and determine command
            parts = self.args_r.split(line.upper())
            numparts = len(parts)
            cmd = ""
            if numparts >= 3 and parts[1] != 'N':
                cmd = parts[1] + parts[2].strip()
            elif numparts >= 5 and parts[1] == 'N':
                # Skip line number at start of command
                cmd = parts[3] + parts[4].strip()
            # Build gcode "params" dictionary
            params = { parts[i]: parts[i+1].strip()
                       for i in range(1, numparts, 2) }
            gcmd = GCodeCommand(self, cmd, origline, params, need_ack)
            # Invoke handler for command
            handler = self.gcode_handlers.get(cmd, self.cmd_default)
            try:
                handler(gcmd)
            except self.error as e:
                print(str(e))
                if not need_ack:
                    raise
            except:
                msg = 'Internal error on command:"%s"' % (cmd,)
                print(msg)
                if not need_ack:
                    raise
    def run_script(self, script):
        self._process_commands(script.split('\n'), need_ack=False)

    def cmd_default(self, gcmd):
        pass
        # cmd = gcmd.get_command()
        # print('Unknown command:"%s"' % (cmd,))

    # Response handling
    def respond_raw(self, msg):
        # print(msg)
        pass
    def respond_info(self, msg, log=True):
        lines = [l.strip() for l in msg.strip().split('\n')]
        self.respond_raw("// " + "\n// ".join(lines))

class GCodeFileLayerInfo:
    def __init__(self):
        # 创建一个无限大小的 deque
        self.queue = deque(maxlen=None)
        self.parseinfo = dict()
    def import_by_json(self, json_data):
        data = json.loads(json_data)
        self.parseinfo = data
    def get_layer_info_by_pos(self, file_position):
        infolist = self.parseinfo.get('info', [])
        last_entry = None
        for entry in infolist:
            if entry.get('pos') > file_position:
                return last_entry 
            last_entry = entry
        return last_entry

class GCodeFileInfo:
    def __init__(self):
        self.file_position = 0
        self.exist_G3 = False
        self._reset()

    def add_line(self, line, file_position, start_pos, end_pos):
        axes_d = [end_pos[i] - start_pos[i] for i in (0, 1, 2, 3)]
        self.file_position = file_position

        if axes_d[2] > 0:
            # 层高变化，进行标记
            self.height_change = end_pos[2] - self.current_height
            self.real_height = end_pos[2]
            # print(f"{self._get_info()}, [ZUP]{start_pos} --> {axes_d} = {end_pos}")
        elif axes_d[2] < 0:
            self.height_change = end_pos[2] - self.current_height
            self.real_height = end_pos[2]
            # print(f"{self._get_info()}, [ZDOWN]{start_pos} --> {axes_d} = {end_pos}")
        
        if axes_d[3] > 0:
            # 层高变化后，存在挤出，则换层
            if self._z_change():
                self._layer_add()
                # print(f"z_change {self._get_info()}, [Extrude]{start_pos} --> {axes_d} = {end_pos}")
            elif self.layer_total == 0:
                self._layer_add()
                # print(f"{self._get_info()}, [Extrude2]{start_pos} --> {axes_d} = {end_pos}")

        # 处理首个G3弧线
        self._check_G3()

    def set_G3(self, is_G3):
        self.move_G3 = is_G3

    # 因自己的切片软件会在所有打印前，划一段1/4圆弧来擦嘴，导致层高后续有变换，
    # 所以编写方法排除擦嘴的G3代码影响
    def _check_G3(self):
        # 当前移动是G3，且当前是第一层，则在G3移动后，清除层高等信息，从G3命令后开始计算
        if self.move_G3:
            #只处理第一个G3
            if 1 == self.layer_total and not self.exist_G3:
                self._reset()
            self.exist_G3 = True
        self.move_G3 = False

    def _z_change(self):
        return self.height_change > 0.0001
    
    def _layer_add(self):
        self.layer_total += 1
        self.current_layer += 1
        self.layer_height = self.real_height - self.current_height
        self.current_height += self.layer_height
        self.height_change = 0.0
        # 每层信息记录列表
        self.parseinfo["layer"] = self.current_layer
        self.parseinfo["height"] = round(self.layer_height, 5)
        self.parseinfo["print"] = round(self.current_height, 5)
        self.parseinfo["pos"] = self.file_position
        self.queue.append(self.parseinfo)
        self.parseinfo = {}
        #print("layer_add:queue_len:%d,layer_total:%d,current_layer:%d"%(len(self.queue),self.layer_total,self.current_layer))

    def _reset(self):
        self.layer_total = 0
        self.current_layer = 0
        self.layer_height = 0.0
        self.current_height = 0.0
        self.real_height = 0.0
        self.height_change = 0.0
        self.move_G3 = False
        # 创建一个无限大小的 deque
        self.queue = deque(maxlen=None)
        self.parseinfo = dict()
        #print("reset")

    def _get_info(self):
        return (f"layer:{self.current_layer}/{self.layer_total}, layer_height:{self.layer_height}, current_height:{self.current_height}, pos:{self.file_position}"
                f", real:{self.real_height}, change:{self.height_change}")
    
    # 获取解析信息
    def get_layer_info(self):
        parseinfo = dict()
        parseinfo["exist_G3"] = self.exist_G3
        parseinfo["layer_total"] = self.layer_total
        parseinfo["info"] = list(self.queue)

        # return parseinfo
        return json.dumps(parseinfo)


class GCodeParseFileCommand():
    def __init__(self, gcode, layer_info):
        self.gcode = gcode
        self.layer_info = layer_info
        self.absolute_coord = self.absolute_extrude = True
        self.base_position = [0.0, 0.0, 0.0, 0.0]
        self.last_position = [0.0, 0.0, 0.0, 0.0]
        gcode.register_command('G1', self.cmd_G1)
        gcode.register_command("G2", self.cmd_G2)
        gcode.register_command("G3", self.cmd_G3)
        gcode.register_command('G20', self.cmd_G20)
        gcode.register_command('G21', self.cmd_G21)
        gcode.register_command('G90', self.cmd_G90)
        gcode.register_command('G91', self.cmd_G91)
        gcode.register_command('G92', self.cmd_G92)
        gcode.register_command('M82', self.cmd_M82)
        gcode.register_command('M83', self.cmd_M83)
        gcode.register_command('M220', self.cmd_M220)
        gcode.register_command('M221', self.cmd_M221)

    def cmd_G1(self, gcmd):
        params = gcmd.get_command_parameters()
        try:
            for pos, axis in enumerate('XYZ'):
                if axis in params:
                    v = float(params[axis])
                    if not self.absolute_coord:
                        # value relative to position of last move
                        self.last_position[pos] += v
                    else:
                        # value relative to base coordinate position
                        self.last_position[pos] = v + self.base_position[pos]
            if 'E' in params:
                v = float(params['E'])
                if not self.absolute_coord or not self.absolute_extrude:
                    # value relative to position of last move
                    self.last_position[3] += v
                else:
                    # value relative to base coordinate position
                    self.last_position[3] = v + self.base_position[3]
        except ValueError as e:
            raise gcmd.error("Unable to parse move '%s'"
                             % (gcmd.get_commandline(),))
    def cmd_G20(self, gcmd):
        raise gcmd.error('Machine does not support G20 (inches) command')
    def cmd_G21(self, gcmd):
        pass
    def cmd_M82(self, gcmd):
        # print(f"M82")
        # Use absolute distances for extrusion
        self.absolute_extrude = True
    def cmd_M83(self, gcmd):
        # print(f"M83")
        # Use relative distances for extrusion
        self.absolute_extrude = False
    def cmd_G90(self, gcmd):
        # print(f"G90")
        # Use absolute coordinates
        self.absolute_coord = True
    def cmd_G91(self, gcmd):
        # print(f"G91")
        # Use relative coordinates
        self.absolute_coord = False 
    def cmd_G92(self, gcmd):
        # Set position
        offsets = [ gcmd.get_float(a, None) for a in 'XYZE' ]
        # print(f"G92 {offsets}, base:{self.base_position}, last:{self.last_position}")
        for i, offset in enumerate(offsets):
            if offset is not None:
                self.base_position[i] = self.last_position[i] - offset
        if offsets == [None, None, None, None]:
            self.base_position = list(self.last_position)
        # print(f"G92 {offsets}, base:{self.base_position}, last:{self.last_position}")
    def cmd_M114(self, gcmd):
        # Get Current Position
        pass
    def cmd_M220(self, gcmd):
        # Set speed factor override percentage
        pass
    def cmd_M221(self, gcmd):
        # Set extrude factor override percentage
        pass
    def cmd_G2(self, gcmd):
        self._cmd_inner(gcmd, True)

    def cmd_G3(self, gcmd):
        self._cmd_inner(gcmd, False)
        self.layer_info.set_G3(True)
    def _cmd_inner(self, gcmd, clockwise):
        if not self.absolute_coord:
            raise gcmd.error("G2/G3 does not support relative move mode")
        currentPos = self.last_position

        # 计算当前的挤出机位置
        e_base = self.base_position[3]
        if not self.absolute_extrude:
            e_base = self.last_position[3]
        # 计算命令执行后挤出机位置
        asE = gcmd.get_float("E", 0)
        e_target = e_base + asE

        # 生成目标位置
        asTarget = [gcmd.get_float("X", currentPos[0]),
                    gcmd.get_float("Y", currentPos[1]),
                    gcmd.get_float("Z", currentPos[2]),
                    e_target]
        self.last_position = asTarget

    def get_gcode_position(self):
        return tuple(self.last_position)

class GCodeFileParse:
    def __init__(self):
        self.sdcard_dirname = os.getcwd()
        self.current_file = None
        self.file_position = self.file_size = 0
        self.next_file_position = 0
        self.gcode = GCodeFileParseDispatch()
        self.info = GCodeFileInfo()
        self.parse = GCodeParseFileCommand(self.gcode, self.info)

    def get_status(self, eventtime):
        return {
            'file_path': self.file_path(),
            'progress': self.progress(),
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
    cmd_SDCARD_PRINT_FILE_help = "Loads a SD file and starts the print.  May "\
        "include files in subdirectories."
    def parse_file(self, filename, only_first = False):
        fname = filename
        try:
            f = open(fname, 'rb')
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(0)
        except:
            logging.exception("virtual_sdcard file open")
            raise Exception("Unable to open file")
        self.current_file = f
        self.file_position = int(0)
        self.file_size = fsize
        self._parse_file_line(only_first=only_first)
        return self.info.get_layer_info()

    # Background work timer
    def _parse_file_line(self, only_first = False):
        partial_input = b""
        lines = []
        while True:
            if not lines:
                # Read more data
                try:
                    data = self.current_file.read(8192)
                except:
                    # print("virtual_sdcard read")
                    break
                if not data:
                    # End of file
                    self.current_file.close()
                    self.current_file = None
                    # print("Finished SD card print")
                    self.gcode.respond_raw("Done printing file")
                    break
                lines = data.split(b'\n')
                lines[0] = partial_input + lines[0]
                partial_input = lines.pop()
                lines.reverse()
                continue

            line = lines.pop()
            next_file_position = self.file_position + len(line) + 1
            self.next_file_position = next_file_position
            try:
                start_pos = self.parse.get_gcode_position()
                self.gcode.run_script(line.decode('utf-8'))
                end_pos = self.parse.get_gcode_position()
                self.info.add_line(line, self.file_position, start_pos, end_pos)
                if only_first and len(self.info.queue) >= 2:
                    break
            except:
                # print("virtual_sdcard dispatch")
                break
            self.file_position = self.next_file_position
        # print(f"Exiting SD card print (position {self.file_position})")
        # print(f"{self.get_status(0)}, {self.parse.get_gcode_position()}")
        # print(f"{self.info.get_layer_info()}")
        return 
