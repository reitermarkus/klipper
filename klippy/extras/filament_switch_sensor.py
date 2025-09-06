# Generic Filament Sensor Module
#
# Copyright (C) 2019  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import chelper #wzy add
import mcu  #wzy add
from . import homing #wzy add

class RunoutHelper:
    def __init__(self, config):
        self.name = config.get_name().split()[-1]
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        if self.name == "power_loss":
            self.mcu = self.printer.lookup_object('mcu') #wzy add
            ffi_main, ffi_lib = chelper.get_ffi() #wzy add
            self._trdispatch = ffi_main.gc(ffi_lib.trdispatch_alloc(), ffi_lib.free) #wzy add
            self._trsyncs = [] #wzy add
            self.gcode.register_command(    #wzy add
            'STEPPER_STOP',self.cmd_STEPPER_STOP,
            desc=self.cmd_STEPPER_STOP_help)

        # Read config
        self.runout_pause = config.getboolean('pause_on_runout', True)
        if self.runout_pause:
            self.printer.load_object(config, 'pause_resume')
        self.runout_gcode = self.insert_gcode = None
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        if self.runout_pause or config.get('runout_gcode', None) is not None:
            self.runout_gcode = gcode_macro.load_template(
                config, 'runout_gcode', '')
        if config.get('insert_gcode', None) is not None:
            self.insert_gcode = gcode_macro.load_template(
                config, 'insert_gcode')
        self.on_disable_gcode = None
        if config.get('on_disable_gcode', None) is not None:
            self.on_disable_gcode = gcode_macro.load_template(
                config, 'on_disable_gcode')
        self.switch_off_gcode = None
        if config.get('switch_off_gcode', None) is not None:
            self.switch_off_gcode = gcode_macro.load_template(
                config, 'switch_off_gcode')
        self.pause_delay = config.getfloat('pause_delay', .5, above=.0)
        self.event_delay = config.getfloat('event_delay', 3., above=0.)
        # Internal state
        self.min_event_systime = self.reactor.NEVER
        self.filament_present = False
        self.sensor_enabled = True
        # Register commands and event handlers
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler('klippy:mcu_identify',
                                            self._handle_mcu_identify) #wzy add
        self.gcode.register_mux_command(
            "QUERY_FILAMENT_SENSOR", "SENSOR", self.name,
            self.cmd_QUERY_FILAMENT_SENSOR,
            desc=self.cmd_QUERY_FILAMENT_SENSOR_help)
        self.gcode.register_mux_command(
            "SET_FILAMENT_SENSOR", "SENSOR", self.name,
            self.cmd_SET_FILAMENT_SENSOR,
            desc=self.cmd_SET_FILAMENT_SENSOR_help)
    def _handle_ready(self):
        self.min_event_systime = self.reactor.monotonic() + 2.
    def _handle_mcu_identify(self): #wzy add
        if self.name == "power_loss":
            kin = self.printer.lookup_object('toolhead').get_kinematics() #wzy add
            for stepper in kin.get_steppers(): #wzy add
                self.add_stepper(stepper) #wzy add
            extruder = self.printer.lookup_object('toolhead').get_extruder()
            stepper_ext = extruder.extruder_stepper.stepper
            self.add_stepper(stepper_ext)         
    def _runout_event_handler(self, eventtime):
        # Pausing from inside an event requires that the pause portion
        # of pause_resume execute immediately.
        pause_prefix = ""
        if self.runout_pause:
            pause_resume = self.printer.lookup_object('pause_resume')
            pause_resume.send_pause_command()
            pause_prefix = "PAUSE\n"
            self.printer.get_reactor().pause(eventtime + self.pause_delay)
        self._exec_gcode(pause_prefix, self.runout_gcode)
    def _insert_event_handler(self, eventtime):
        self._exec_gcode("", self.insert_gcode)
    def _on_disable_handler(self, eventtime):
        self._exec_gcode("", self.on_disable_gcode)
    def _switch_off_handler(self, eventtime):
        self._exec_gcode("", self.switch_off_gcode)
    def _exec_gcode(self, prefix, template):
        try:
            if template is not None:
                self.gcode.run_script(prefix + template.render() + "\nM400")
            if self.name == "power_loss":
                self.printer.power_loss_occur = False
                self.printer.power_loss_processing = False #标记断电处理流程已完成
        except Exception:
            logging.exception("Script running error")
        self.min_event_systime = self.reactor.monotonic() + self.event_delay
    def note_filament_present(self, is_filament_present, lazy=True):
        if lazy and is_filament_present == self.filament_present:
            return
        self.filament_present = is_filament_present
        eventtime = self.reactor.monotonic()
        if eventtime < self.min_event_systime:
            # do not process during the initialization time, duplicates,
            # during the event delay time, while an event is running
            return
        # when the sensor is disabled
        if not self.sensor_enabled:
            if self.on_disable_gcode is not None:
                self.min_event_systime = self.reactor.NEVER
                logging.info(
                    "Filament Sensor %s: do sensor_disable gcode, Time %.2f" %
                    (self.name, eventtime))
                self.reactor.register_callback(self._on_disable_handler)
            return
        # Determine "printing" status
        idle_timeout = self.printer.lookup_object("idle_timeout")
        is_printing = idle_timeout.get_status(eventtime)["state"] == "Printing"
        # Perform filament action associated with status change (if any)
        if is_filament_present:
            if self.insert_gcode is not None:
                # insert detected
                self.min_event_systime = self.reactor.NEVER
                logging.info(
                    "Filament Sensor %s: insert event detected, Time %.2f" %
                    (self.name, eventtime))
                self.reactor.register_callback(self._insert_event_handler)
        elif is_printing and self.runout_gcode is not None:
            # runout detected
            self.min_event_systime = self.reactor.NEVER
            logging.info(
                "Filament Sensor %s: runout event detected, Time %.2f" %
                (self.name, eventtime))
            self.reactor.register_callback(self._runout_event_handler)
    def get_status(self, eventtime):
        return {
            "filament_detected": bool(self.filament_present),
            "enabled": bool(self.sensor_enabled)}
    def stats(self,eventtime):
        return False,"filament_sensor:name=%s,enabled=%s,detected=%s"% \
                    (self.name, bool(self.sensor_enabled), bool(self.filament_present))
    # Check if a pause is needed;
    def check_to_pause(self, need_pause=True):
        if self.sensor_enabled and not self.filament_present:
            if need_pause:
                self.gcode.run_script("PAUSE")
                self.gcode.respond_raw("Filament Runout Detected!")
                logging.warning("Filament Runout Detected!")
            return True
        return False
    cmd_QUERY_FILAMENT_SENSOR_help = "Query the status of the Filament Sensor"
    def cmd_QUERY_FILAMENT_SENSOR(self, gcmd):
        if self.filament_present:
            msg = "Filament Sensor %s: filament detected" % (self.name)
        else:
            msg = "Filament Sensor %s: filament not detected" % (self.name)
        gcmd.respond_info(msg)
    cmd_SET_FILAMENT_SENSOR_help = "Sets the filament sensor on/off"
    def cmd_SET_FILAMENT_SENSOR(self, gcmd):
        self.sensor_enabled = gcmd.get_int("ENABLE", 1)
        if not bool(self.sensor_enabled):
            self.reactor.register_callback(self._switch_off_handler)
        else:
            self.note_filament_present(self.filament_present,lazy=False)
    def add_stepper(self, stepper): #wzy add
        trsyncs = {trsync.get_mcu(): trsync for trsync in self._trsyncs}
        trsync = trsyncs.get(stepper.get_mcu())
        if trsync is None:
            trsync = mcu.MCU_trsync(stepper.get_mcu(),self._trdispatch)
            self._trsyncs.append(trsync)
            logging.info("add_stepper,oid:%s" % (trsync._oid))
        trsync.add_stepper(stepper)
        # Check for unsupported multi-mcu shared stepper rails
        sname = stepper.get_name()
        if sname.startswith('stepper_'):
            for ot in self._trsyncs:
                for s in ot.get_steppers():
                    if ot is not trsync and s.get_name().startswith(sname[:9]):
                        cerror = self._mcu.get_printer().config_error
                        raise cerror("Multi-mcu homing not supported on"
                                     " multi-mcu shared axis")
    cmd_STEPPER_STOP_help = "Stop the stepper by send commands to the mcu" #wzy add
    def cmd_STEPPER_STOP(self,gcmd): #wzy add
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.move_queue.reset()
        fan_state = self.printer.lookup_object('fan')
        fan_state.fan.set_speed_from_command(0.)
        heater_state = self.printer.lookup_object('heaters')
        heater_state.turn_off_all_heaters()     
        print_time = toolhead.get_last_move_time() 
        expire_timeout = 0.025
        for trsync in self._trsyncs:
            trsync.start(print_time, None, expire_timeout)
        etrsync = self._trsyncs[0]
        ffi_main, ffi_lib = chelper.get_ffi()
        ffi_lib.trdispatch_start(self._trdispatch, etrsync.REASON_HOST_REQUEST)
        ffi_main, ffi_lib = chelper.get_ffi()
        ffi_lib.trdispatch_stop(self._trdispatch)
        for trsync in self._trsyncs:
            trsync.stop()      
        homing_state = homing.Homing(self.printer)
        kin = toolhead.get_kinematics()
        kin.rails[0].homing_speed = 300
        self.printer.power_loss_occur = False # clear power loss flag before homing
        try:
            kin.home(homing_state)
        except self.printer.command_error:
            if self.printer.is_shutdown():
                raise self.printer.command_error(
                    "Homing failed due to printer shutdown")
            self.printer.lookup_object('stepper_enable').motor_off()
            raise

class SwitchSensor:
    def __init__(self, config):
        printer = config.get_printer()
        buttons = printer.load_object(config, 'buttons')
        switch_pin = config.get('switch_pin')
        buttons.register_buttons([switch_pin], self._button_handler)
        self.runout_helper = RunoutHelper(config)
        self.get_status = self.runout_helper.get_status
        self.stats = self.runout_helper.stats
    def _button_handler(self, eventtime, state):
        self.runout_helper.note_filament_present(state)

def load_config_prefix(config):
    return SwitchSensor(config)
