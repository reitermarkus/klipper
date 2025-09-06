# Printer cooling fan
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from . import pulse_counter

FAN_MIN_TIME = 0.100

class Fan:
    def __init__(self, config, default_shutdown_speed=0.):
        self.printer = config.get_printer()
        self.last_fan_value = 0.
        self.last_fan_time = 0.
        # Read config
        self.max_power = config.getfloat('max_power', 1., above=0., maxval=1.)
        self.max_power_backup = self.max_power
        self.max_power_factor = -1 # -1 means not using, 0-100 means has be set
        self.kick_start_time = config.getfloat('kick_start_time', 0.1,
                                               minval=0.)
        self.off_below = config.getfloat('off_below', default=0.,
                                         minval=0., maxval=1.)
        cycle_time = config.getfloat('cycle_time', 0.010, above=0.)
        hardware_pwm = config.getboolean('hardware_pwm', False)
        shutdown_speed = config.getfloat(
            'shutdown_speed', default_shutdown_speed, minval=0., maxval=1.)
        # Setup pwm object
        ppins = self.printer.lookup_object('pins')
        self.mcu_fan = ppins.setup_pin('pwm', config.get('pin'))
        self.mcu_fan._invert = config.getboolean('invert', False) #dingzeqi
        self.mcu_fan.setup_max_duration(0.)
        self.mcu_fan.setup_cycle_time(cycle_time, hardware_pwm)
        shutdown_power = max(0., min(self.max_power, shutdown_speed))
        self.mcu_fan.setup_start_value(0., shutdown_power)

        # Setup tachometer
        self.tachometer = FanTachometer(config)

        # Register callbacks
        self.printer.register_event_handler("gcode:request_restart",
                                            self._handle_request_restart)

    def get_mcu(self):
        return self.mcu_fan.get_mcu()
    def set_speed(self, print_time, value):
        if value < self.off_below:
            value = 0.
        value = max(0., min(self.max_power, value * self.max_power))
        if value == self.last_fan_value:
            return
        print_time = max(self.last_fan_time + FAN_MIN_TIME, print_time)
        if (value and value < self.max_power and self.kick_start_time
            and (not self.last_fan_value or value - self.last_fan_value > .5)):
            # Run fan at full speed for specified kick_start_time
            self.mcu_fan.set_pwm(print_time, self.max_power)
            print_time += self.kick_start_time
        self.mcu_fan.set_pwm(print_time, value)
        self.last_fan_time = print_time
        self.last_fan_value = value
    def set_speed_from_command(self, value):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.register_lookahead_callback((lambda pt:
                                              self.set_speed(pt, value)))
    def _handle_request_restart(self, print_time):
        self.set_speed(print_time, 0.)

    def get_status(self, eventtime):
        tachometer_status = self.tachometer.get_status(eventtime)
        return {
            'speed': self.last_fan_value,
            'rpm': tachometer_status['rpm'],
            'max_power': self.max_power_backup,
            'max_power_factor':self.max_power_factor
        }

class FanTachometer:
    def __init__(self, config):
        printer = config.get_printer()
        self._freq_counter = None

        pin = config.get('tachometer_pin', None)
        if pin is not None:
            self.ppr = config.getint('tachometer_ppr', 2, minval=1)
            poll_time = config.getfloat('tachometer_poll_interval',
                                        0.0015, above=0.)
            sample_time = 1.
            self._freq_counter = pulse_counter.FrequencyCounter(
                printer, pin, sample_time, poll_time)

    def get_status(self, eventtime):
        if self._freq_counter is not None:
            rpm = self._freq_counter.get_frequency() * 30. / self.ppr
        else:
            rpm = None
        return {'rpm': rpm}

class PrinterFan:
    def __init__(self, config):
        self.fan = Fan(config)
        # Register commands
        self.gcode = config.get_printer().lookup_object('gcode')
        self.gcode.register_command("M106", self.cmd_M106)
        self.gcode.register_command("M107", self.cmd_M107)
        #dingzeqi
        self.gcode.register_command("M116", self.cmd_M116)
        self.gcode.register_command("F105", self.cmd_F105)
    def get_status(self, eventtime):
        return self.fan.get_status(eventtime)
    def cmd_M106(self, gcmd):
        # Set fan speed
        value = gcmd.get_float('S', 255., minval=0.) / 255.
        self.fan.set_speed_from_command(value)
    def cmd_M107(self, gcmd):
        # Turn fan off
        self.fan.set_speed_from_command(0.)
    #dingzeqi
    def cmd_M116(self, gcmd):
        get_factor = gcmd.get_float('S', 100., minval=0.)
        speed_factor = get_factor * self.fan.max_power_backup / 100
        if speed_factor < 0 or speed_factor > self.fan.max_power_backup:
            return
        else:
            eventtime = self.fan.printer.get_reactor().monotonic()
            # get curret speed_power, and Calculate the ratio of the current value to the maximum value
            temp_eventtime_speed = self.get_status(eventtime)['speed']
            if self.fan.max_power == 0 or \
                (temp_eventtime_speed == 0. and \
                 self.fan.max_power == self.fan.max_power_backup):
                speed_value = self.fan.max_power_backup
                speed_ratio = 1.0
            else:
                speed_ratio = temp_eventtime_speed  / self.fan.max_power
            self.fan.max_power = speed_factor
            self.fan.max_power_factor = get_factor
            # reset the ratio by new max_power
            #self.fan.set_speed_from_command(speed_value)
            self.gcode.run_script_from_command("M106 S%f"%(speed_ratio*255))
    # set max_power,and no impact current speed power -- by lixm
    def cmd_F105(self, gcmd):
        get_factor = gcmd.get_float('S', 100., minval=-1.)
        if get_factor < 0.:
            return
        speed_factor = get_factor * self.fan.max_power_backup / 100
        if speed_factor < 0 or speed_factor > self.fan.max_power_backup:
            return
        else:
            self.fan.max_power = speed_factor
            self.fan.max_power_factor = get_factor

def load_config(config):
    return PrinterFan(config)
