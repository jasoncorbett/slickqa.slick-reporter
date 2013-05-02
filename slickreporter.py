#!/usr/bin/env python3.3
"""
Slick Reporter is a simple script that can run a command, examine it's output, and report results
to slick based on output.  It uses regular expressions configured in a configuration file to define
how and what it pays attention to.
"""
__author__ = 'jcorbett'

import sys
import configparser
import slickqa
import logging
import logging.handlers
import argparse

################################################################################
# Logging
################################################################################
class StrFormatLogRecord(logging.LogRecord):
    """
    Even though you can select '{' as the style for the formatter class, you still can't use
    {} formatting for your message.  This is stupid, so this class will fix it.
    """

    def getMessage(self):
        msg = str(self.msg)
        if self.args:
            # potential bug here, if they provide a 0th argument, but don't use it in the message.
            # the old formatting would have thrown an exception in that case, and it still will.
            if '{}' in msg or '{0}' in msg:
                msg = msg.format(*self.args)
            else:
                msg = msg % self.args
        return msg

# Allow {} style formatting of log messages, which is far superior!
logging.setLogRecordFactory(StrFormatLogRecord)

def initialize_logging(configuration):
    assert(isinstance(configuration, configparser.ConfigParser))
    logfile = configuration['Logging'].get('logfile')
    level = configuration['Logging'].get('level', 'DEBUG')
    stdout = configuration['Logging'].getboolean('stdout', True)
    format = configuration['Logging'].get('format', '[{asctime}|{levelname:<8}|{name}]: {message}')
    dateformat = configuration['Logging'].get('dateformat', '%x %I:%M:%S %p')
    handlers = []
    formatter = logging.Formatter(fmt=format, datefmt=dateformat, style='{')
    root_logger = logging.getLogger()
    exc_info = None
    try:
        if logfile is not None and logfile != '':
            handlers.append(logging.handlers.WatchedFileHandler(logfile))
            if stdout:
                handlers.append(logging.StreamHandler())
        else:
            # if there is no logfile, you have to have stdout logging
            handlers.append(logging.StreamHandler())
    except PermissionError:
        handlers = [logging.StreamHandler(),]
        exc_info = sys.exc_info()
    root_logger.setLevel(level)
    if root_logger.hasHandlers():
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)
    for handler in handlers:
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    if exc_info is not None:
        logger = logging.getLogger("slick-reporter.initialize_logging")
        logger.warning("Unable to write to log file {}: ", logfile, exc_info=exc_info)

################################################################################
# Configuration
################################################################################

basic_configuration = """
[Slick]
url = http://localhost:8080/slickij

[Logging]
logfile = slick-reporter.log
level = INFO
stdout = True
format = [{asctime}|{levelname:<8}|{name}]: {message}
dateformat = %x %I:%M:%S %p
"""

def load_configuration(filepath):
    logger = logging.getLogger("slick-reporter.load_configuration")
    logger.debug("Loading configuration from file path {}", filepath)
    config = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
    config.read_string(basic_configuration)
    files_read = config.read([filepath,])
    if len(files_read) is 0:
        logger.debug("Unable to read config file '{}', using defaults.", filepath)
    return config

def save_configuration(filepath, config):
    assert(isinstance(config, configparser.ConfigParser))
    logger = logging.getLogger("slick-reporter.save_configuration")
    logger.debug("Attempting to save configuration file {}.", filepath)
    try:
        with open(filepath, 'w') as configfile:
            config.write(configfile)
    except:
        logger.warn("Unable to write configuration file '{}': ", filepath, exc_info=sys.exc_info())
        raise


################################################################################
# Main
################################################################################
def setup(options):
    """This method gets everything ready.  It loads configuration, initializes logging, initializes the connection
    to slick.
    """
    config = load_configuration(options.configpath)
    if options.stdout:
        config['Logging']['stdout'] = 'True'
    if hasattr(options, 'nologfile') and options.nologfile:
        config['Logging']['logfile'] = ''
    if hasattr(options, 'loglevel') and options.loglevel is not None and options.loglevel != '':
        config['Logging']['level'] = options.loglevel
    if hasattr(options, 'slickurl') and options.slickurl is not None and options.slickurl != '':
        config['Slick']['url'] = options.slickurl
    if hasattr(options, 'logfile') and options.logfile is not None and options.logfile != '':
        config['Logging']['logfile'] = options.logfile
    initialize_logging(config)
    logger = logging.getLogger("slick-reporter.setup")
    logger.info("slick-reporter is initializing.")
    logger.debug("Configuring a slick connection at url '{}'.", config['Slick']['url'])
    slick = slickqa.SlickConnection(config['Slick']['url'])
    validate_slick_connection(slick)

    return (config, slick)

def validate_slick_connection(slick):
    assert(isinstance(slick, slickqa.SlickConnection))
    logger = logging.getLogger("slick-reporter.validate_slick_connection")
    logger.debug("Attempting to connect to slick at url {}", slick.baseUrl)
    versioninfo = slick.version.findOne()
    logger.info("Connected to {} version {} at url {}", versioninfo.productName, versioninfo.versionString, slick.baseUrl)

def main(args=sys.argv[1:]):
    """
    The main method of the script, it does everything.

    :type args: list
    :param args: A list of strings that are the command line arguments to the script.  The default is sys.argv[1:]
    """
    parser = argparse.ArgumentParser(description="slick-reporter is used to take the output from a command line script and turn it into slick results.")
    parser.add_argument('-c', '--config', action='store', default='slick-reporter.conf', dest='configpath', metavar='CONFIGPATH', help="specify the config file slick-reporter uses, default=slick-reporter.conf")
    parser.add_argument('-n', '--nologfile', action='store_true', default=False, dest='nologfile', help="don't send logs to the slick-reporter.log log file")
    parser.add_argument('--loglevel', action='store', default='INFO', dest='loglevel', help="Change the default log level from INFO to another level (like DEBUG, WARNING, ERROR, or CRITICAL)")
    parser.add_argument('-q', '--quiet', action='store_false', default=True, dest='stdout', help="don't log to stdout")
    parser.add_argument('--slick', action='store', dest='slickurl', metavar='SLICKBASEURL', help='Use the specified url for connecting to slick')
    parser.add_argument('--configure', action='store_true', dest='configure', default=False, help="Configure slick-reporter")

    options = parser.parse_args(args)

    (config, slick) = setup(options)
    if options.configure:
        save_configuration(options.configpath, config)
        print("Configuration saved to {}.".format(options.configpath))


if __name__ == '__main__':
    main()
