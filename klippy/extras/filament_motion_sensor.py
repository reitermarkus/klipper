# Filament Motion Sensor Module
#
# Copyright (C) 2021 Joshua Wherrett <thejoshw.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import filament_switch_sensor

CHECK_RUNOUT_TIMEOUT = .250

class EncoderSensor:
    def __init__(self, config):
        # Read config
        self.printer = config.get_printer()
        switch_pin = config.get('switch_pin')
        self.extruder_name = config.get('extruder')
        self.detection_length = config.getfloat(
                'detection_length', 7., above=0.)
        # Configure pins
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons([switch_pin], self.encoder_event)
        # Get printer objects
        self.reactor = self.printer.get_reactor()
        self.runout_helper = filament_switch_sensor.RunoutHelper(config)
        self.get_status = self.runout_helper.get_status
        self.stats = self.runout_helper.stats
        self.extruder = None
        self.estimated_print_time = None
        # Initialise internal state
        self.filament_runout_pos = None
        self.filament_len = 0 #flsun test
        gcode = self.printer.lookup_object('gcode') #flsun test
        gcode.register_command('SET_FILAMENT_NUM_ZERO', self.cmd_SET_FILAMENT_NUM_ZERO)
        gcode.register_command('GET_FILAMENT_NUM', self.cmd_GET_FILAMENT_NUM)
        # Register commands and event handlers
        self.printer.register_event_handler('klippy:ready',
                self._handle_ready)
        self.printer.register_event_handler('idle_timeout:printing',
                self._handle_printing)
        self.printer.register_event_handler('idle_timeout:ready',
                self._handle_not_printing)
        self.printer.register_event_handler('idle_timeout:idle',
                self._handle_not_printing)
    def _update_filament_runout_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        self.filament_runout_pos = (
                self._get_extruder_pos(eventtime) +
                self.detection_length)
    def _handle_ready(self):
        self.extruder = self.printer.lookup_object(self.extruder_name)
        self.estimated_print_time = (
                self.printer.lookup_object('mcu').estimated_print_time)
        self._update_filament_runout_pos()
        self._extruder_pos_update_timer = self.reactor.register_timer(
                self._extruder_pos_update_event)
    def _handle_printing(self, print_time):
        self.reactor.update_timer(self._extruder_pos_update_timer,
                self.reactor.NOW)
    def _handle_not_printing(self, print_time):
        self.reactor.update_timer(self._extruder_pos_update_timer,
                self.reactor.NEVER)
    def _get_extruder_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        print_time = self.estimated_print_time(eventtime)
        return self.extruder.find_past_position(print_time)
    def _extruder_pos_update_event(self, eventtime):
        extruder_pos = self._get_extruder_pos(eventtime)
        # Check for filament runout
        self.runout_helper.note_filament_present(
                extruder_pos < self.filament_runout_pos)
        return eventtime + CHECK_RUNOUT_TIMEOUT
    def encoder_event(self, eventtime, state):
        self.filament_len += 1.9468 #flsun test
        if self.extruder is not None:
            self._update_filament_runout_pos(eventtime)
            # Check for filament insertion
            # Filament is always assumed to be present on an encoder event
            self.runout_helper.note_filament_present(True)
    def recover_motion_check(self,eventtime):
        if not self.get_status(eventtime)['enabled']:
            return
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        # 更新一次位置参数，激活检测
        if self.extruder is not None:
            self._update_filament_runout_pos(eventtime)
            self.runout_helper.note_filament_present(True)
            
    def cmd_SET_FILAMENT_NUM_ZERO(self, eventtime=None):
        self.filament_len = 0
    def cmd_GET_FILAMENT_NUM(self, eventtime=None):
        mea_flow = self.filament_len/150
        if mea_flow > 0.4 and mea_flow < 2.4:
            gcode = self.printer.lookup_object('gcode') #flsun add
            gcode.run_script_from_command('M117 flow rate is %f%%' % (mea_flow*100))
        return self.filament_len
def load_config_prefix(config):
    return EncoderSensor(config)
