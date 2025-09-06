# Save arbitrary variables so that values can be kept across restarts.
#
# Copyright (C) 2020 Dushyant Ahuja <dusht.ahuja@gmail.com>
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, logging, ast, configparser
import subprocess
import util

class SaveVariables:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.filename = os.path.expanduser(config.get('filename'))
        self.tempfile = os.path.expanduser(config.get('tempfile'))
        self.allVariables = {}
        try:
            self.loadVariables()
        except self.printer.command_error as e:
            raise config.error(str(e))
        try:
            with open(self.filename, 'a') : pass
            with open(self.tempfile, 'a') : pass
        except Exception as e:
            raise config.error(str(e))
        self.printer.register_event_handler("klippy:system_exit", self.handler_system_exit)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('SAVE_VARIABLE', self.cmd_SAVE_VARIABLE,
                               desc=self.cmd_SAVE_VARIABLE_help)
        gcode.register_command('F112', self.cmd_F112)
    def loadVariables(self):
        allvars = {}
        varfile = configparser.ConfigParser()
        try:
            if os.path.exists(self.tempfile):
                varfile.read(self.tempfile)
            else:
                varfile.read(self.filename)
                try:
                    f = open(self.tempfile, "w")
                    varfile.write(f)
                    f.close()
                except:
                    msg = "Unable to save variable to file:%s"%self.tempfile
                    logging.exception(msg)
                    raise self.printer.command_error(msg)
            if varfile.has_section('Variables'):
                for name, val in varfile.items('Variables'):
                    allvars[name] = ast.literal_eval(val)
            logging.info("loadVariables:%s"%allvars)
        except:
            msg = "Unable to parse existing variable file"
            logging.exception(msg)
            raise self.printer.command_error(msg)
        # if allvars is not empty,update allVariables
        if allvars:
            self.allVariables = allvars
    cmd_SAVE_VARIABLE_help = "Save arbitrary variables to disk"
    def cmd_SAVE_VARIABLE(self, gcmd):
        varname = gcmd.get('VARIABLE')
        value = gcmd.get('VALUE')
        try:
            value = ast.literal_eval(value)
        except ValueError as e:
            raise gcmd.error("Unable to parse '%s' as a literal" % (value,))
        newvars = dict(self.allVariables)
        newvars[varname] = value
        logging.debug("newvars:%s"%newvars)
        # Write file
        varfile = configparser.ConfigParser()
        varfile.add_section('Variables')
        for name, val in sorted(newvars.items()):
            varfile.set('Variables', name, repr(val).replace('%', "%%"))
        try:
            f = open(self.tempfile, "w")
            varfile.write(f)
            f.close()
        except:
            msg = "Unable to save variable"
            logging.exception(msg)
            raise gcmd.error(msg)
        gcmd.respond_info("Variable Saved:%s = %s"%(varname,value))
        self.allVariables = newvars
        #self.loadVariables()
    def save_to_disk(self, wait_ret=False):
        try:
            cmd_str = "cp " + self.tempfile + ' ' + self.filename + " && sync"
            logging.info("exec cmd: %s"%cmd_str)
            if wait_ret:
                subprocess.check_call(cmd_str, shell=True, preexec_fn=util.set_cpu_affinity)
            else:
                subprocess.Popen(cmd_str, shell=True, preexec_fn=util.set_cpu_affinity)
        except Exception as e:
            logging.error(str(e))
    def cmd_F112(self,gcmd):
        self.save_to_disk()
    def handler_system_exit(self):
        self.save_to_disk(wait_ret=True)
    def get_status(self, eventtime):
        return {'variables': self.allVariables}

def load_config(config):
    return SaveVariables(config)
